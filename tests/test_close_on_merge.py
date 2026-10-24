"""Fixtures for scripts/close_on_merge.py (NPTE-181, INFR-413). Standard library only."""

from __future__ import annotations

import contextlib
import io
import sys
import unittest
from pathlib import Path
from typing import Any

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "scripts"))

import close_on_merge as com  # noqa: E402

A = "11111111-1111-4111-8111-111111111111"
B = "22222222-2222-4222-8222-222222222222"
C = "33333333-3333-4333-8333-333333333333"
STRAY = "15cfe2a9-c0fd-4cae-b0ba-c3454da4bffe"
BASE = "https://tracker.example"


class FakeTracker:
    """Records every call; answers from a table of per-task behaviours.

    Tasks are keyed by the ref the script sends (a UUID or a short id); a short-id
    entry answers GET with its ``id`` like the real tracker's dual addressing.
    """

    def __init__(self, tasks: dict[str, dict[str, Any]] | None = None, exchange: int = 200) -> None:
        self.tasks = tasks or {}
        self.exchange = exchange
        self.calls: list[tuple[str, str, Any]] = []

    def __call__(
        self, method: str, url: str, payload: dict[str, Any] | None, token: str | None
    ) -> tuple[int, Any]:
        path = url[len(BASE) :]
        self.calls.append((method, path, payload))
        if path == "/auth/service-token":
            if self.exchange == 200:
                return 200, {"data": {"access_token": "bearer"}}
            return self.exchange, {"detail": "no"}
        tid = path.split("/")[4]
        task = self.tasks.get(tid, {"get": 404, "transition": 404})
        if method == "GET" and path.endswith("/checklist"):
            code = task.get("checklist_get", task.get("get", 200))
            if code != 200:
                return code, {"detail": "no"}
            return 200, {"items": task.get("steps", [])}
        if method == "PATCH" and "/checklist/" in path:
            code = task.get("patch", 200)
            if code == 200:
                for step in task.get("steps", []):
                    if step["id"] == path.rsplit("/", 1)[1]:
                        step["is_checked"] = (payload or {}).get("is_checked", step["is_checked"])
            return code, {"ok": code == 200}
        if (
            path.endswith("/transition")
            and "steps" in task
            and "transition" not in task
            and (payload or {}).get("to_status") == "done"
        ):
            open_steps = [s for s in task["steps"] if not s.get("is_checked")]
            if open_steps:
                return 422, {
                    "detail": {
                        "error": "task_done_gate_open_steps",
                        "open_count": len(open_steps),
                        "steps_total": len(task["steps"]),
                        "open_steps": open_steps,
                    }
                }
        if method == "GET":
            code = task.get("get", 200)
            if code != 200:
                return code, {"detail": "missing"}
            return 200, {
                "id": task.get("id", tid),
                "status": task.get("status", "todo"),
                "plain_summary": task.get("summary", ""),
            }
        if path.endswith("/comments"):
            return 201, {"id": "c"}
        return task.get("transition", 201), task.get("transition_body", {"ok": True})

    def transitions(self) -> list[tuple[str, str]]:
        return [
            (p.split("/")[4], d["to_status"])
            for m, p, d in self.calls
            if m == "POST" and p.endswith("/transition")
        ]

    def comments(self) -> list[str]:
        return [d["body"] for m, p, d in self.calls if m == "POST" and p.endswith("/comments")]

    def patched(self) -> list[tuple[str, str]]:
        return [(p.split("/")[4], p.rsplit("/", 1)[1]) for m, p, _ in self.calls if m == "PATCH"]

    def done_evidence(self) -> list[Any]:
        return [
            d.get("evidence")
            for m, p, d in self.calls
            if m == "POST" and p.endswith("/transition") and d["to_status"] == "done"
        ]


def run(
    body: str,
    tracker: FakeTracker,
    title: str = "A PR",
    key: str = "nebossvc_x",
    extra: dict[str, str] | None = None,
) -> tuple[int, str]:
    env = {
        "NEBOS_SERVICE_KEY": key,
        "NEBOS_API_BASE": BASE,
        "PR_NUMBER": "7",
        "PR_TITLE": title,
        "PR_BODY": body,
        "PR_URL": "https://github.com/o/r/pull/7",
        "REPO": "o/r",
        **(extra or {}),
    }
    out = io.StringIO()
    with contextlib.redirect_stdout(out):
        rc = com.run(env, tracker)
    return rc, out.getvalue()


class ParserTests(unittest.TestCase):
    def test_explicit_only(self) -> None:
        body = f"Task: {A}\n\nUnblocks {B} and relates to {C}."
        self.assertEqual(com.extract_refs("t", body), ([A], []))

    def test_exact_1793_line_is_ignored(self) -> None:
        body = f"Task: {A}\nPlan of record: PHTW-44 ({STRAY}), Phase 1 item 1.\n"
        self.assertEqual(com.extract_refs("t", body), ([A], []))

    def test_checklist_uuids_ignored(self) -> None:
        body = f"## Requirements\n- [x] step {B}\n* [ ] step {C}\n  - [X] step {STRAY}\n"
        self.assertEqual(com.extract_refs("t", body), ([], []))

    def test_checklist_line_starting_with_keyword_after_box_is_ignored(self) -> None:
        self.assertEqual(com.extract_refs("t", f"- [ ] Task: {B}"), ([], []))

    def test_mid_prose_task_colon_is_ignored(self) -> None:
        body = f"This PR unblocks task: {B} once deployed.\nSee also closes: {C} in the thread."
        self.assertEqual(com.extract_refs("t", body), ([], []))

    def test_quote_and_code_ignored(self) -> None:
        body = f"> Task: {B}\n```\nTask: {C}\n```\n`Task: {STRAY}`\n~~~\nRefs: {A}\n~~~\n"
        self.assertEqual(com.extract_refs("t", body), ([], []))

    def test_multi_id_refs_line(self) -> None:
        body = f"Refs: {A}, {B} and {C}"
        self.assertEqual(com.extract_refs("t", body), ([A, B, C], []))

    def test_keywords_case_insensitive_and_indent(self) -> None:
        body = f"  TASK: {A}\n\tcloses:{B}\nTasks: {C}"
        self.assertEqual(com.extract_refs("t", body), ([A, B, C], []))

    def test_list_stops_at_prose(self) -> None:
        body = f"Task: {A} and also {B}"
        self.assertEqual(com.extract_refs("t", body), ([A], []))

    def test_uuid_case_folded_and_deduped(self) -> None:
        self.assertEqual(com.extract_refs(f"Task: {A.upper()}", f"Task: {A}"), ([A], []))

    def test_short_ids_collected(self) -> None:
        self.assertEqual(com.extract_refs("t", "Task: MTCE-28\nRefs: PTR-1, PTR-2"), ([], ["MTCE-28", "PTR-1", "PTR-2"]))

    def test_title_line_counts(self) -> None:
        self.assertEqual(com.extract_refs(f"Task: {A}", ""), ([A], []))

    def test_longer_hex_run_is_not_a_uuid(self) -> None:
        self.assertEqual(com.extract_refs("t", f"Task: {A}-extra"), ([], []))


class RunTests(unittest.TestCase):
    def test_done_with_comment(self) -> None:
        t = FakeTracker({A: {"status": "todo", "summary": "**verification**: unit test passes"}})
        rc, _ = run(f"Task: {A}\nprose {B}", t)
        self.assertEqual(rc, 0)
        self.assertEqual(t.transitions(), [(A, "done")])
        self.assertEqual(len(t.comments()), 1)
        self.assertNotIn(B, " ".join(p for _, p, _ in t.calls))

    def test_1793_body_touches_only_the_task_line(self) -> None:
        t = FakeTracker({A: {}, STRAY: {}})
        body = f"Task: {A}\nPlan of record: PHTW-44 ({STRAY}), Phase 1 item 1."
        rc, _ = run(body, t)
        self.assertEqual(rc, 0)
        self.assertEqual([tid for tid, _ in t.transitions()], [A])
        self.assertFalse(any(STRAY in p for _, p, _ in t.calls))

    def test_multi_id_refs_closes_each(self) -> None:
        t = FakeTracker({A: {}, B: {}})
        rc, _ = run(f"Refs: {A}, {B}", t)
        self.assertEqual(rc, 0)
        self.assertEqual(t.transitions(), [(A, "done"), (B, "done")])

    def test_live_rule_moves_to_in_review(self) -> None:
        t = FakeTracker({A: {"status": "in_progress", "summary": "**verification**: check it live after deploy"}})
        rc, _ = run(f"Task: {A}", t)
        self.assertEqual(rc, 0)
        self.assertEqual(t.transitions(), [(A, "in_review")])
        self.assertIn("in_review, not done", t.comments()[0])

    def test_live_rule_already_in_review_only_comments(self) -> None:
        t = FakeTracker({A: {"status": "in_review", "summary": "**verification**: live check"}})
        rc, _ = run(f"Task: {A}", t)
        self.assertEqual(rc, 0)
        self.assertEqual(t.transitions(), [])
        self.assertEqual(len(t.comments()), 1)

    def test_done_task_is_never_reopened(self) -> None:
        t = FakeTracker({A: {"status": "done", "summary": "**verification**: live check"}})
        rc, out = run(f"Task: {A}", t)
        self.assertEqual(rc, 0)
        self.assertEqual(t.transitions(), [])
        self.assertEqual(t.comments(), [])
        self.assertIn("not reopened", out)

    def test_404_is_a_warning(self) -> None:
        t = FakeTracker({})
        rc, out = run(f"Task: {A}", t)
        self.assertEqual(rc, 0)
        self.assertIn("::warning::", out)

    def test_409_is_idempotent(self) -> None:
        t = FakeTracker({A: {"transition": 409}})
        rc, out = run(f"Task: {A}", t)
        self.assertEqual(rc, 0)
        self.assertIn("idempotent", out)

    def test_open_steps_422_comments_and_stays_green(self) -> None:
        detail = {
            "detail": {
                "error": "task_done_gate_open_steps",
                "open_count": 1,
                "steps_total": 3,
                "open_steps": [{"id": "s1", "title": "Wire it"}],
            }
        }
        t = FakeTracker({A: {"transition": 422, "transition_body": detail}})
        rc, _ = run(f"Task: {A}", t)
        self.assertEqual(rc, 0)
        self.assertIn("Wire it", t.comments()[0])

    def test_other_422_fails(self) -> None:
        t = FakeTracker({A: {"transition": 422, "transition_body": {"detail": [{"msg": "bad"}]}}})
        rc, _ = run(f"Task: {A}", t)
        self.assertEqual(rc, 1)
        self.assertEqual(t.comments(), [])

    def test_missing_key_with_reference_fails(self) -> None:
        t = FakeTracker({A: {}})
        rc, out = run(f"Task: {A}", t, key="")
        self.assertEqual(rc, 1)
        self.assertIn("::error::", out)
        self.assertEqual(t.calls, [])

    def test_missing_key_without_reference_is_green(self) -> None:
        t = FakeTracker({})
        rc, _ = run(f"just prose with {A}", t, key="")
        self.assertEqual(rc, 0)
        self.assertEqual(t.calls, [])

    def test_rejected_key_fails(self) -> None:
        t = FakeTracker({A: {}}, exchange=401)
        rc, out = run(f"Task: {A}", t)
        self.assertEqual(rc, 1)
        self.assertIn("::error::", out)

    # Short ids (INFR-413): resolved through the tracker, then handled like a UUID.

    def test_short_id_resolves_and_closes(self) -> None:
        t = FakeTracker({"MTCE-28": {"id": A}, A: {}})
        rc, out = run("Task: MTCE-28", t)
        self.assertEqual(rc, 0)
        self.assertEqual(t.transitions(), [(A, "done")])
        self.assertIn("MTCE-28 resolves to task", out)
        self.assertEqual(len(t.comments()), 1)

    def test_short_id_and_its_uuid_close_the_task_once(self) -> None:
        t = FakeTracker({"MTCE-28": {"id": A}, A: {}})
        rc, _ = run(f"Task: {A}\nRefs: MTCE-28", t)
        self.assertEqual(rc, 0)
        self.assertEqual(t.transitions(), [(A, "done")])

    def test_multi_short_ids_close_each(self) -> None:
        t = FakeTracker({"PTR-1": {"id": A}, "PTR-2": {"id": B}, A: {}, B: {}})
        rc, _ = run("Refs: PTR-1, PTR-2", t)
        self.assertEqual(rc, 0)
        self.assertEqual(t.transitions(), [(A, "done"), (B, "done")])

    def test_short_id_follows_the_live_rule(self) -> None:
        t = FakeTracker({"MTCE-28": {"id": A}, A: {"status": "todo", "summary": "**verification**: check it live"}})
        rc, _ = run("Task: MTCE-28", t)
        self.assertEqual(rc, 0)
        self.assertEqual(t.transitions(), [(A, "in_review")])

    def test_unknown_short_id_is_a_warning(self) -> None:
        t = FakeTracker({})
        rc, out = run("Task: MTCE-28", t)
        self.assertEqual(rc, 0)
        self.assertIn("::warning::", out)
        self.assertEqual(t.transitions(), [])

    def test_short_id_lookup_error_fails_red(self) -> None:
        t = FakeTracker({"MTCE-28": {"get": 500}})
        rc, out = run("Task: MTCE-28", t)
        self.assertEqual(rc, 1)
        self.assertIn("::error::", out)
        self.assertEqual(t.transitions(), [])

    def test_short_id_answer_without_uuid_fails_red(self) -> None:
        t = FakeTracker({"MTCE-28": {"id": "not-a-uuid"}})
        rc, _ = run("Task: MTCE-28", t)
        self.assertEqual(rc, 1)
        self.assertEqual(t.transitions(), [])

    def test_missing_key_with_short_reference_fails(self) -> None:
        t = FakeTracker({})
        rc, out = run("Task: MTCE-28", t, key="")
        self.assertEqual(rc, 1)
        self.assertIn("::error::", out)
        self.assertEqual(t.calls, [])


# Steps the PR marks [x] under Requirements (task 3d7ab1bb).

S1 = "aaaaaaaa-0000-4000-8000-000000000001"
S2 = "aaaaaaaa-0000-4000-8000-000000000002"


def steps(*rows: tuple[str, str, bool]) -> list[dict[str, Any]]:
    return [{"id": sid, "title": title, "is_checked": done} for sid, title, done in rows]


class RequirementsParserTests(unittest.TestCase):
    def test_only_checked_lines_inside_the_section(self) -> None:
        body = (
            "Task: x\n- [x] before the section\n## Requirements\n- [x] One done\n- [ ] Two open\n"
            "* [X] Three star\n+ [x]   Four   plus\n### Notes inside\n- [x] Five nested\n"
            "## Test plan\n- [x] Six after\n"
        )
        self.assertEqual(
            com.covered_lines(body), ["one done", "three star", "four plus", "five nested"]
        )

    def test_section_at_any_level_and_restarts(self) -> None:
        body = "### requirements (part 1)\n- [x] a\n## Other\n- [x] b\n# REQUIREMENTS\n- [x] c\n"
        self.assertEqual(com.covered_lines(body), ["a", "c"])

    def test_fenced_lines_tick_nothing(self) -> None:
        body = "## Requirements\n```\n- [x] fenced\n```\n- [x] real\n"
        self.assertEqual(com.covered_lines(body), ["real"])

    def test_no_section_no_lines(self) -> None:
        self.assertEqual(com.covered_lines("- [x] loose\nTask: x"), [])

    def test_step_matched_by_id_or_title_prefix(self) -> None:
        lines = com.covered_lines(
            f"## Requirements\n- [x] {S1.upper()} anything\n"
            "- [x] statement   PDF with seller entity; other\n"
        )
        self.assertTrue(com.step_covered({"id": S1, "title": "x"}, lines))
        pdf = "Statement PDF with seller entity; other account's data absent"
        self.assertTrue(com.step_covered({"id": S2, "title": pdf}, lines))
        self.assertFalse(com.step_covered({"id": C, "title": "Something else entirely here"}, lines))


class RequirementsRunTests(unittest.TestCase):
    def test_every_step_covered_ticks_then_done(self) -> None:
        t = FakeTracker({A: {"steps": steps((S1, "Wire the route", False), (S2, "Add the tests", False))}})
        body = f"Task: {A}\n\n## Requirements\n- [x] {S1} Wire the route\n- [x] Add the tests\n"
        rc, out = run(body, t)
        self.assertEqual(rc, 0)
        self.assertEqual(t.patched(), [(A, S1), (A, S2)])
        self.assertEqual(t.transitions(), [(A, "done")])
        self.assertIn("moved to done", out)
        self.assertEqual(len(t.comments()), 1)
        self.assertIn("Ticked by this merge", t.comments()[0])
        self.assertIn(S2, t.comments()[0])

    def test_unchecked_line_stays_open_and_task_stays_open(self) -> None:
        t = FakeTracker({A: {"steps": steps((S1, "Wire the route", False), (S2, "Watch the next run", False))}})
        rc, _ = run(f"Task: {A}\n## Requirements\n- [x] {S1}\n- [ ] {S2}\n", t)
        self.assertEqual(rc, 0)
        self.assertEqual(t.patched(), [(A, S1)])
        self.assertEqual(t.transitions(), [(A, "done")])
        self.assertIn("1 of 2 checklist steps are still open", t.comments()[0])
        self.assertIn("Ticked by this merge", t.comments()[0])

    def test_already_checked_step_not_patched_again(self) -> None:
        t = FakeTracker({A: {"steps": steps((S1, "Wire the route", True), (S2, "Add the tests", False))}})
        rc, _ = run(f"Task: {A}\n## Requirements\n- [x] {S1}\n- [x] {S2}\n", t)
        self.assertEqual(rc, 0)
        self.assertEqual(t.patched(), [(A, S2)])
        self.assertEqual(t.transitions(), [(A, "done")])

    def test_no_requirements_section_reads_no_steps(self) -> None:
        t = FakeTracker({A: {"steps": steps((S1, "Wire the route", False))}})
        rc, _ = run(f"Task: {A}\n- [x] {S1}\n", t)
        self.assertEqual(rc, 0)
        self.assertFalse(any("/checklist" in p for _, p, _ in t.calls))
        self.assertEqual(t.patched(), [])

    def test_refused_tick_fails_red(self) -> None:
        t = FakeTracker({A: {"steps": steps((S1, "Wire the route", False)), "patch": 403}})
        rc, out = run(f"Task: {A}\n## Requirements\n- [x] {S1}\n", t)
        self.assertEqual(rc, 1)
        self.assertIn("Could not tick step", out)

    def test_unreadable_steps_fail_red(self) -> None:
        t = FakeTracker({A: {"steps": [], "checklist_get": 500}})
        rc, _ = run(f"Task: {A}\n## Requirements\n- [x] {S1}\n", t)
        self.assertEqual(rc, 1)
        self.assertEqual(t.patched(), [])

    def test_done_task_steps_not_touched(self) -> None:
        t = FakeTracker({A: {"status": "done", "transition": 409, "steps": steps((S1, "Wire the route", False))}})
        rc, _ = run(f"Task: {A}\n## Requirements\n- [x] {S1}\n", t)
        self.assertEqual(rc, 0)
        self.assertFalse(any("/checklist" in p for _, p, _ in t.calls))

    def test_unknown_task_with_requirements_is_still_a_warning(self) -> None:
        t = FakeTracker({})
        rc, out = run(f"Task: {A}\n## Requirements\n- [x] {S1}\n", t)
        self.assertEqual(rc, 0)
        self.assertIn("::warning::", out)
        self.assertEqual(t.patched(), [])

    def test_unnamed_task_is_never_touched(self) -> None:
        t = FakeTracker({A: {"steps": steps((S1, "Wire the route", False))}, B: {"steps": steps((S2, "Add the tests", False))}})
        rc, _ = run(f"Task: {A}\n## Requirements\n- [x] {S1}\n- [x] {S2}\n", t)
        self.assertEqual(rc, 0)
        self.assertEqual(t.patched(), [(A, S1)])
        self.assertFalse(any(B in p for _, p, _ in t.calls))

    def test_short_id_task_steps_ticked(self) -> None:
        t = FakeTracker({"INFR-413": {"id": B}, A: {"steps": steps((S1, "Wire the route", False))}, B: {"steps": steps((S2, "Add the tests", False))}})
        rc, out = run(f"Task: {A}\nRefs: INFR-413\n## Requirements\n- [x] {S1}\n- [x] {S2}\n", t)
        self.assertEqual(rc, 0)
        self.assertIn("INFR-413 resolves to task", out)
        self.assertEqual(t.patched(), [(A, S1), (B, S2)])
        self.assertEqual(t.transitions(), [(A, "done"), (B, "done")])

    def test_live_rule_ticks_then_in_review(self) -> None:
        t = FakeTracker({A: {"status": "in_progress", "summary": "**verification**: check it live after deploy", "steps": steps((S1, "Wire the route", False))}})
        rc, _ = run(f"Task: {A}\n## Requirements\n- [x] {S1}\n", t)
        self.assertEqual(rc, 0)
        self.assertEqual(t.patched(), [(A, S1)])
        self.assertEqual(t.transitions(), [(A, "in_review")])
        self.assertIn("Ticked by this merge", t.comments()[0])


# Close evidence for the tracker's done-evidence gate (MTCE-67).


class EvidenceTests(unittest.TestCase):
    def test_done_sends_merged_pr_and_merge_commit(self) -> None:
        t = FakeTracker({A: {}})
        extra = {"MERGE_SHA": "ABCDEF1234567", "MERGED_AT": "2026-10-24T13:00:00Z"}
        rc, out = run(f"Task: {A}", t, extra=extra)
        self.assertEqual(rc, 0)
        self.assertEqual(
            t.done_evidence(),
            [
                [
                    {"kind": "pull_request", "id": "o/r#7", "state": "merged", "as_of": "2026-10-24T13:00:00Z"},
                    {"kind": "commit", "id": "o/r@abcdef1234567"},
                ]
            ],
        )
        self.assertIn("Close evidence for each move to done: o/r#7, o/r@abcdef1234567", out)

    def test_without_merge_details_sends_the_pr_alone(self) -> None:
        t = FakeTracker({A: {}})
        rc, _ = run(f"Task: {A}", t)
        self.assertEqual(rc, 0)
        self.assertEqual(t.done_evidence(), [[{"kind": "pull_request", "id": "o/r#7", "state": "merged"}]])

    def test_every_task_closed_gets_the_evidence(self) -> None:
        t = FakeTracker({A: {}, B: {}})
        rc, _ = run(f"Refs: {A}, {B}", t, extra={"MERGE_SHA": "abcdef1"})
        self.assertEqual(rc, 0)
        self.assertEqual(len(t.done_evidence()), 2)
        self.assertTrue(all(len(e) == 2 for e in t.done_evidence()))

    def test_malformed_repo_or_number_gives_no_evidence(self) -> None:
        self.assertEqual(com.close_evidence("not a repo", "7"), [])
        self.assertEqual(com.close_evidence("o/r", "07"), [])
        self.assertEqual(com.close_evidence("o/r", ""), [])

    def test_bad_sha_and_time_are_dropped(self) -> None:
        refs = com.close_evidence("o/r", "7", "zzz", "yesterday")
        self.assertEqual(refs, [{"kind": "pull_request", "id": "o/r#7", "state": "merged"}])

    def test_no_evidence_means_a_plain_move_and_a_warning(self) -> None:
        t = FakeTracker({A: {}})
        env_bad = {"REPO": "", "PR_NUMBER": "x"}
        rc, out = run(f"Task: {A}", t, extra=env_bad)
        self.assertEqual(rc, 0)
        self.assertEqual(t.done_evidence(), [None])
        self.assertIn("No well-formed close evidence", out)

    def test_in_review_move_sends_no_evidence(self) -> None:
        t = FakeTracker({A: {"status": "in_progress", "summary": "**verification**: check it live"}})
        rc, _ = run(f"Task: {A}", t, extra={"MERGE_SHA": "abcdef1"})
        self.assertEqual(rc, 0)
        moves = [d for m, p, d in t.calls if m == "POST" and p.endswith("/transition")]
        self.assertEqual([d["to_status"] for d in moves], ["in_review"])
        self.assertNotIn("evidence", moves[0])

    def test_evidence_refusal_fails_red(self) -> None:
        body = {"detail": {"error": "task_done_evidence_invalid", "problems": ["evidence[0]: bad"]}}
        t = FakeTracker({A: {"transition": 422, "transition_body": body}})
        rc, _ = run(f"Task: {A}", t)
        self.assertEqual(rc, 1)


if __name__ == "__main__":
    unittest.main()
