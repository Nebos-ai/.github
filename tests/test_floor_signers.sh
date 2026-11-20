#!/usr/bin/env bash
# Scripted test of the repo-governance-floor signer step (ADR-PROV
# floor-signer-trust-source-org-list). Extracts the step's shell between the
# `# >>> signer-logic` / `# <<< signer-logic` markers and runs it against throwaway
# git repos signed with throwaway ssh keys. No network: curl is shimmed to fail, which
# the step treats as "web-flow key unavailable" (a warning).
# Cases map to ADR-PROV test requirements 1-7 (review R2) and the cert-authority,
# revocation and require_org_list rules (AD-1, AD-2, AD-6).
set -euo pipefail

ROOT=$(cd "$(dirname "$0")/.." && pwd)
WF="$ROOT/.github/workflows/repo-governance-floor.yml"
T=$(mktemp -d)
trap 'rm -rf "$T"' EXIT

sed -n '/# >>> signer-logic/,/# <<< signer-logic/p' "$WF" | sed -e '1d;$d' | sed 's/^          //' > "$T/logic.sh"
[[ -s "$T/logic.sh" ]] || { echo "FAIL: could not extract signer logic from $WF"; exit 1; }

mkdir -p "$T/shim" "$T/tmp"
printf '#!/bin/sh\nexit 22\n' > "$T/shim/curl"
chmod +x "$T/shim/curl"

for k in org base other; do ssh-keygen -q -t ed25519 -N '' -C "$k" -f "$T/k_$k"; done
line() { echo "$1@example.test $(cut -d' ' -f1,2 "$T/k_$2.pub")"; }

PASS=0
FAILN=0
N=0

# new_repo <name>: repo whose BASE lists only the base key. Sets REPO, BASE.
new_repo() {
  REPO="$T/repo_$1"
  mkdir -p "$REPO/.git-signers"
  git -C "$REPO" init -q -b main
  git -C "$REPO" config core.hooksPath /dev/null # throwaway repo: ignore any global hooks
  git -C "$REPO" config user.email t@example.test
  git -C "$REPO" config user.name t
  git -C "$REPO" config gpg.format ssh
  git -C "$REPO" config commit.gpgsign false
  line base base > "$REPO/.git-signers/allowed_signers"
  git -C "$REPO" add -A
  git -C "$REPO" commit -q -m base
  BASE=$(git -C "$REPO" rev-parse HEAD)
}

# signed_commit <key>: an empty commit signed with that key.
signed_commit() {
  N=$((N + 1))
  git -C "$REPO" -c user.signingkey="$T/k_$1" commit -q -S --allow-empty -m "c$N"
}

# Optional per-case knobs, reset after every case: REQ (require_org_list), REVF (revoked
# fingerprints file), PRCTX=0 (no pull_request context: empty BASE_SHA/HEAD_SHA),
# WANT_LOG (extended regex the step log must match).
REQ=false
REVF=""
PRCTX=1
WANT_LOG=""

# run_case <label> <expected passed=> <expected trust_source or -> <ORG_OUTCOME> <ORG_FILE>
run_case() {
  local label=$1 want=$2 want_src=$3 outcome=$4 orgfile=$5
  : > "$T/out"
  local head
  head=$(git -C "$REPO" rev-parse HEAD)
  local rc=0 b="$BASE" h="$head"
  if [[ "$PRCTX" == 0 ]]; then b=""; h=""; fi
  ( cd "$REPO" && PATH="$T/shim:$PATH" SIGNERS_PATH=.git-signers/allowed_signers BASE_SHA="$b" HEAD_SHA="$h" \
      ORG_OUTCOME="$outcome" ORG_FILE="$orgfile" REVOKED_FILE="$REVF" REQUIRE_ORG="$REQ" \
      RUNNER_TEMP="$T/tmp" GITHUB_OUTPUT="$T/out" \
      bash -euo pipefail "$T/logic.sh" > "$T/log" 2>&1 ) || rc=$?
  local got src
  got=$(sed -n 's/^passed=//p' "$T/out" | tail -1)
  src=$(sed -n 's/^trust_source=//p' "$T/out" | tail -1)
  local logok=1
  if [[ -n "$WANT_LOG" ]] && ! grep -qE "$WANT_LOG" "$T/log"; then logok=0; fi
  REQ=false; REVF=""; PRCTX=1
  if [[ "$got" == "$want" && "$logok" == 1 && ( "$want_src" == "-" || "$src" == "$want_src" ) ]]; then
    echo "ok   - $label (passed=$got trust_source=${src:-none})"
    PASS=$((PASS + 1))
  else
    echo "FAIL - $label: want passed=$want trust_source=$want_src log~/${WANT_LOG}/, got passed=${got:-none} trust_source=${src:-none} rc=$rc logmatch=$logok"
    sed 's/^/       | /' "$T/log"
    FAILN=$((FAILN + 1))
  fi
  WANT_LOG=""
}

line org org > "$T/org_list" # org list: only the org key
printf '# comments only\n\n' > "$T/org_empty"

new_repo 1
signed_commit org
run_case "key only in the org list passes" true org+base success "$T/org_list"

new_repo 2
signed_commit base
run_case "key only in the base list still passes (union)" true org+base success "$T/org_list"

new_repo 3
signed_commit other
run_case "key in neither list fails" false org+base success "$T/org_list"

new_repo 4
line attacker other >> "$REPO/.git-signers/allowed_signers"
git -C "$REPO" add -A
N=$((N + 1))
git -C "$REPO" -c user.signingkey="$T/k_other" commit -q -S -m "enroll own key $N"
run_case "PR that adds its own key to the repo file still fails" false org+base success "$T/org_list"

new_repo 5
signed_commit org
run_case "org list unavailable: org-only key fails (fall back to base list)" false base-only failure "$T/does-not-exist"

new_repo 6
signed_commit base
run_case "org list unavailable: base key passes (fall back to base list)" true base-only failure "$T/does-not-exist"

new_repo 7
signed_commit org
run_case "org list present but has no key line: treated as unavailable" false base-only success "$T/org_empty"

new_repo 8
signed_commit base
signed_commit other
run_case "range with one good and one unlisted commit fails" false org+base success "$T/org_list"

new_repo 9
git -C "$REPO" rm -q -r .git-signers
git -C "$REPO" commit -q -m "drop signers"
BASE=$(git -C "$REPO" rev-parse HEAD)
signed_commit other
run_case "base without allowed_signers keeps the bootstrap skip" skipped - success "$T/org_list"

# --- ADR test requirement 1: valid-before is not revocation (review F2/R6) -------------
# git verifies a signature at the commit's own timestamp, so a commit back-dated to before
# `valid-before` still passes while a current-dated one fails. Pins the behaviour the ADR
# relies on when it says valid-before is documentation only.
new_repo 10
printf '%s\n' "other@example.test valid-before=\"20260101\" $(cut -d' ' -f1,2 "$T/k_other.pub")" >> "$REPO/.git-signers/allowed_signers"
git -C "$REPO" add -A
git -C "$REPO" commit -q -m "list other with valid-before"
BASE=$(git -C "$REPO" rev-parse HEAD)
N=$((N + 1))
GIT_AUTHOR_DATE="2025-06-01T12:00:00Z" GIT_COMMITTER_DATE="2025-06-01T12:00:00Z" \
  git -C "$REPO" -c user.signingkey="$T/k_other" commit -q -S --allow-empty -m "backdated $N"
run_case "valid-before: back-dated commit still passes (so it is not a revocation)" true org+base success "$T/org_list"
signed_commit other
run_case "valid-before: current-dated commit by the expired key fails" false org+base success "$T/org_list"

# --- test 2: revoked fingerprint in the org list --------------------------------------
org_fpr=$(ssh-keygen -lf "$T/k_org.pub" | awk '{print $2}')
printf '# revoked\n%s\n' "$org_fpr" > "$T/revoked"
new_repo 11
signed_commit org
REVF="$T/revoked" WANT_LOG="revoked key" run_case "key in org list and revoked_fingerprints fails" false org+base success "$T/org_list"
new_repo 12
signed_commit base
REVF="$T/revoked" run_case "revoked org key does not affect a different listed key" true org+base success "$T/org_list"

# --- test 3: cert-authority without principals= and namespaces="git" ------------------
ca_key=$(cut -d' ' -f1,2 "$T/k_other.pub")
printf '%s\n' "*@example.test cert-authority $ca_key" > "$T/org_ca_unscoped"
cat "$T/org_list" "$T/org_ca_unscoped" > "$T/org_with_ca"
new_repo 13
signed_commit base
WANT_LOG="org signer list line [0-9]+: cert-authority" run_case "unscoped cert-authority line in the org list fails, naming the line" false org+base success "$T/org_with_ca"
new_repo 14
printf '%s\n' "*@example.test cert-authority,namespaces=\"git\" $ca_key" >> "$REPO/.git-signers/allowed_signers"
git -C "$REPO" add -A
git -C "$REPO" commit -q -m "ca without principals"
BASE=$(git -C "$REPO" rev-parse HEAD)
signed_commit base
WANT_LOG="base signer list line [0-9]+: cert-authority" run_case "cert-authority without principals= in the base list fails" false org+base success "$T/org_list"
new_repo 15
printf '%s\n' "*@example.test cert-authority,principals=\"repo:x\",namespaces=\"git\" $ca_key" >> "$REPO/.git-signers/allowed_signers"
git -C "$REPO" add -A
git -C "$REPO" commit -q -m "scoped ca"
BASE=$(git -C "$REPO" rev-parse HEAD)
signed_commit base
run_case "a scoped cert-authority line is not rejected by the guard" true org+base success "$T/org_list"

# --- test 4: no trailing newline on either list ---------------------------------------
printf '%s' "$(line org org)" > "$T/org_nonl"
new_repo 16
printf '%s' "$(line base base)" > "$REPO/.git-signers/allowed_signers"
git -C "$REPO" add -A
git -C "$REPO" commit -q -m "no trailing newline"
BASE=$(git -C "$REPO" rev-parse HEAD)
signed_commit org
run_case "no trailing newline: org-only key still parsed" true org+base success "$T/org_nonl"
signed_commit base
run_case "no trailing newline: base-only key still parsed" true org+base success "$T/org_nonl"

# --- test 5: empty BASE_SHA (new branch, first push) -------------------------------------
new_repo 17
signed_commit other
PRCTX=0 run_case "empty BASE_SHA: skipped without error, trust_source still reported" "n/a" org+base success "$T/org_list"

# --- test 6: require_org_list ------------------------------------------------------------
new_repo 18
signed_commit base
REQ=true WANT_LOG="floor-org-signers-required" run_case "require_org_list=true, org list unavailable: red" false base-only failure "$T/does-not-exist"
new_repo 19
signed_commit base
REQ=false WANT_LOG="floor-org-signers-unavailable" run_case "require_org_list=false, org list unavailable: green plus warning" true base-only failure "$T/does-not-exist"
new_repo 20
signed_commit base
REQ=true run_case "require_org_list=true, org list readable: green" true org+base success "$T/org_list"
new_repo 21
signed_commit base
PRCTX=0 REQ=true run_case "require_org_list=true outside a PR (canary), org list unavailable: red" false base-only failure "$T/does-not-exist"

# --- test 7: end to end against a fixture governance repo with a real sparse checkout ------
GOV="$T/gov"
mkdir -p "$GOV/.git-signers"
git -C "$GOV" init -q -b main
git -C "$GOV" config core.hooksPath /dev/null
git -C "$GOV" config user.email t@example.test
git -C "$GOV" config user.name t
git -C "$GOV" config commit.gpgsign false
line org org > "$GOV/.git-signers/allowed_signers"
printf '# none revoked\n' > "$GOV/.git-signers/revoked_fingerprints"
echo unrelated > "$GOV/README.md"
git -C "$GOV" add -A
git -C "$GOV" commit -q -m gov
GOV_SHA=$(git -C "$GOV" rev-parse HEAD)
git clone -q --depth 1 --no-checkout "file://$GOV" "$T/orgco"
git -C "$T/orgco" sparse-checkout set --no-cone '/.git-signers/allowed_signers' '/.git-signers/revoked_fingerprints'
git -C "$T/orgco" checkout -q main
if [[ -f "$T/orgco/.git-signers/allowed_signers" && ! -e "$T/orgco/README.md" ]]; then
  echo "ok   - sparse checkout fetched the two signer files only"
  PASS=$((PASS + 1))
else
  echo "FAIL - sparse checkout of the fixture governance repo is wrong"
  FAILN=$((FAILN + 1))
fi
new_repo 22
signed_commit org
REVF="$T/orgco/.git-signers/revoked_fingerprints" run_case "end to end: key only in the sparse-checked-out org list passes" true org+base success "$T/orgco/.git-signers/allowed_signers"
if [[ "$(sed -n 's/^org_list_sha=//p' "$T/out" | tail -1)" == "$GOV_SHA" ]]; then
  echo "ok   - org_list_sha output is the commit of the org list that was read"
  PASS=$((PASS + 1))
else
  echo "FAIL - org_list_sha output does not match the fixture governance commit"
  FAILN=$((FAILN + 1))
fi

# Static: the workflow sparse-checks-out both signer files.
if grep -q '\.git-signers/revoked_fingerprints' <<<"$(awk '/id: org_signers/{f=1} f&&/- name: Verify absorption/{exit} f' "$WF")"; then
  echo "ok   - org list checkout also fetches revoked_fingerprints"
  PASS=$((PASS + 1))
else
  echo "FAIL - org list checkout must sparse-checkout .git-signers/revoked_fingerprints"
  FAILN=$((FAILN + 1))
fi

# Static: the org list is fetched from the literal ref main, never from the governance_ref input.
BLOCK=$(awk '/id: org_signers/{f=1} f&&/- name: Verify absorption/{exit} f' "$WF")
if grep -q 'ref: main' <<<"$BLOCK" && ! grep -q 'governance_ref' <<<"$BLOCK"; then
  echo "ok   - org list checkout uses literal ref main, not governance_ref"
  PASS=$((PASS + 1))
else
  echo "FAIL - org list checkout must use 'ref: main' and must not reference governance_ref"
  FAILN=$((FAILN + 1))
fi

echo "passed=$PASS failed=$FAILN"
[[ $FAILN -eq 0 ]]
