"""Fixtures for aws_runner_guard.py (AUT-113). Needs PyYAML; no network, no secrets."""

import tempfile
import textwrap
import unittest
from pathlib import Path

import aws_runner_guard as g

ROUTED = "${{ fromJSON(github.event_name == 'merge_group' && (vars.NEBBOS_CI_RUNNER_QUEUE || '\"ubuntu-latest\"') || (vars.NEBBOS_CI_RUNNER_PR || '\"ubuntu-latest\"')) }}"


def run(workflows: dict[str, str], allowlist: str | None = "allowed: {}\n", pr: str = "", queue: str = ""):
    with tempfile.TemporaryDirectory() as d:
        root = Path(d)
        (root / ".github/workflows").mkdir(parents=True)
        for name, body in workflows.items():
            (root / ".github/workflows" / name).write_text(textwrap.dedent(body))
        if allowlist is not None:
            (root / g.DEFAULT_ALLOWLIST).write_text(textwrap.dedent(allowlist))
        allow = g.load_allowlist(root / g.DEFAULT_ALLOWLIST)
        wfs = g.load_workflows(root, ".github/workflows")
        g.classify(wfs)
        return g.evaluate(wfs, allow, {"NEBBOS_CI_RUNNER_PR": pr, "NEBBOS_CI_RUNNER_QUEUE": queue})


def levels(findings, level):
    return [f for f in findings if f.level == level]


def wf(job_body: str, header: str = "") -> str:
    return f"name: t\non: pull_request\n{header}jobs:\n  check:\n    runs-on: {ROUTED}\n{textwrap.indent(textwrap.dedent(job_body), '    ')}"


class Guard(unittest.TestCase):
    def test_clean_routed_job_passes(self):
        f = run({"a.yml": wf("steps:\n  - run: echo hi\n")})
        self.assertEqual(f, [])

    def test_github_token_is_implicitly_allowed(self):
        f = run({"a.yml": wf("steps:\n  - run: echo\n    env:\n      GH_TOKEN: ${{ secrets.GITHUB_TOKEN }}\n")})
        self.assertEqual(levels(f, "error"), [])

    def test_unlisted_secret_in_routed_job_fails(self):
        f = run({"a.yml": wf("steps:\n  - run: echo\n    env:\n      K: ${{ secrets.SOME_KEY }}\n")})
        errs = levels(f, "error")
        self.assertEqual(len(errs), 1)
        self.assertIn("SOME_KEY", errs[0].message)

    def test_allowlisted_secret_passes(self):
        f = run({"a.yml": wf("steps:\n  - run: echo\n    env:\n      K: ${{ secrets.SOME_KEY }}\n")}, "allowed:\n  some_key: read-only cache token\n")
        self.assertEqual(levels(f, "error"), [])

    def test_unrouted_job_with_secret_is_ignored(self):
        body = "name: t\non: push\njobs:\n  deploy:\n    runs-on: ubuntu-latest\n    steps:\n      - run: echo\n        env:\n          K: ${{ secrets.SOME_KEY }}\n"
        self.assertEqual(run({"a.yml": body}), [])

    def test_bracket_and_whole_context_forms(self):
        f = run({"a.yml": wf("steps:\n  - run: echo\n    env:\n      A: ${{ secrets['BRACKET_ONE'] }}\n      B: ${{ toJSON(secrets) }}\n")})
        msgs = " ".join(x.message for x in levels(f, "error"))
        self.assertIn("BRACKET_ONE", msgs)
        self.assertIn("whole secrets context", msgs)

    def test_comment_is_not_a_reference(self):
        f = run({"a.yml": wf("steps:\n  # uses ${{ secrets.COMMENTED }}\n  - run: echo\n")})
        self.assertEqual(f, [])

    def test_workflow_level_env_reaches_routed_job(self):
        f = run({"a.yml": wf("steps:\n  - run: echo\n", header="env:\n  TURBO_TOKEN: ${{ secrets.TURBO_TOKEN }}\n")})
        self.assertIn("TURBO_TOKEN", levels(f, "error")[0].message)

    def test_if_expression_counts(self):
        f = run({"a.yml": wf("steps:\n  - if: secrets.X_FLAG != ''\n    run: echo\n")})
        self.assertIn("X_FLAG", levels(f, "error")[0].message)

    def test_matrix_routing(self):
        body = 'name: t\non: push\njobs:\n  m:\n    strategy:\n      matrix:\n        r: [ "${{ vars.NEBBOS_CI_RUNNER_PR }}" ]\n    runs-on: ${{ matrix.r }}\n    steps:\n      - run: echo\n        env:\n          K: ${{ secrets.M_KEY }}\n'
        self.assertIn("M_KEY", levels(run({"a.yml": body}), "error")[0].message)

    def test_reusable_input_routing(self):
        callee = "name: c\non:\n  workflow_call:\n    inputs:\n      runner: {type: string}\njobs:\n  work:\n    runs-on: ${{ inputs.runner }}\n    steps:\n      - run: echo\n        env:\n          K: ${{ secrets.R_KEY }}\n"
        caller = "name: p\non: push\njobs:\n  call:\n    uses: ./.github/workflows/callee.yml\n    with:\n      runner: ${{ vars.NEBBOS_CI_RUNNER_PR }}\n"
        f = run({"callee.yml": callee, "caller.yml": caller})
        self.assertIn("R_KEY", levels(f, "error")[0].message)

    def test_reusable_input_not_routed_when_caller_hardcodes(self):
        callee = "name: c\non:\n  workflow_call:\n    inputs:\n      runner: {type: string}\njobs:\n  work:\n    runs-on: ${{ inputs.runner }}\n    steps:\n      - run: echo\n        env:\n          K: ${{ secrets.R_KEY }}\n"
        caller = "name: p\non: push\njobs:\n  call:\n    uses: ./.github/workflows/callee.yml\n    with:\n      runner: ubuntu-latest\n"
        self.assertEqual(run({"callee.yml": callee, "caller.yml": caller}), [])

    def test_external_reusable_call_with_inherit_fails(self):
        body = "name: p\non: push\njobs:\n  scan:\n    uses: Nebos-ai/.github/.github/workflows/security-scan.yml@abc\n    with:\n      runner: ${{ vars.NEBBOS_CI_RUNNER_PR }}\n    secrets: inherit\n"
        self.assertIn("inherit", levels(run({"a.yml": body}), "error")[0].message)

    def test_needs_output_routing(self):
        body = "name: t\non: push\njobs:\n  pick:\n    runs-on: ubuntu-latest\n    outputs:\n      r: ${{ steps.s.outputs.r }}\n    steps:\n      - id: s\n        run: echo \"r=${{ vars.NEBBOS_CI_RUNNER_PR }}\" >> $GITHUB_OUTPUT\n  work:\n    needs: pick\n    runs-on: ${{ needs.pick.outputs.r }}\n    steps:\n      - run: echo\n        env:\n          K: ${{ secrets.N_KEY }}\n"
        self.assertIn("N_KEY", levels(run({"a.yml": body}), "error")[0].message)

    def test_environment_on_routed_job_is_a_finding(self):
        f = run({"a.yml": wf("environment: production\nsteps:\n  - run: echo\n")})
        self.assertIn("ENVIRONMENT:PRODUCTION", levels(f, "error")[0].message)

    PENDING = """
        allowed: {}
        pending_fixes:
          - workflow: .github/workflows/a.yml
            job: check
            secrets: [SOME_KEY]
            task: AUT-111
        """
    LEAK = "steps:\n  - run: echo\n    env:\n      K: ${{ secrets.SOME_KEY }}\n"

    def test_pending_job_is_reported_not_failed(self):
        f = run({"a.yml": wf(self.LEAK)}, self.PENDING)
        self.assertEqual(levels(f, "error"), [])
        self.assertEqual(len(levels(f, "warning")), 1)

    def test_pending_job_refused_when_runner_variable_flipped(self):
        f = run({"a.yml": wf(self.LEAK)}, self.PENDING, pr='["self-hosted","nebbos-aws"]')
        errs = levels(f, "error")
        self.assertEqual(len(errs), 1)
        self.assertIn("Refused as a flip target", errs[0].message)
        self.assertIn("AUT-111", errs[0].message)

    def test_hosted_variable_values_are_not_a_flip(self):
        for v in ("", '"ubuntu-latest"', "ubuntu-24.04", '["ubuntu-latest"]'):
            self.assertTrue(g.runner_is_hosted(v), v)
        for v in ('["self-hosted"]', '{"group":"aws"}', "nebbos-aws", '"nebbos-spot"'):
            self.assertFalse(g.runner_is_hosted(v), v)

    def test_pending_does_not_cover_a_new_secret(self):
        body = "steps:\n  - run: echo\n    env:\n      K: ${{ secrets.SOME_KEY }}\n      L: ${{ secrets.OTHER_KEY }}\n"
        f = run({"a.yml": wf(body)}, self.PENDING)
        errs = levels(f, "error")
        self.assertEqual(len(errs), 1)
        self.assertIn("OTHER_KEY", errs[0].message)

    def test_stale_pending_entry_warns(self):
        f = run({"a.yml": wf("steps:\n  - run: echo\n")}, self.PENDING)
        self.assertEqual(levels(f, "error"), [])
        self.assertIn("remove it", levels(f, "warning")[0].message)

    def test_heavy_job_hardcoded_ubuntu_warns(self):
        body = "name: t\non: push\njobs:\n  big:\n    runs-on: ubuntu-latest\n    steps:\n      - run: pytest\n"
        al = "heavy_jobs:\n  - workflow: .github/workflows/a.yml\n    job: big\n"
        f = run({"a.yml": body}, al)
        self.assertEqual(levels(f, "error"), [])
        self.assertIn("hard-codes", levels(f, "warning")[0].message)

    def test_heavy_job_already_routed_does_not_warn(self):
        al = "heavy_jobs:\n  - workflow: .github/workflows/a.yml\n    job: check\n"
        self.assertEqual(run({"a.yml": wf("steps:\n  - run: pytest\n")}, al), [])

    def test_missing_allowlist_is_a_setup_error(self):
        with self.assertRaises(g.SetupError):
            run({"a.yml": wf("steps:\n  - run: echo\n")}, allowlist=None)

    def test_allowlist_entry_needs_reason(self):
        with self.assertRaises(g.SetupError):
            run({"a.yml": wf("steps:\n  - run: echo\n")}, "allowed:\n  X_KEY: ''\n")

    def test_pending_entry_needs_task(self):
        with self.assertRaises(g.SetupError):
            run({"a.yml": wf("steps:\n  - run: echo\n")}, "pending_fixes:\n  - {workflow: a, job: b, secrets: [X]}\n")

    def test_unparseable_workflow_is_a_setup_error(self):
        with self.assertRaises(g.SetupError):
            run({"a.yml": "jobs: [unclosed\n"})


def lit(runs_on: str, on: str = "merge_group", body: str = "steps:\n  - run: echo\n") -> str:
    return f"name: t\non: {on}\njobs:\n  heavy:\n    runs-on: {runs_on}\n{textwrap.indent(body, '    ')}"


class LiteralAwsLabels(unittest.TestCase):
    """AUT-120: a literal nebbos-ci-* label is routed; ondemand only on merge_group or main-only push."""

    LEAK = "steps:\n  - run: echo\n    env:\n      K: ${{ secrets.SOME_KEY }}\n"
    MAIN_PUSH = "\n  push:\n    branches: [main]\n"

    def test_literal_spot_label_string_is_routed_and_checked(self):
        f = run({"a.yml": lit("nebbos-ci-spot", "pull_request", self.LEAK)})
        errs = levels(f, "error")
        self.assertEqual(len(errs), 1)
        self.assertIn("SOME_KEY", errs[0].message)
        self.assertIn("literal nebbos-ci-*", errs[0].message)

    def test_literal_label_in_list_is_routed(self):
        f = run({"a.yml": lit("[self-hosted, linux, x64, nebbos-ci-spot]", "pull_request", self.LEAK)})
        self.assertIn("SOME_KEY", levels(f, "error")[0].message)

    def test_literal_label_in_mapping_labels_is_routed(self):
        f = run({"a.yml": lit("\n      labels: [self-hosted, nebbos-ci-spot]", "pull_request", self.LEAK)})
        self.assertIn("SOME_KEY", levels(f, "error")[0].message)

    def test_literal_label_in_expression_is_routed(self):
        f = run({"a.yml": lit("${{ github.event_name == 'x' && 'nebbos-ci-spot' || 'ubuntu-latest' }}", "pull_request", self.LEAK)})
        self.assertIn("SOME_KEY", levels(f, "error")[0].message)

    def test_literal_label_with_allowlisted_secret_passes(self):
        f = run({"a.yml": lit("nebbos-ci-spot", "pull_request", self.LEAK)}, "allowed:\n  SOME_KEY: ok\n")
        self.assertEqual(levels(f, "error"), [])

    def test_literal_label_counts_as_routed_for_flip_check(self):
        al = "allowed: {}\npending_fixes:\n  - workflow: .github/workflows/a.yml\n    job: heavy\n    secrets: [SOME_KEY]\n    task: AUT-111\n"
        f = run({"a.yml": lit("nebbos-ci-spot", "pull_request", self.LEAK)}, al, pr='["self-hosted"]')
        self.assertIn("Refused as a flip target", levels(f, "error")[0].message)

    def test_label_case_is_ignored(self):
        f = run({"a.yml": lit("NEBBOS-CI-OnDemand", "pull_request")})
        self.assertIn("untrusted event", levels(f, "error")[0].message)

    def test_ondemand_string_on_pull_request_fails(self):
        f = run({"a.yml": lit("nebbos-ci-ondemand", "pull_request")})
        errs = levels(f, "error")
        self.assertEqual(len(errs), 1)
        self.assertIn("pull_request", errs[0].message)
        self.assertIn("NEBBOS_CI_RUNNER_QUEUE", errs[0].message)

    def test_ondemand_list_on_pull_request_fails(self):
        f = run({"a.yml": lit("[self-hosted, linux, x64, nebbos-ci-ondemand]", "pull_request")})
        self.assertEqual(len(levels(f, "error")), 1)

    def test_ondemand_in_expression_fails_even_on_merge_group(self):
        f = run({"a.yml": lit("${{ github.event_name == 'merge_group' && 'nebbos-ci-ondemand' || 'ubuntu-latest' }}")})
        errs = levels(f, "error")
        self.assertEqual(len(errs), 1)
        self.assertIn("inside an expression", errs[0].message)
        self.assertIn("variable", errs[0].message)

    def test_ondemand_merge_group_only_passes(self):
        self.assertEqual(run({"a.yml": lit("nebbos-ci-ondemand", "merge_group")}), [])
        self.assertEqual(run({"a.yml": lit("[self-hosted, nebbos-ci-ondemand]", "[merge_group]")}), [])

    def test_ondemand_merge_group_and_main_push_passes(self):
        on = "\n  merge_group:" + self.MAIN_PUSH
        self.assertEqual(run({"a.yml": lit("nebbos-ci-ondemand", on)}), [])
        self.assertEqual(run({"a.yml": lit("nebbos-ci-ondemand", "\n  push:\n    branches: main\n")}), [])

    def test_ondemand_push_without_branch_filter_fails(self):
        for on in ("push", "\n  push:\n", "\n  push:\n    branches: [main, 'release/*']\n", "\n  push:\n    branches: ['**']\n", "\n  push:\n    branches: [main]\n    tags: ['*']\n"):
            f = run({"a.yml": lit("nebbos-ci-ondemand", on)})
            self.assertEqual(len(levels(f, "error")), 1, on)

    def test_ondemand_fails_on_each_untrusted_event(self):
        for ev in ("pull_request_target", "workflow_dispatch", "schedule", "workflow_call", "issue_comment"):
            f = run({"a.yml": lit("nebbos-ci-ondemand", f"[merge_group, {ev}]")})
            errs = levels(f, "error")
            self.assertEqual(len(errs), 1, ev)
            self.assertIn(ev, errs[0].message)

    def test_ondemand_without_on_key_fails(self):
        body = "name: t\njobs:\n  heavy:\n    runs-on: nebbos-ci-ondemand\n    steps:\n      - run: echo\n"
        self.assertEqual(len(levels(run({"a.yml": body}), "error")), 1)

    def test_queue_variable_routing_stays_allowed_on_pull_request(self):
        # The sanctioned route: no literal label, so the new rule has nothing to flag.
        self.assertEqual(run({"a.yml": wf("steps:\n  - run: echo\n")}), [])

    def test_unrelated_runner_labels_are_not_routed(self):
        f = run({"a.yml": lit("my-nebbos-ci-spot-like", "pull_request", self.LEAK)})
        self.assertEqual(f, [])

    def test_ondemand_literal_also_gets_secret_check(self):
        f = run({"a.yml": lit("nebbos-ci-ondemand", "merge_group", self.LEAK)})
        errs = levels(f, "error")
        self.assertEqual(len(errs), 1)
        self.assertIn("SOME_KEY", errs[0].message)


CALLEE = "on: workflow_call\njobs:\n  j:\n    runs-on: ${{ inputs.runner }}\n    steps: [{run: x}]\n"


def errs(workflows):
    return levels(run(workflows), "error")


class FailClosed(unittest.TestCase):
    """AUT-120 review round: a runs-on that cannot be resolved fails in a workflow with an untrusted event."""

    def one(self, body, **kw):
        return errs({"a.yml": body, **kw})

    def test_matrix_literal_ondemand_fails(self):
        body = "on: pull_request\njobs:\n  a:\n    strategy:\n      matrix:\n        runner: [nebbos-ci-ondemand]\n    runs-on: ${{ matrix.runner }}\n    steps: [{run: x}]\n"
        e = self.one(body)
        self.assertEqual(len(e), 1)
        self.assertIn("pull_request", e[0].message)

    def test_matrix_include_literal_ondemand_fails(self):
        body = "on: pull_request\njobs:\n  a:\n    strategy:\n      matrix:\n        include:\n          - runner: nebbos-ci-ondemand\n    runs-on: ${{ matrix.runner }}\n    steps: [{run: x}]\n"
        self.assertEqual(len(self.one(body)), 1)

    def test_matrix_of_hosted_literals_passes(self):
        body = "on: pull_request\njobs:\n  a:\n    strategy:\n      matrix:\n        os: [ubuntu-latest, ubuntu-24.04]\n    runs-on: ${{ matrix.os }}\n    steps: [{run: x}]\n"
        self.assertEqual(run({"a.yml": body}), [])

    def test_matrix_value_from_unknown_expression_fails(self):
        body = "on: pull_request\njobs:\n  a:\n    strategy:\n      matrix:\n        os: ${{ fromJSON(needs.x.outputs.m) }}\n    runs-on: ${{ matrix.os }}\n    steps: [{run: x}]\n"
        self.assertIn("cannot resolve", self.one(body)[0].message)

    def test_matrix_key_missing_fails(self):
        body = "on: pull_request\njobs:\n  a:\n    strategy:\n      matrix:\n        other: [1]\n    runs-on: ${{ matrix.runner }}\n    steps: [{run: x}]\n"
        self.assertIn("cannot resolve", self.one(body)[0].message)

    def test_format_built_label_fails(self):
        body = "on: pull_request\njobs:\n  a:\n    runs-on: ${{ format('nebbos-ci-{0}', 'ondemand') }}\n    steps: [{run: x}]\n"
        self.assertGreaterEqual(len(self.one(body)), 1)

    def test_split_label_concat_fails(self):
        body = "on: pull_request\njobs:\n  a:\n    runs-on: ${{ 'nebbos-ci-' }}ondemand\n    steps: [{run: x}]\n"
        self.assertEqual(len(self.one(body)), 1)

    def test_env_indirect_fails(self):
        body = "on: pull_request\nenv:\n  R: nebbos-ci-ondemand\njobs:\n  a:\n    runs-on: ${{ env.R }}\n    steps: [{run: x}]\n"
        self.assertIn("cannot resolve", self.one(body)[0].message)

    def test_pull_request_title_as_label_fails(self):
        body = "on: pull_request\njobs:\n  a:\n    runs-on: ${{ github.event.pull_request.title }}\n    steps: [{run: x}]\n"
        self.assertIn("cannot resolve", self.one(body)[0].message)

    def test_sanctioned_routing_with_extra_author_input_fails(self):
        body = "on: pull_request\njobs:\n  a:\n    runs-on: ${{ vars.NEBBOS_CI_RUNNER_PR || github.event.pull_request.title }}\n    steps: [{run: x}]\n"
        self.assertIn("cannot resolve", self.one(body)[0].message)

    def test_hosted_only_expression_passes(self):
        body = "on: pull_request\njobs:\n  a:\n    runs-on: ${{ 'ubuntu-latest' }}\n    steps: [{run: x}]\n"
        self.assertEqual(run({"a.yml": body}), [])

    def test_unresolvable_expression_on_trusted_workflow_passes(self):
        body = "on: merge_group\njobs:\n  a:\n    runs-on: ${{ env.R }}\n    steps: [{run: x}]\n"
        self.assertEqual(run({"a.yml": body}), [])

    def test_reusable_caller_literal_ondemand_fails(self):
        caller = "on: pull_request\njobs:\n  a:\n    uses: ./.github/workflows/r.yml\n    with:\n      runner: nebbos-ci-ondemand\n"
        e = errs({"a.yml": caller, "r.yml": CALLEE})
        self.assertEqual(len(e), 1)
        self.assertIn("pull_request", e[0].message)

    def test_reusable_caller_on_merge_group_passes(self):
        caller = "on: merge_group\njobs:\n  a:\n    uses: ./.github/workflows/r.yml\n    with:\n      runner: nebbos-ci-ondemand\n"
        self.assertEqual(errs({"a.yml": caller, "r.yml": CALLEE}), [])

    def test_reusable_caller_unresolvable_input_fails(self):
        caller = "on: pull_request\njobs:\n  a:\n    uses: ./.github/workflows/r.yml\n    with:\n      runner: ${{ github.event.pull_request.title }}\n"
        self.assertIn("cannot resolve", errs({"a.yml": caller, "r.yml": CALLEE})[0].message)

    def test_reusable_caller_missing_input_fails(self):
        caller = "on: pull_request\njobs:\n  a:\n    uses: ./.github/workflows/r.yml\n"
        self.assertIn("cannot resolve", errs({"a.yml": caller, "r.yml": CALLEE})[0].message)

    def test_reusable_caller_hosted_input_passes(self):
        caller = "on: pull_request\njobs:\n  a:\n    uses: ./.github/workflows/r.yml\n    with:\n      runner: ubuntu-latest\n"
        self.assertEqual(errs({"a.yml": caller, "r.yml": CALLEE}), [])

    def test_callee_without_local_callers_passes(self):
        self.assertEqual(errs({"r.yml": CALLEE}), [])

    def test_external_callee_with_literal_ondemand_on_pr_fails(self):
        caller = "on: pull_request\njobs:\n  a:\n    uses: Nebos-ai/.github/.github/workflows/x.yml@abc\n    with:\n      runner: nebbos-ci-ondemand\n"
        self.assertEqual(len(errs({"a.yml": caller})), 1)

    def test_callee_hardcoded_ondemand_on_workflow_call_fails(self):
        body = "on: workflow_call\njobs:\n  j:\n    runs-on: nebbos-ci-ondemand\n    steps: [{run: x}]\n"
        e = errs({"r.yml": body})
        self.assertEqual(len(e), 1)
        self.assertIn("workflow_call", e[0].message)

    def test_on_mapping_with_null_values(self):
        body = "on:\n  pull_request:\n  merge_group:\njobs:\n  a:\n    runs-on: nebbos-ci-ondemand\n    steps: [{run: x}]\n"
        e = errs({"a.yml": body})
        self.assertEqual(len(e), 1)
        self.assertIn("pull_request", e[0].message)
        ok = "on:\n  merge_group:\njobs:\n  a:\n    runs-on: nebbos-ci-ondemand\n    steps: [{run: x}]\n"
        self.assertEqual(errs({"a.yml": ok}), [])

    def test_job_if_does_not_make_it_trusted(self):
        body = "on: [merge_group, pull_request]\njobs:\n  a:\n    if: github.event_name == 'merge_group'\n    runs-on: nebbos-ci-ondemand\n    steps: [{run: x}]\n"
        self.assertEqual(len(errs({"a.yml": body})), 1)

    def test_yaml_anchor_label_fails(self):
        body = "on: pull_request\nx: &r nebbos-ci-ondemand\njobs:\n  a:\n    runs-on: *r\n    steps: [{run: x}]\n"
        self.assertEqual(len(errs({"a.yml": body})), 1)

    def test_label_with_whitespace_and_group_mapping_fail(self):
        for ro in ("' nebbos-ci-ondemand '", "\n      group: ci\n      labels: nebbos-ci-ondemand", "[nebbos-ci-ondemand, '${{ x }}']"):
            body = f"on: pull_request\njobs:\n  a:\n    runs-on: {ro}\n    steps: [{{run: x}}]\n"
            self.assertGreaterEqual(len(errs({"a.yml": body})), 1, ro)

    def test_push_main_forms(self):
        for br in ("[main]", "['main']", "main"):
            body = f"on:\n  push:\n    branches: {br}\n    paths: ['**']\njobs:\n  a:\n    runs-on: nebbos-ci-ondemand\n    steps: [{{run: x}}]\n"
            self.assertEqual(errs({"a.yml": body}), [], br)
        body = "on:\n  push:\n    branches: [Main]\njobs:\n  a:\n    runs-on: nebbos-ci-ondemand\n    steps: [{run: x}]\n"
        self.assertEqual(len(errs({"a.yml": body})), 1)


if __name__ == "__main__":
    unittest.main()
