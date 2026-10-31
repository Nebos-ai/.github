#!/usr/bin/env python3
"""Post one automation run record to the Nebbos registry. Always exits 0.

POST {backend}/api/v1/automations/{slug}/runs
  {external_run_id, trigger, started_at, finished_at, outcome, duration_ms, summary, external_url}
Auth: the service key is exchanged at POST {backend}/auth/service-token for a short-lived bearer
(the pattern main-red-alarm and the billing job workflows already use). No secret is printed.
"""
from __future__ import annotations

import datetime as dt
import json
import os
import sys
import urllib.error
import urllib.request


def warn(msg: str) -> None:
    print(f"::warning::report-automation-run: {msg}")


def call(method: str, url: str, body=None, token=None, accept="application/json"):
    data = json.dumps(body).encode() if body is not None else None
    headers = {"Accept": accept}
    if token:
        headers["Authorization"] = f"Bearer {token}"
    if data is not None:
        headers["Content-Type"] = "application/json"
    req = urllib.request.Request(url, data=data, headers=headers, method=method)
    try:
        with urllib.request.urlopen(req, timeout=20) as resp:
            raw = resp.read()
            return resp.status, (json.loads(raw) if raw else None)
    except urllib.error.HTTPError as exc:
        raw = exc.read().decode(errors="replace")
        try:
            return exc.code, json.loads(raw)
        except ValueError:
            return exc.code, raw
    except Exception as exc:
        return 0, str(exc)


def parse(ts: str) -> dt.datetime:
    return dt.datetime.fromisoformat(ts.replace("Z", "+00:00"))


def iso(t: dt.datetime) -> str:
    return t.astimezone(dt.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def main() -> int:
    env = os.environ
    slug = env.get("SLUG", "").strip()
    if not slug:
        warn("no slug given")
        return 0
    now = dt.datetime.now(dt.timezone.utc)

    outcome = (env.get("OUTCOME") or "failure").lower()
    outcome = outcome if outcome in ("success", "failure", "cancelled", "skipped") else "failure"
    event = env.get("EVENT_NAME", "")
    trigger = {"schedule": "schedule", "workflow_dispatch": "manual"}.get(event, "event")

    repo, run_id = env["REPO"], env["RUN_ID"]
    attempt = env.get("RUN_ATTEMPT", "1")
    started = None
    job_id = None
    gh = "https://api.github.com"
    token = env.get("GH_TOKEN", "")
    if token:
        status, body = call("GET", f"{gh}/repos/{repo}/actions/runs/{run_id}/attempts/{attempt}/jobs?per_page=100",
                            token=token, accept="application/vnd.github+json")
        if status == 200 and isinstance(body, dict):
            mine = [j for j in body.get("jobs", []) if j.get("status") == "in_progress"
                    and (j.get("name") == env.get("JOB_NAME") or j.get("runner_name") == env.get("RUNNER_NAME"))]
            if mine:
                job_id = mine[0]["id"]
                started = parse(mine[0]["started_at"])
        if started is None:
            status, body = call("GET", f"{gh}/repos/{repo}/actions/runs/{run_id}", token=token,
                                accept="application/vnd.github+json")
            if status == 200 and isinstance(body, dict) and body.get("run_started_at"):
                started = parse(body["run_started_at"])
    if started is None:
        started = now
        warn("could not read the job start time; duration is recorded as 0")

    record = {
        "external_run_id": f"gh-{run_id}-{attempt}" + (f"-{job_id}" if job_id else ""),
        "trigger": trigger,
        "started_at": iso(started),
        "finished_at": iso(now),
        "outcome": outcome,
        "duration_ms": max(0, int((now - started).total_seconds() * 1000)),
        "summary": (env.get("SUMMARY") or f"{env.get('WORKFLOW_NAME', '')} / {env.get('JOB_NAME', '')}: {outcome}")[:500],
        "external_url": f"{env.get('SERVER_URL', 'https://github.com')}/{repo}/actions/runs/{run_id}",
    }

    if env.get("DRY_RUN", "false").lower() == "true":
        print(f"dry run, would POST /api/v1/automations/{slug}/runs: {json.dumps(record)}")
        return 0

    key = env.get("SERVICE_KEY", "").strip()
    if not key:
        warn("no service-key input; run record not posted")
        return 0
    base = (env.get("BACKEND_URL", "").strip() or "https://api.nebbos.ai").rstrip("/")
    status, body = call("POST", f"{base}/auth/service-token", {"key": key})
    bearer = None
    if status == 200 and isinstance(body, dict):
        data = body.get("data", body)
        bearer = data.get("access_token") if isinstance(data, dict) else None
    if not bearer:
        warn(f"service-token exchange failed (HTTP {status}); run record not posted")
        return 0
    print(f"::add-mask::{bearer}")

    status, body = call("POST", f"{base}/api/v1/automations/{slug}/runs", record, bearer)
    if status in (200, 201, 202, 204, 409):
        print(f"run record for {slug} accepted (HTTP {status}): {record['outcome']} in {record['duration_ms']} ms")
    elif status == 404:
        warn(f"registry has no endpoint or no automation '{slug}' yet (HTTP 404); expected until the registry and manifest sync land")
    else:
        warn(f"registry returned HTTP {status}: {str(body)[:200]}")
    return 0


if __name__ == "__main__":
    try:
        sys.exit(main())
    except Exception as exc:  # never fail the caller
        warn(f"unexpected error {type(exc).__name__}: {exc}")
        sys.exit(0)
