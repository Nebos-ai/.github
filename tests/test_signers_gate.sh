#!/usr/bin/env bash
# Scripted test of .github/workflows/git-signers-gate.yml (ADR-PROV floor-signer-trust-source,
# AD-4(ii), review R3/R10). Extracts the gate shell between the `# >>> gate-logic` /
# `# <<< gate-logic` markers and runs it against throwaway git repos signed with throwaway
# ssh keys. No network.
set -euo pipefail

ROOT=$(cd "$(dirname "$0")/.." && pwd)
WF="$ROOT/.github/workflows/git-signers-gate.yml"
T=$(mktemp -d)
trap 'rm -rf "$T"' EXIT

sed -n '/# >>> gate-logic/,/# <<< gate-logic/p' "$WF" | sed -e '1d;$d' | sed 's/^          //' > "$T/logic.sh"
[[ -s "$T/logic.sh" ]] || { echo "FAIL: could not extract gate logic from $WF"; exit 1; }
mkdir -p "$T/tmp"

for k in base other; do ssh-keygen -q -t ed25519 -N '' -C "$k" -f "$T/k_$k"; done
line() { echo "$1@example.test $(cut -d' ' -f1,2 "$T/k_$2.pub")"; }

PASS=0
FAILN=0

# new_repo <name>: "main" lists only the base key; BASE = main tip; origin/main ref = main tip.
new_repo() {
  REPO="$T/repo_$1"
  mkdir -p "$REPO/.git-signers"
  git -C "$REPO" init -q -b main
  git -C "$REPO" config core.hooksPath /dev/null
  git -C "$REPO" config user.email t@example.test
  git -C "$REPO" config user.name t
  git -C "$REPO" config gpg.format ssh
  git -C "$REPO" config commit.gpgsign false
  line base base > "$REPO/.git-signers/allowed_signers"
  echo x > "$REPO/file.txt"
  git -C "$REPO" add -A
  git -C "$REPO" commit -q -m base
  BASE=$(git -C "$REPO" rev-parse HEAD)
  git -C "$REPO" update-ref refs/remotes/origin/main "$BASE"
}

# commit <key> <path> <content-line or ->: signed commit appending a line to a file.
commit() {
  local key=$1 path=$2 content=$3
  if [[ "$content" != "-" ]]; then
    mkdir -p "$(dirname "$REPO/$path")"
    printf '%s\n' "$content" >> "$REPO/$path"
    git -C "$REPO" add -A
  fi
  git -C "$REPO" -c user.signingkey="$T/k_$key" commit -q -S --allow-empty -m "c by $key"
}

# run_case <label> <expected passed=> [event]
run_case() {
  local label=$1 want=$2 event=${3:-pull_request}
  : > "$T/out"
  local head rc=0
  head=$(git -C "$REPO" rev-parse HEAD)
  ( cd "$REPO" && EVENT="$event" BASE_SHA="$BASE" HEAD_SHA="$head" MAIN_REF=origin/main \
      LIST_PATH=.git-signers/allowed_signers RUNNER_TEMP="$T/tmp" GITHUB_OUTPUT="$T/out" \
      bash -euo pipefail "$T/logic.sh" > "$T/log" 2>&1 ) || rc=$?
  local got
  got=$(sed -n 's/^passed=//p' "$T/out" | tail -1)
  if [[ "$got" == "$want" ]]; then
    echo "ok   - $label (passed=$got)"
    PASS=$((PASS + 1))
  else
    echo "FAIL - $label: want passed=$want, got ${got:-none} rc=$rc"
    sed 's/^/       | /' "$T/log"
    FAILN=$((FAILN + 1))
  fi
}

new_repo 1
commit other file.txt "unrelated change"
run_case "PR that does not touch .git-signers passes even if signed by an unlisted key" true

new_repo 2
commit base .git-signers/allowed_signers "$(line other other)"
run_case "signer change signed by a key in the base list passes" true

new_repo 3
commit other .git-signers/allowed_signers "$(line other other)"
run_case "PR that enrols the key that signs it fails" false

new_repo 4
commit base .git-signers/allowed_signers "$(line other other)"
# current main dropped the base key and holds only 'other'
printf '%s\n' "$(line other other)" > "$T/main_list"
BLOB=$(git -C "$REPO" hash-object -w "$T/main_list")
TREE=$(printf '100644 blob %s\tallowed_signers\n' "$BLOB" | git -C "$REPO" mktree)
ROOTTREE=$(printf '040000 tree %s\t.git-signers\n' "$TREE" | git -C "$REPO" mktree)
M=$(git -C "$REPO" commit-tree -m "main rotated" "$ROOTTREE" -p "$BASE")
git -C "$REPO" update-ref refs/remotes/origin/main "$M"
run_case "commit signed by a key removed from current main fails (both lists must pass)" false

new_repo 5
commit base .git-signers/allowed_signers "*@example.test cert-authority,principals=\"repo:x\",namespaces=\"git\" $(cut -d' ' -f1,2 "$T/k_other.pub")"
run_case "PR that adds a cert-authority line fails, even a scoped one" false

new_repo 6
commit other file.txt "unrelated"
run_case "merge_group event passes without verification" true merge_group

new_repo 7
commit base .git-signers/revoked_fingerprints "SHA256:abc"
run_case "a change to revoked_fingerprints signed by a listed key passes" true

new_repo 8
commit other .git-signers/revoked_fingerprints "SHA256:abc"
run_case "a change to revoked_fingerprints signed by an unlisted key fails" false

echo "passed=$PASS failed=$FAILN"
[[ $FAILN -eq 0 ]]
