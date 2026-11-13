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


if __name__ == "__main__":
    unittest.main()
