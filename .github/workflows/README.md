# Nebos-ai/.github — org-wide reusable workflows

Reusable workflows every repo in the `Nebos-ai` org can call. This is the
GitHub-native org-profile repository (created by nebos-governance commit
`88225b5d` in PR #2222); its `.github/workflows/` directory is what
`uses: Nebos-ai/.github/.github/workflows/<name>.yml@<ref>` resolves to.

## Workflows

### `repo-governance-floor.yml` (`v1`)

Enforces the Nebos Constitution Article XV governance floor on any repo that
calls it. Six checks (BASELINE.md exists, AGENTS.md size, AGENTS.md baseline
back-ref, AGENTS.md rule-drift, matrix_triple enum, commit signer allowlist)
run against the consumer repo; a compliance score (0-100) is emitted and the
job fails closed at < 100.

**Consumer contract** — minimum wiring:

```yaml
name: governance
on:
  pull_request:
  push:
    branches: [main]

permissions:
  contents: read

jobs:
  floor:
    uses: Nebos-ai/.github/.github/workflows/repo-governance-floor.yml@v1
    with:
      sub_category: service  # or surface | fullstack | infra | worker | asset | docs
      # matrix_triple: '{"role":"service","language":"python","runtime":"server"}'
      # allowed_signers_path: .git-signers/allowed_signers  # default
      # governance_ref: main  # or a SHA/tag pin
```

**Required inputs:**

| Input | Type | Description |
|---|---|---|
| `sub_category` | string | Repo sub_category per Article XV taxonomy (`service`, `surface`, `fullstack`, `infra`, `worker`, `asset`, `docs`). |

**Optional inputs:**

| Input | Default | Description |
|---|---|---|
| `matrix_triple` | `""` | JSON `{role, language, runtime}`. Populate only when triple recurs across ≥2 org repos (W1 v5 AD-1 F1). |
| `allowed_signers_path` | `.git-signers/allowed_signers` | Consumer-repo path to the SSH signer allowlist. `""` skips signer verification (bootstrap only). |
| `governance_ref` | `main` | Ref of `nebos-governance` to check out for check scripts. Pin to a SHA/tag for reproducibility windows. |

**Outputs:**

| Output | Description |
|---|---|
| `compliance_score` | 0-100 numeric score; feeds `repo_inventory.json.compliance_score` per Article XV para 2. |

**Contexts (for branch-protection wiring)** — the check that appears in
GitHub's branch-protection UI is:

```
Nebos-ai governance floor
```

Add this exact string to the "Require status checks" list when configuring
`main`-branch protection per RSSB-22 (W3b). The floor job's `name:` is the
authoritative source; changes to that name require a coordinated migration
across all consumer branch-protection configs.

**Permissions** — the workflow itself requires only `contents: read`. The
consumer is responsible for wiring any additional permissions its own steps
need. `pull-requests: write` is NOT required by this workflow.

**Concurrency** — `group: repo-governance-floor-${{ github.ref }}`,
`cancel-in-progress: false`. Sequential per-ref execution prevents concurrent
signer-check races on shared refs (per OR-H2).

## Version pinning

The `v1` tag on this repo is the stable contract. Downstream repos should
pin to `@v1` (not `@main`) so a governance-floor breaking change doesn't
silently propagate. When `v2` becomes necessary, downstream repos migrate
individually via a coordinated PR wave.

## Provenance

- Authored by: RSSB-18 (W0) execution 2026-08-22 as part of the
  Repo Structure Standard v5 project (Nebos Projects
  `5051d1ae-6dd4-44df-ba7a-df59d763ea05`).
- Landing site: created by nebos-governance commit `88225b5d` (PR #2222,
  ratified 2026-08-22).
- Depends on: `nebos-governance/scripts/repo_structure_v5/` canonical home
  (RSSB-14 landed via PR #2229) + Constitution Article XV (ratified via
  PR #2222 merge commit `4481d587`).
