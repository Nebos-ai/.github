# aws-runner-guard

Keeps secrets off CI jobs that can run on shared AWS runners. Tracker task AUT-113 (parent AUT-104);
an exit criterion of governance ADR #2997 (AD-6, AD-13 item 2). This directory is public: it holds
no secrets and no repo-specific data. Each repo keeps its own list in
`.github/aws-runner-secrets-allowlist.yml`.

## Use it

A small workflow in each repo, pinned by commit SHA of this repo (it is a composite action, not a
reusable workflow, because a reusable workflow cannot name the SHA it was pinned at, and the script
has to come from that same SHA):

```yaml
name: aws-runner-guard
on:
  pull_request:
  merge_group:
  workflow_dispatch:
permissions:
  contents: read
jobs:
  aws-runner-guard:
    runs-on: ubuntu-latest   # hosted on purpose: the guard must never be variable-routed
    timeout-minutes: 5
    steps:
      - uses: actions/checkout@v7
        with:
          persist-credentials: false
      - uses: Nebos-ai/.github/.github/actions/aws-runner-guard@<commit sha>
        with:
          runner-pr: ${{ vars.NEBBOS_CI_RUNNER_PR }}
          runner-queue: ${{ vars.NEBBOS_CI_RUNNER_QUEUE }}
```

## The rules

1. **Blocking.** A job is variable-routed when its runner can come from a `NEBBOS_CI_RUNNER_*` org
   variable: `runs-on` reads it, or reads a matrix value, a reusable-workflow input or a needed job's
   output that does, or the job is a reusable-workflow call that passes it. Every workflow file is
   read with a YAML parser (comments are not references). A variable-routed job must not reference
   any `secrets.X` that is not allowed. Counted: `secrets.X` and `secrets['X']` anywhere in the job
   (`env`, `with`, `run`, `if`, `services`, `container`), workflow-level `env:` (it reaches every job
   in the file), `secrets: inherit`, a bare `secrets` context such as `toJSON(secrets)`, and a job
   `environment:` (it carries environment secrets and approvals; allow it as `environment:<name>`).
   `GITHUB_TOKEN` is always allowed.
2. **Pending fixes.** Jobs that exist today with an offending secret go in `pending_fixes`, each
   naming the secrets and the task that removes them. The check reports them as warnings and passes,
   but only for the listed secrets: a new secret on the same job fails. Two ways a pending job fails:
   - it is refused as a flip target: if `vars.NEBBOS_CI_RUNNER_PR` or `vars.NEBBOS_CI_RUNNER_QUEUE`
     holds anything other than a GitHub-hosted label (`ubuntu-*`, `windows-*`, `macos-*`, as a JSON
     string or list), every variable-routed job still in `pending_fixes` fails, so the next PR in
     that repo goes red until the task lands. Fix the job, delete its entry, then flip.
   - an entry whose job or secret no longer exists is only a warning ("remove it"), so the fix PR
     can drop it in the same change.
3. **Literal AWS labels (AUT-120).** A job whose `runs-on` names a literal `nebbos-ci-*` label
   (a string, a list, `labels:` of a mapping, or text inside a `${{ }}` expression; case-insensitive)
   is treated as variable-routed: rules 1 and 2 apply to it, so the secret allowlist and the flip
   check cover a job that hard-codes the Spot or On-Demand label.
4. **Trusted pool (AUT-120, ADR AD-7a).** The `nebbos-ci-ondemand` pool may write the `trusted/` cache
   prefix, so it is only for trusted runs. The guard fails any job that names it unless every event in
   the workflow's `on:` is trusted. Trusted events: `merge_group`, and `push` limited to
   `branches: [main]` (a branch list of exactly `main`; no `tags`, no `branches-ignore`, no other branch,
   no pattern). Every other event is untrusted, including `pull_request`, `pull_request_target`,
   `workflow_dispatch`, `schedule`, `workflow_call` (the caller's event decides, and the guard does not
   follow callers), `issue_comment`, a `push` with no branch filter, and a workflow with no `on:` at
   all. A job-level `if:` is not honoured: limit the events, not the job. The sanctioned way to reach
   the pool stays the variable, `runs-on: ${{ fromJSON(vars.NEBBOS_CI_RUNNER_QUEUE) }}` style routing,
   which has no literal label. A literal `nebbos-ci-ondemand` inside a `${{ }}` expression is an error
   even on `merge_group`: the message tells the author to use the variable routing. Label matching is
   by prefix `nebbos-ci-ondemand` (so a future `nebbos-ci-ondemand-large` is covered).
5. **Warn-first.** `heavy_jobs` lists the heavy jobs (the set in research record 17ed3f20). One that
   still hard-codes `runs-on: ubuntu-*` raises a warning, never a failure.

## Allowlist file

```yaml
allowed:            # empty at the start except GITHUB_TOKEN, which is implicit
  SOME_SECRET: one-line reason it is safe on a shared runner
pending_fixes:
  - workflow: .github/workflows/staging.yml
    job: check
    secrets: [NEBOS_PROJECTS_SERVICE_KEY, SLACK_OPS_WEBHOOK]
    task: AUT-111
heavy_jobs:
  - workflow: .github/workflows/staging.yml
    job: check
```

A missing or invalid file, or a workflow that does not parse, fails the check with exit status 2.

## Output

Annotations with file and line, a table in the job summary, and one line:
`AWS_RUNNER_GUARD workflows=<n> jobs=<n> variable_routed=<n> errors=<n> warnings=<n>`. Names only.

## Tests

`test_aws_runner_guard.py` runs in `.github/workflows/aws-runner-guard-tests.yml`.
