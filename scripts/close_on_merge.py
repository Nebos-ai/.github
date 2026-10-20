#!/usr/bin/env python3
"""Close-on-merge for the Nebos tracker: one implementation for every Nebos-ai repo.

On a merged pull request, move the tasks the PR names to done (or in_review when the
task's verification asks for a check on the running service).

What counts as a reference (NPTE-181)
-------------------------------------
Only a line that STARTS (after optional spaces or tabs) with ``Task:``, ``Tasks:``,
``Closes:`` or ``Refs:`` (any case), followed by a list of full task UUIDs or short ids
(``PREFIX-N``, for example ``MTCE-28``) separated by commas, semicolons, ``&`` or ``and``.
The list ends at the first word that is not an id. Everything else is context and is never
acted on: prose, quoted text, URLs, fenced code blocks, Markdown checklist lines and a UUID
in the middle of a sentence.

Short ids (INFR-413) are resolved through the tracker, which answers
``GET /api/v1/tasks/<ref>`` for a UUID or a short id under the caller's scope. The task
they resolve to is then handled exactly like a UUID reference; a short id and its UUID on
the same PR close the task once.

Steps the PR delivers (task 3d7ab1bb)
-------------------------------------
Checklist lines never name a task, but they do say which of its steps the PR covers. Before
moving a named task, the script ticks each of its OPEN steps that a ``- [x]`` line under a
Requirements heading covers, then asks for done. The rule is the one the MCP requirements
gate applies when the PR is opened (nebos-backend ``nebos_mcp/requirements_parse.py``): the
section starts at a heading whose text starts with "Requirements" and runs to the next
heading of the same or a higher level, and a line covers a step when it contains the step's
id or the first 30 characters of its title, both lower-cased with whitespace collapsed.
``- [ ]`` lines, lines outside that section and fenced code tick nothing. The task's comment
lists the steps ticked. A tick the tracker refuses fails the run.

Behaviour kept from the per-repo copies
---------------------------------------
- The task is read first. When its **verification** text asks for a live check (after
  deploy, in production, live, deployed, app/api.nebbos.ai) it moves to in_review with a
  comment, not to done. A task that is already done is never reopened.
- 422 ``task_done_gate_open_steps`` (open checklist steps): comment the open steps, leave
  the task open, exit 0. Any other 422 fails the run.
- 404 (unknown UUID or short id): warning, continue. 409: already in that status,
  idempotent.
- The run is red only when at least one reference (UUID or short id) exists and the
  service key is missing or rejected, a short id lookup gets any answer but 200 or 404,
  a covered step cannot be read or ticked, or a transition fails.

The script uses only the Python standard library. It holds no secret and no tenant data:
the key and the API base come from the environment.

Environment: NEBOS_SERVICE_KEY, NEBOS_API_BASE, PR_NUMBER, PR_TITLE, PR_BODY, PR_URL, REPO.
"""

from __future__ import annotations

import json
import os
import re
import sys
import urllib.error
import urllib.request
from collections.abc import Callable, Mapping
from typing import Any

_UUID = r"[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{12}"
_SHORT = r"[A-Z][A-Z0-9]*-[0-9]+"

# A reference line: starts with the keyword, nothing in front of it but spaces or tabs.
_REF_LINE = re.compile(
    r"^[ \t]*(?:tasks?|closes|refs)[ \t]*:[ \t]*(?P<rest>.*)$", re.IGNORECASE
)
# One list item after the keyword: a full UUID or a short id, with an optional separator.
_ITEM = re.compile(
    r"[ \t]*(?:,|;|&|(?i:and)\b)?[ \t]*"
    rf"(?:(?P<uuid>{_UUID})(?![0-9A-Za-z_-])|(?P<short>{_SHORT})(?![0-9A-Za-z_-]))"
)
_FENCE = re.compile(r"^[ \t]*(```|~~~)")

_VERIFICATION = re.compile(
    r"\*\*verification\*\*:?\s*(.*?)(?=\n\*\*[A-Za-z_]+\*\*|\Z)", re.DOTALL | re.IGNORECASE
)
_LIVE_CHECK = re.compile(
    r"after (?:the |a |each |its )?deploy(?:ment)?\b|post[- ]deploy"
    r"|\b(?:in|on|against) prod(?:uction)?\b|\blive\b|\bdeployed\b"
    r"|\b(?:app|api)\.nebbos\.ai\b",
    re.IGNORECASE,
)

# Requirements checklist, as nebos-backend nebos_mcp/requirements_parse.py defines it.
_HEADING = re.compile(r"^\s{0,3}(#{1,6})\s+(.*?)\s*#*\s*$")
_CHECKLIST = re.compile(r"^\s*[-*+]\s+\[([ xX])\]\s+(.*\S)\s*$")
_TITLE_PREFIX_CHARS = 30

DONE_GATE_ERROR = "task_done_gate_open_steps"

HttpFn = Callable[[str, str, "dict[str, Any] | None", "str | None"], "tuple[int, Any]"]


def extract_refs(*texts: str) -> tuple[list[str], list[str]]:
    """Return (full UUIDs, short ids) named on explicit reference lines.

    First-seen order, de-duplicated. UUIDs are lower-cased, short ids upper-cased.
    """
    uuids: list[str] = []
    shorts: list[str] = []
    for text in texts:
        in_fence = False
        for line in (text or "").splitlines():
            if _FENCE.match(line):
                in_fence = not in_fence
                continue
            if in_fence:
                continue
            m = _REF_LINE.match(line)
            if not m:
                continue
            rest = m.group("rest")
            pos = 0
            while True:
                item = _ITEM.match(rest, pos)
                if not item:
                    break
                if item.group("uuid"):
                    uuids.append(item.group("uuid").lower())
                else:
                    shorts.append(item.group("short").upper())
                pos = item.end()
    return list(dict.fromkeys(uuids)), list(dict.fromkeys(shorts))


def _normalize(text: str) -> str:
    """Lower-case and collapse every whitespace run to one space."""
    return " ".join(str(text).lower().split())


def covered_lines(body: str) -> list[str]:
    """The normalised text of every ``- [x]`` line in the body's Requirements section(s).

    A section starts at a heading whose text starts with "Requirements" (any case, any
    level) and runs to the next heading of the same or a higher level; deeper headings stay
    inside it. Lines inside fenced code are skipped.
    """
    lines: list[str] = []
    section_level: int | None = None
    in_fence = False
    for line in (body or "").splitlines():
        if _FENCE.match(line):
            in_fence = not in_fence
            continue
        if in_fence:
            continue
        heading = _HEADING.match(line)
        if heading:
            level = len(heading.group(1))
            if heading.group(2).strip().lower().startswith("requirements"):
                section_level = level
            elif section_level is not None and level <= section_level:
                section_level = None
            continue
        if section_level is None:
            continue
        item = _CHECKLIST.match(line)
        if item and item.group(1).lower() == "x":
            lines.append(_normalize(item.group(2)))
    return lines


def step_covered(step: Mapping[str, Any], lines: list[str]) -> bool:
    """True when a covered line contains the step's id or its title's first 30 characters."""
    step_id = _normalize(step.get("id") or "")
    prefix = _normalize(step.get("title") or "")[:_TITLE_PREFIX_CHARS].strip()
    return any((step_id and step_id in line) or (prefix and prefix in line) for line in lines)


def http_json(
    method: str, url: str, payload: dict[str, Any] | None = None, token: str | None = None
) -> tuple[int, Any]:
    """Minimal stdlib HTTP helper. Returns (status, parsed JSON or raw text); 0 on a network error."""
    data = json.dumps(payload).encode() if payload is not None else None
    headers = {"Content-Type": "application/json", "Accept": "application/json"}
    if token:
        headers["Authorization"] = f"Bearer {token}"
    req = urllib.request.Request(url, data=data, headers=headers, method=method)
    try:
        with urllib.request.urlopen(req, timeout=15) as resp:  # noqa: S310 - https base from env
            raw = resp.read().decode(errors="replace")
            try:
                return resp.status, json.loads(raw)
            except ValueError:
                return resp.status, raw
    except urllib.error.HTTPError as exc:
        raw = exc.read().decode(errors="replace")
        try:
            return exc.code, json.loads(raw)
        except ValueError:
            return exc.code, raw
    except Exception as exc:  # network error, timeout
        return 0, str(exc)


def _ok(status: int) -> bool:
    return 200 <= status < 300


def _open_steps_detail(body: Any) -> dict[str, Any] | None:
    detail = body.get("detail") if isinstance(body, dict) else None
    if isinstance(detail, dict) and detail.get("error") == DONE_GATE_ERROR:
        return detail
    return None


def _task_id_from(body: Any) -> str:
    """The task UUID in a GET /api/v1/tasks/<ref> answer (bare or under ``data``), or ''."""
    if not isinstance(body, dict):
        return ""
    task = body["data"] if isinstance(body.get("data"), dict) else body
    tid = str(task.get("id") or "").lower()
    return tid if re.fullmatch(_UUID, tid) else ""


def run(env: Mapping[str, str], http: HttpFn = http_json) -> int:
    key = (env.get("NEBOS_SERVICE_KEY") or "").strip()
    base = (env.get("NEBOS_API_BASE") or "").strip().rstrip("/")
    pr_number = env.get("PR_NUMBER", "")
    pr_title = env.get("PR_TITLE", "")
    pr_body = env.get("PR_BODY", "") or ""
    pr_url = env.get("PR_URL", "")
    repo = env.get("REPO", "")

    ids, shorts = extract_refs(pr_title, pr_body)
    refs = ids + shorts
    if not refs:
        print(
            f"No task reference on a Task:/Tasks:/Closes:/Refs: line in PR #{pr_number}; "
            "nothing to close."
        )
        return 0
    print(
        f"Acting on {len(refs)} task reference(s) from explicit lines in PR #{pr_number}: "
        + ", ".join(refs)
    )

    if not key:
        print(
            "::error::NEBOS_SERVICE_KEY is not set, so close-on-merge cannot close "
            f"tasks {refs}. Add the repo secret, then re-run this job."
        )
        return 1
    if not base:
        print("::error::NEBOS_API_BASE is not set, so close-on-merge cannot reach the tracker.")
        return 1

    status, body = http("POST", f"{base}/auth/service-token", {"key": key}, None)
    if status in (401, 403):
        print(
            f"::error::Service-token exchange failed (HTTP {status}): the service key is "
            f"invalid, expired or revoked. Tasks {refs} were NOT auto-closed."
        )
        return 1
    if not _ok(status):
        print(f"::error::Service-token exchange failed (HTTP {status}): {str(body)[:200]}")
        return 1
    data = body.get("data", body) if isinstance(body, dict) else {}
    bearer = str(data.get("access_token", "")) if isinstance(data, dict) else ""
    if not bearer:
        print("::error::No access_token in the service-token response.")
        return 1

    pr_label = f"PR #{pr_number} ([{pr_title}]({pr_url})) in {repo}"
    failed = False

    # Short ids: the tracker resolves PREFIX-N to the task UUID under the caller's scope.
    for sid in shorts:
        s_status, s_body = http("GET", f"{base}/api/v1/tasks/{sid}", None, bearer)
        if s_status == 404:
            print(f"::warning::Task {sid} not found (HTTP 404); skipped (unknown short id or wrong tenant).")
            continue
        tid = _task_id_from(s_body) if _ok(s_status) else ""
        if not tid:
            print(f"::warning::Could not resolve task {sid} (HTTP {s_status}): {str(s_body)[:200]}")
            failed = True
            continue
        print(f"  {sid} resolves to task {tid}.")
        if tid not in ids:
            ids.append(tid)

    def comment(tid: str, text: str) -> None:
        c_status, c_body = http("POST", f"{base}/api/v1/tasks/{tid}/comments", {"body": text}, bearer)
        if _ok(c_status):
            print(f"  Comment posted on task {tid}.")
        else:
            print(f"  ::notice::Comment on task {tid} failed (HTTP {c_status}): {str(c_body)[:100]}")

    def read_task(tid: str) -> tuple[str | None, str | None] | None:
        r_status, r_body = http("GET", f"{base}/api/v1/tasks/{tid}", None, bearer)
        if not _ok(r_status) or not isinstance(r_body, dict):
            print(f"  Reading task {tid} failed (HTTP {r_status}).")
            return None
        task = r_body["data"] if isinstance(r_body.get("data"), dict) else r_body
        m = _VERIFICATION.search(str(task.get("plain_summary") or ""))
        check = m.group(1).strip() if m else ""
        return task.get("status"), (check if _LIVE_CHECK.search(check) else None)

    covered = covered_lines(pr_body)

    def tick_covered(tid: str) -> tuple[list[str], bool]:
        """Tick the task's open steps the PR marks [x]. Returns (ticked step lines, ok)."""
        if not covered:
            return [], True
        l_status, l_body = http("GET", f"{base}/api/v1/tasks/{tid}/checklist", None, bearer)
        if l_status == 404:
            return [], True  # unknown task: the transition below reports it
        items = l_body.get("items") if _ok(l_status) and isinstance(l_body, dict) else None
        if not isinstance(items, list):
            print(f"::warning::Could not read the steps of task {tid} (HTTP {l_status}); none ticked.")
            return [], False
        ticked: list[str] = []
        ok = True
        for step in items:
            if not isinstance(step, dict) or step.get("is_checked") or not step_covered(step, covered):
                continue
            sid = str(step.get("id") or "")
            p_status, p_body = http(
                "PATCH", f"{base}/api/v1/tasks/{tid}/checklist/{sid}", {"is_checked": True}, bearer
            )
            if _ok(p_status):
                print(f"  Ticked step {sid} of task {tid}: the PR marks it [x] under Requirements.")
                ticked.append(f"- {step.get('title') or '(untitled)'} ({sid})")
            else:
                print(
                    f"::warning::Could not tick step {sid} of task {tid} "
                    f"(HTTP {p_status}): {str(p_body)[:200]}"
                )
                ok = False
        return ticked, ok

    for tid in ids:
        read = read_task(tid)
        ticked: list[str] = []
        if read is None or read[0] != "done":
            ticked, tick_ok = tick_covered(tid)
            failed = failed or not tick_ok
        note = (
            "\n\nTicked by this merge (the PR marks them [x] under Requirements):\n" + "\n".join(ticked)
            if ticked
            else ""
        )
        if read is None:
            print(f"::warning::Could not read task {tid}; closing it without the live-check rule.")
        elif read[1]:
            current, check = read
            if current == "done":
                print(f"  Task {tid} is already done; not reopened.")
                continue
            if current != "in_review":
                print(f"Moving task {tid} to in_review (verification needs a live check).")
                t_status, t_body = http(
                    "POST", f"{base}/api/v1/tasks/{tid}/transition", {"to_status": "in_review"}, bearer
                )
                print(f"  HTTP {t_status}: {str(t_body)[:200]}")
                if t_status == 404:
                    print(f"::warning::Task {tid} not found (HTTP 404); skipped.")
                    continue
                if not _ok(t_status):
                    print(f"::warning::Failed to move task {tid} to in_review (HTTP {t_status}).")
                    failed = True
                    continue
            moved = "stays in" if current == "in_review" else "moves to"
            shown = check if len(check or "") <= 1500 else (check or "")[:1500] + " ..."
            comment(
                tid,
                f"Merged via {pr_label}. Its verification needs a check on the running service, "
                f"so the task {moved} in_review, not done. Move it to done once this check "
                f"passes:\n\n{shown}{note}",
            )
            continue

        print(f"Moving task {tid} to done.")
        t_status, t_body = http(
            "POST", f"{base}/api/v1/tasks/{tid}/transition", {"to_status": "done"}, bearer
        )
        print(f"  HTTP {t_status}: {str(t_body)[:200]}")
        if t_status == 409:
            print(f"  Task {tid} is already done; idempotent, skipped.")
            continue
        if t_status == 404:
            print(f"::warning::Task {tid} not found (HTTP 404); skipped (unknown id or wrong tenant).")
            continue
        if t_status == 422:
            detail = _open_steps_detail(t_body)
            if detail is not None:
                steps = detail.get("open_steps") or []
                count = detail.get("open_count", len(steps))
                total = detail.get("steps_total", "?")
                print(
                    f"::notice::Task {tid} left open: {count} of {total} checklist steps still open."
                )
                lines = [
                    f"Merged via {pr_label}, but {count} of {total} checklist steps are still "
                    "open, so the task stays open. Open steps:"
                ]
                lines += [
                    f"- {s.get('title', '(untitled)')} ({s.get('id', '?')})"
                    for s in steps
                    if isinstance(s, dict)
                ]
                comment(tid, "\n".join(lines) + note)
                continue
        if not _ok(t_status):
            print(f"::warning::Failed to move task {tid} to done (HTTP {t_status}).")
            failed = True
            continue
        print(f"  Task {tid} moved to done.")
        comment(tid, f"Closed by {pr_label}.{note}")

    if failed:
        print("::error::One or more tasks failed to resolve, tick or transition; see the warnings above.")
        return 1
    print("Done.")
    return 0


def main() -> int:
    return run(os.environ)


if __name__ == "__main__":
    sys.exit(main())
