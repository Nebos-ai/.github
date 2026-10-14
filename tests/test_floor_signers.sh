#!/usr/bin/env bash
# Scripted test of the repo-governance-floor signer step (ADR-PROV
# floor-signer-trust-source-org-list). Extracts the step's shell between the
# `# >>> signer-logic` / `# <<< signer-logic` markers and runs it against throwaway
# git repos signed with throwaway ssh keys. No network: curl is shimmed to fail, which
# the step treats as "web-flow key unavailable" (a warning).
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

# run_case <label> <expected passed=> <expected trust_source or -> <ORG_OUTCOME> <ORG_FILE>
run_case() {
  local label=$1 want=$2 want_src=$3 outcome=$4 orgfile=$5
  : > "$T/out"
  local head
  head=$(git -C "$REPO" rev-parse HEAD)
  local rc=0
  ( cd "$REPO" && PATH="$T/shim:$PATH" SIGNERS_PATH=.git-signers/allowed_signers BASE_SHA="$BASE" HEAD_SHA="$head" \
      ORG_OUTCOME="$outcome" ORG_FILE="$orgfile" RUNNER_TEMP="$T/tmp" GITHUB_OUTPUT="$T/out" \
      bash -euo pipefail "$T/logic.sh" > "$T/log" 2>&1 ) || rc=$?
  local got src
  got=$(sed -n 's/^passed=//p' "$T/out" | tail -1)
  src=$(sed -n 's/^trust_source=//p' "$T/out" | tail -1)
  if [[ "$got" == "$want" && ( "$want_src" == "-" || "$src" == "$want_src" ) ]]; then
    echo "ok   - $label (passed=$got trust_source=${src:-none})"
    PASS=$((PASS + 1))
  else
    echo "FAIL - $label: want passed=$want trust_source=$want_src, got passed=${got:-none} trust_source=${src:-none} rc=$rc"
    sed 's/^/       | /' "$T/log"
    FAILN=$((FAILN + 1))
  fi
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
