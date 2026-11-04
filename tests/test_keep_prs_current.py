"""Fixtures for scripts/keep_prs_current.py (PHTW-212). Standard library and git only."""

from __future__ import annotations

import os
import subprocess
import sys
import tempfile
import time
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "scripts"))

import keep_prs_current as kpc  # noqa: E402

PERSON = {
    "GIT_AUTHOR_NAME": "Test Person",
    "GIT_AUTHOR_EMAIL": "fixture@nebbos.ai",
    "GIT_COMMITTER_NAME": "Test Person",
    "GIT_COMMITTER_EMAIL": "fixture@nebbos.ai",
}
GENERATED = ("gen.json", "client/")


class FakeGh:
    def __init__(self, rebase_requested: bool = False) -> None:
        self.calls: list[tuple[str, int, str]] = []
        self.rebase_requested = rebase_requested

    def add_label(self, number: int) -> None:
        self.calls.append(("add_label", number, kpc.CONFLICT_LABEL))

    def remove_label(self, number: int) -> None:
        self.calls.append(("remove_label", number, kpc.CONFLICT_LABEL))

    def comment(self, number: int, body: str) -> None:
        self.calls.append(("comment", number, body))

    def rebase_requested_since(self, number: int, since_epoch: int) -> bool:
        return self.rebase_requested


class PureHelpers(unittest.TestCase):
    def test_parse_paths_splits_on_space_newline_and_comma(self) -> None:
        self.assertEqual(kpc.parse_paths("a.json\n b/ ,c/*.lock"), ("a.json", "b/", "c/*.lock"))
        self.assertEqual(kpc.parse_paths(""), ())
        self.assertEqual(kpc.parse_paths(None), ())

    def test_is_generated_prefix_exact_and_glob(self) -> None:
        specs = ("openapi.json", "clients/python/", "frontend/src/lib/api-client", "*.lock")
        self.assertTrue(kpc.is_generated("openapi.json", specs))
        self.assertTrue(kpc.is_generated("clients/python/x/models.py", specs))
        self.assertTrue(kpc.is_generated("frontend/src/lib/api-client/types.gen.ts", specs))
        self.assertTrue(kpc.is_generated("uv.lock", specs))
        self.assertFalse(kpc.is_generated("openapi.json.bak", specs))
        self.assertFalse(kpc.is_generated("clients/pythonic.py", specs))
        self.assertFalse(kpc.is_generated("nebos_mcp/tool_policy.py", specs))

    def test_split_conflicts_sorts_and_dedupes(self) -> None:
        gen, real = kpc.split_conflicts(["b.py", "gen.json", "a.py", "a.py", "client/x.ts"], GENERATED)
        self.assertEqual(gen, ["client/x.ts", "gen.json"])
        self.assertEqual(real, ["a.py", "b.py"])

    def test_overlap_is_the_shared_files(self) -> None:
        self.assertEqual(kpc.overlap(["a", "b", "c"], ["c", "a", "z"]), ["a", "c"])
        self.assertEqual(kpc.overlap(["a"], ["b"]), [])

    def test_dependabot_logins(self) -> None:
        for login in ("dependabot", "dependabot[bot]", "app/dependabot", "Dependabot"):
            self.assertTrue(kpc.is_dependabot(login), login)
        self.assertFalse(kpc.is_dependabot("Nebbos"))

    def test_debounce_only_for_the_bots_own_recent_commit(self) -> None:
        now = 1_000_000.0
        self.assertTrue(kpc.debounced(kpc.BOT_EMAIL, int(now) - 60, now))
        self.assertFalse(kpc.debounced(kpc.BOT_EMAIL, int(now) - 31 * 60, now))
        self.assertFalse(kpc.debounced("fixture@nebbos.ai", int(now) - 60, now))

    def test_pr_from_node_reads_queue_auto_merge_and_labels(self) -> None:
        pr = kpc.pr_from_node(
            {
                "number": 7, "headRefName": "feat/x", "headRefOid": "abc", "isDraft": True,
                "isCrossRepository": False, "isInMergeQueue": True,
                "author": {"login": "dependabot"}, "autoMergeRequest": {"enabledAt": "t"},
                "labels": {"nodes": [{"name": "conflict"}, {"name": "parked"}]},
            }
        )  # fmt: skip
        self.assertEqual((pr.number, pr.branch, pr.head_sha), (7, "feat/x", "abc"))
        self.assertTrue(pr.is_draft and pr.in_queue and pr.auto_merge)
        self.assertEqual(pr.labels, frozenset({"conflict", "parked"}))
        bare = kpc.pr_from_node({"number": 8, "headRefName": "b", "headRefOid": "d", "author": None})
        self.assertFalse(bare.auto_merge or bare.in_queue or bare.is_fork)
        self.assertEqual(bare.author, "")

    def test_summary_table(self) -> None:
        md = kpc.summary_markdown([kpc.Outcome(3, "conflict", ("a.py",))], "f" * 40, False)
        self.assertIn("| #3 | conflict | `a.py` |", md)
        self.assertIn("No open PRs", kpc.summary_markdown([], "f" * 40, True))


def git(cwd: Path, *args: str, env: dict[str, str] | None = None) -> str:
    proc = subprocess.run(
        ["git", *args], cwd=cwd, capture_output=True, text=True, env={**os.environ, **(env or PERSON)}
    )
    if proc.returncode:
        raise AssertionError(f"git {' '.join(args)}: {proc.stderr}")
    return proc.stdout.strip()


class Repo:
    """A bare origin plus a clone standing in for the caller's checkout of main."""

    def __init__(self, tmp: Path) -> None:
        self.origin = tmp / "origin.git"
        self.root = tmp / "root"
        self.work = tmp / "work"
        git(tmp, "init", "-q", "--bare", "-b", "main", str(self.origin))
        git(tmp, "clone", "-q", str(self.origin), str(self.work))
        self.write("app.py", "top = 1\n" + "".join(f"line{i} = {i}\n" for i in range(20)) + "bottom = 1\n")
        self.write("gen.json", '{"v": 0}\n')
        self.write("other.txt", "other\n")
        git(self.work, "add", "-A")
        git(self.work, "commit", "-q", "-m", "base")
        git(self.work, "push", "-q", "origin", "HEAD:refs/heads/main")
        git(tmp, "clone", "-q", str(self.origin), str(self.root))

    def write(self, rel: str, text: str) -> None:
        path = self.work / rel
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(text)

    def replace(self, rel: str, old: str, new: str) -> None:
        path = self.work / rel
        path.write_text(path.read_text().replace(old, new))

    def commit_on(self, branch: str, start: str, edits: list[tuple[str, str, str]], env=None) -> str:
        git(self.work, "fetch", "-q", "origin")
        git(self.work, "checkout", "-q", "-B", branch, start)
        for rel, old, new in edits:
            self.replace(rel, old, new)
        git(self.work, "commit", "-q", "-am", f"edit on {branch}", env=env)
        git(self.work, "push", "-q", "-f", "origin", f"HEAD:refs/heads/{branch}")
        return git(self.work, "rev-parse", "HEAD")

    def main_sha(self) -> str:
        git(self.root, "fetch", "-q", "origin")
        return git(self.root, "rev-parse", "origin/main")

    def remote_tip(self, branch: str) -> str:
        return git(self.root, "ls-remote", str(self.origin), f"refs/heads/{branch}").split()[0]


def fake_regen(wt: Path, command: str) -> bool:
    (wt / "gen.json").write_text('{"v": "regenerated"}\n')
    return True


class ProcessPr(unittest.TestCase):
    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.repo = Repo(Path(self._tmp.name))
        self.settings = kpc.Settings(generated=GENERATED, regen_command="regen")

    def tearDown(self) -> None:
        self._tmp.cleanup()

    def run_pr(self, pr: kpc.Pr, gh: FakeGh | None = None, settings: kpc.Settings | None = None):
        gh = gh or FakeGh()
        outcome = kpc.process_pr(
            pr, gh=gh, root=self.repo.root, main_sha=self.repo.main_sha(),
            settings=settings or self.settings, regen=fake_regen,
        )  # fmt: skip
        return outcome, gh

    def pr(self, tip: str, branch: str = "feat/x", **kw) -> kpc.Pr:
        return kpc.Pr(number=1, branch=branch, head_sha=tip, **kw)

    def test_no_shared_files_leaves_the_pr_alone(self) -> None:
        tip = self.repo.commit_on("feat/x", "origin/main", [("other.txt", "other", "pr")])
        self.repo.commit_on("main", "origin/main", [("app.py", "bottom = 1", "bottom = 2")])
        outcome, _ = self.run_pr(self.pr(tip))
        self.assertEqual(outcome.result, "skipped(no shared files)")
        self.assertEqual(self.repo.remote_tip("feat/x"), tip)

    def test_clean_shared_file_is_merged_by_the_bot_and_pushed(self) -> None:
        tip = self.repo.commit_on("feat/x", "origin/main", [("app.py", "top = 1", "top = 2")])
        main = self.repo.commit_on("main", "origin/main", [("app.py", "bottom = 1", "bottom = 2")])
        outcome, _ = self.run_pr(self.pr(tip, is_draft=True))
        self.assertEqual(outcome.result, "updated")
        self.assertEqual(outcome.files, ("app.py",))
        new_tip = self.repo.remote_tip("feat/x")
        self.assertNotEqual(new_tip, tip)
        git(self.repo.root, "fetch", "-q", "origin")
        git(self.repo.root, "merge-base", "--is-ancestor", main, new_tip)
        self.assertEqual(git(self.repo.root, "log", "-1", "--format=%ae", new_tip), kpc.BOT_EMAIL)
        self.assertIn(f"{kpc.SHA_TRAILER} {main}", git(self.repo.root, "log", "-1", "--format=%B", new_tip))

    def test_generated_only_conflict_takes_main_and_regenerates(self) -> None:
        tip = self.repo.commit_on("feat/x", "origin/main", [("gen.json", '"v": 0', '"v": 1')])
        self.repo.commit_on("main", "origin/main", [("gen.json", '"v": 0', '"v": 2')])
        outcome, _ = self.run_pr(self.pr(tip))
        self.assertEqual(outcome.result, "updated(regenerated)")
        new_tip = self.repo.remote_tip("feat/x")
        git(self.repo.root, "fetch", "-q", "origin")
        self.assertEqual(git(self.repo.root, "show", f"{new_tip}:gen.json"), '{"v": "regenerated"}')

    def test_real_conflict_labels_and_pushes_nothing(self) -> None:
        tip = self.repo.commit_on(
            "feat/x", "origin/main", [("app.py", "line5 = 5", "line5 = 50"), ("gen.json", '"v": 0', '"v": 1')]
        )
        self.repo.commit_on("main", "origin/main", [("app.py", "line5 = 5", "line5 = 55"), ("gen.json", '"v": 0', '"v": 2')])
        outcome, gh = self.run_pr(self.pr(tip))
        self.assertEqual((outcome.result, outcome.files), ("conflict", ("app.py",)))
        self.assertEqual(gh.calls, [("add_label", 1, kpc.CONFLICT_LABEL)])
        self.assertEqual(self.repo.remote_tip("feat/x"), tip)

    def test_real_conflict_already_labelled_makes_no_call(self) -> None:
        tip = self.repo.commit_on("feat/x", "origin/main", [("app.py", "line5 = 5", "line5 = 50")])
        self.repo.commit_on("main", "origin/main", [("app.py", "line5 = 5", "line5 = 55")])
        outcome, gh = self.run_pr(self.pr(tip, labels=frozenset({"conflict"})))
        self.assertEqual(outcome.result, "conflict")
        self.assertEqual(gh.calls, [])

    def test_dependabot_gets_one_rebase_comment_and_no_commit(self) -> None:
        tip = self.repo.commit_on("feat/x", "origin/main", [("app.py", "top = 1", "top = 2")])
        self.repo.commit_on("main", "origin/main", [("app.py", "bottom = 1", "bottom = 2")])
        outcome, gh = self.run_pr(self.pr(tip, author="dependabot"))
        self.assertEqual(outcome.result, "dependabot-rebase")
        self.assertEqual(gh.calls, [("comment", 1, kpc.REBASE_COMMAND)])
        again, gh2 = self.run_pr(self.pr(tip, author="dependabot"), FakeGh(rebase_requested=True))
        self.assertEqual(again.result, "skipped(dependabot rebase already requested)")
        self.assertEqual(gh2.calls, [])
        self.assertEqual(self.repo.remote_tip("feat/x"), tip)

    def test_bot_commit_under_30_minutes_old_is_debounced(self) -> None:
        tip = self.repo.commit_on("feat/x", "origin/main", [("app.py", "top = 1", "top = 2")], env=kpc.BOT_ENV)
        self.repo.commit_on("main", "origin/main", [("app.py", "bottom = 1", "bottom = 2")])
        outcome, _ = self.run_pr(self.pr(tip))
        self.assertTrue(outcome.result.startswith("skipped(bot updated it"), outcome.result)

    def test_head_moved_since_listing_is_left_for_the_next_run(self) -> None:
        self.repo.commit_on("feat/x", "origin/main", [("app.py", "top = 1", "top = 2")])
        outcome, _ = self.run_pr(self.pr("0" * 40))
        self.assertEqual(outcome.result, "skipped(head moved since listing; next run)")

    def test_up_to_date_pr_drops_a_stale_conflict_label(self) -> None:
        tip = self.repo.commit_on("feat/x", "origin/main", [("app.py", "top = 1", "top = 2")])
        outcome, gh = self.run_pr(self.pr(tip, labels=frozenset({"conflict"})))
        self.assertEqual(outcome.result, "skipped(up to date)")
        self.assertEqual(gh.calls, [("remove_label", 1, kpc.CONFLICT_LABEL)])

    def test_dry_run_reports_and_pushes_nothing(self) -> None:
        tip = self.repo.commit_on("feat/x", "origin/main", [("app.py", "top = 1", "top = 2")])
        self.repo.commit_on("main", "origin/main", [("app.py", "bottom = 1", "bottom = 2")])
        dry = kpc.Settings(generated=GENERATED, regen_command="regen", dry_run=True)
        outcome, gh = self.run_pr(self.pr(tip), settings=dry)
        self.assertEqual(outcome.result, "would-update")
        self.assertEqual((gh.calls, self.repo.remote_tip("feat/x")), ([], tip))

    def test_clean_auto_merge_pr_is_left_to_the_queue(self) -> None:
        tip = self.repo.commit_on("feat/x", "origin/main", [("app.py", "top = 1", "top = 2")])
        self.repo.commit_on("main", "origin/main", [("app.py", "bottom = 1", "bottom = 2")])
        outcome, _ = self.run_pr(self.pr(tip, auto_merge=True))
        self.assertEqual(outcome.result, "skipped(auto-merge; the queue builds on main)")
        self.assertEqual(self.repo.remote_tip("feat/x"), tip)

    def test_fork_and_queued_prs_are_skipped_before_any_git(self) -> None:
        self.assertEqual(self.run_pr(self.pr("x", is_fork=True))[0].result, "skipped(fork)")
        self.assertEqual(self.run_pr(self.pr("x", in_queue=True))[0].result, "skipped(in merge queue)")

    def test_link_shared_inputs_links_dirs_and_missing_files(self) -> None:
        root, wt = self.repo.root, Path(self._tmp.name) / "wt"
        (root / "frontend" / "node_modules").mkdir(parents=True)
        (root / "wheelhouse").mkdir()
        (root / "wheelhouse" / "new.whl").write_text("w")
        (wt / "frontend").mkdir(parents=True)
        (wt / "wheelhouse").mkdir()
        (wt / "wheelhouse" / "committed.whl").write_text("c")
        kpc.link_shared_inputs(root, wt, ("frontend/node_modules", "wheelhouse", "absent"))
        self.assertTrue((wt / "frontend" / "node_modules").is_symlink())
        self.assertTrue((wt / "wheelhouse" / "new.whl").is_symlink())
        self.assertFalse((wt / "wheelhouse" / "committed.whl").is_symlink())


if __name__ == "__main__":
    unittest.main()
