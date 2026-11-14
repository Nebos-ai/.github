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
   prefix, so it is only for trusted runs. The guard fails any job that can reach it unless every event in
   the workflow's `on:` is trusted. Trusted events: `merge_group`, and `push` limited to
   `branches: [main]` (a branch list of exactly `main`; no `tags`, no `branches-ignore`, no other branch,
   no pattern). Every other event is untrusted, including `pull_request`, `pull_request_target`,
   `schedule`, `workflow_dispatch`, `workflow_call`, `issue_comment`, a `push` with no branch filter,
   and a workflow with no `on:` at all. A job-level `if:` is not honoured: limit the events, not the job.
   The sanctioned way to reach the pool is the variable, `runs-on: ${{ fromJSON(vars.NEBBOS_CI_RUNNER_QUEUE) }}`
   style routing, which has no literal label. A literal `nebbos-ci-ondemand` inside a `${{ }}` expression
   is an error even on `merge_group`; the message tells the author to use the variable routing. Labels
   match by prefix `nebbos-ci-ondemand`.
   A reusable workflow cannot hard-code `nebbos-ci-ondemand`: its own events are `workflow_call`, which
   is untrusted, so the guard fails it.
5. **Fail closed on runs-on expressions.** In a workflow with an untrusted event, a `runs-on` that
   contains `${{ }}` is accepted only when the guard can resolve it:
   - the sanctioned `vars.NEBBOS_CI_RUNNER_*` routing (plus `fromJSON`, `github.event_name`,
     `github.ref` and `github.ref_name`, which a PR author cannot set);
   - `matrix.X`, when every value of X in `strategy.matrix`, `include:` entries included, is a plain
     literal or the routing; each literal is judged by the rules above;
   - `inputs.X` in a `workflow_call` callee, when every local caller's `with:` value for X (or the
     input's default) is the routing or a plain literal. The caller's events decide the On-Demand rule,
     and the error is reported at the caller's `with:` line. A callee with no local caller has nothing
     to reach it that the guard can see, so it passes (a caller in another repo is judged by that repo's
     guard, which reads its `with:` directly);
   - a `needs.<job>.outputs.X` read of a job that itself reads the routing;
   - an expression made only of GitHub-hosted labels.
   Anything else (`env.X`, `format(...)`, a label built from pieces, `github.event.pull_request.title`,
   a matrix built from an unknown expression) is an error: "runs-on expression the guard cannot resolve;
   use a literal label, a matrix of literals or vars.NEBBOS_CI_RUNNER_* routing". The `with:` of a
   reusable-workflow call is also scanned: a literal `nebbos-ci-*` label there makes the call routed,
   and for a callee outside this repo the label is judged against the caller's events.
6. **Warn-first.** `heavy_jobs` lists the heavy jobs (the set in research record 17ed3f20). One that
   still hard-codes `runs-on: ubuntu-*` raises a warning, never a failure.

## What the guard can and cannot do

The guard is a merge-time check that runs inside the PR's own workflow. It blocks the merge, but it
cannot stop a PR job from running: a PR can change the workflow files and the job starts before anyone
merges. The runner-side fence (the On-Demand pool refusing jobs from refs other than `main` and
`gh-readonly-queue/*`) is tracked on AUT-120.

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
