#!/usr/bin/env python3
"""Close-on-merge for the Nebos tracker: one implementation for every Nebos-ai repo.

On a merged pull request, move the tasks the PR names to done (or in_review when the
task's verification asks for a check on the running service).

What counts as a reference (NPTE-181)
-------------------------------------
Only a line that STARTS (after optional spaces or tabs) with ``Task:``, ``Tasks:``,
``Closes:`` or ``Refs:`` (any case), followed by a list of full task UUIDs separated by
commas, semicolons, ``&`` or ``and``. The list ends at the first word that is not an id.
Everything else is context and is never acted on: prose, quoted text, URLs, fenced code
blocks, Markdown checklist lines and a UUID in the middle of a sentence. Short ids
(for example ``Task: MTCE-28``) are out of scope for now: the script logs a notice that
the line was seen and not resolved.

Behaviour kept from the per-repo copies
---------------------------------------
- The task is read first. When its **verification** text asks for a live check (after
  deploy, in production, live, deployed, app/api.nebbos.ai) it moves to in_review with a
  comment, not to done. A task that is already done is never reopened.
- 422 ``task_done_gate_open_steps`` (open checklist steps): comment the open steps, leave
  the task open, exit 0. Any other 422 fails the run.
- 404: warning, continue. 409: already in that status, idempotent.
- The run is red only when at least one UUID reference exists and the service key is
  missing or rejected, or a transition fails.

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


def run(env: Mapping[str, str], http: HttpFn = http_json) -> int:
    key = (env.get("NEBOS_SERVICE_KEY") or "").strip()
    base = (env.get("NEBOS_API_BASE") or "").strip().rstrip("/")
    pr_number = env.get("PR_NUMBER", "")
    pr_title = env.get("PR_TITLE", "")
    pr_body = env.get("PR_BODY", "") or ""
    pr_url = env.get("PR_URL", "")
    repo = env.get("REPO", "")

    ids, shorts = extract_refs(pr_title, pr_body)
    for sid in shorts:
        print(
            f"::notice::Task reference {sid} on a Task:/Refs: line in PR #{pr_number} was seen "
            "and not resolved: short ids are not handled yet (NPTE-181 follow-up)."
        )
    if not ids:
        print(
            f"No full-UUID reference on a Task:/Tasks:/Closes:/Refs: line in PR #{pr_number}; "
            "nothing to close."
        )
        return 0
    print(
        f"Acting on {len(ids)} task reference(s) from explicit lines in PR #{pr_number}: "
        + ", ".join(ids)
    )

    if not key:
        print(
            "::error::NEBOS_SERVICE_KEY is not set, so close-on-merge cannot close "
            f"tasks {ids}. Add the repo secret, then re-run this job."
        )
        return 1
    if not base:
        print("::error::NEBOS_API_BASE is not set, so close-on-merge cannot reach the tracker.")
        return 1

    status, body = http("POST", f"{base}/auth/service-token", {"key": key}, None)
    if status in (401, 403):
        print(
            f"::error::Service-token exchange failed (HTTP {status}): the service key is "
            f"invalid, expired or revoked. Tasks {ids} were NOT auto-closed."
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

    for tid in ids:
        read = read_task(tid)
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
                f"passes:\n\n{shown}",
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
                comment(tid, "\n".join(lines))
                continue
        if not _ok(t_status):
            print(f"::warning::Failed to move task {tid} to done (HTTP {t_status}).")
            failed = True
            continue
        print(f"  Task {tid} moved to done.")
        comment(tid, f"Closed by {pr_label}.")

    if failed:
        print("::error::One or more tasks failed to transition; see the warnings above.")
        return 1
    print("Done.")
    return 0


def main() -> int:
    return run(os.environ)


if __name__ == "__main__":
    sys.exit(main())
