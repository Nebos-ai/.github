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


def run(
    body: str, tracker: FakeTracker, title: str = "A PR", key: str = "nebossvc_x"
) -> tuple[int, str]:
    env = {
        "NEBOS_SERVICE_KEY": key,
        "NEBOS_API_BASE": BASE,
        "PR_NUMBER": "7",
        "PR_TITLE": title,
        "PR_BODY": body,
        "PR_URL": "https://github.com/o/r/pull/7",
        "REPO": "o/r",
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


if __name__ == "__main__":
    unittest.main()
