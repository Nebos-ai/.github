#!/usr/bin/env python3
"""PUT a validated automations manifest (JSON) into the Nebbos automation registry.

PUT {backend}/api/v1/automations/manifests/{repo}   (nebos-backend, bearer from /auth/service-token)

Fail-soft contract: while the registry endpoint is not deployed (404/405), the service key is missing or
unusable, or the backend is unreachable / 5xx, this prints a warning and exits 0 so the calling
workflow stays green. A 400/422 means the manifest itself is wrong and exits 1.

Env: MANIFEST_JSON, REPO_NAME, NEBOS_BACKEND_URL (optional), K1..K4 (service keys, tried in order).
No secret value is ever printed.
"""
from __future__ import annotations

import json
import os
import sys
import urllib.error
import urllib.request

DEFAULT_BACKEND = "https://api.nebbos.ai"


def call(method: str, url: str, body: object | None = None, token: str | None = None) -> tuple[int, object]:
    data = json.dumps(body).encode() if body is not None else None
    headers = {"Accept": "application/json"}
    if token:
        headers["Authorization"] = f"Bearer {token}"
    if data is not None:
        headers["Content-Type"] = "application/json"
    req = urllib.request.Request(url, data=data, headers=headers, method=method)
    try:
        with urllib.request.urlopen(req, timeout=30) as resp:
            raw = resp.read()
            return resp.status, (json.loads(raw) if raw else None)
    except urllib.error.HTTPError as exc:
        raw = exc.read().decode(errors="replace")
        try:
            return exc.code, json.loads(raw)
        except ValueError:
            return exc.code, raw
    except Exception as exc:  # network failure
        return 0, str(exc)


def mint_token(base: str) -> str | None:
    for name in ("K1", "K2", "K3", "K4"):
        key = os.environ.get(name, "").strip()
        if not key:
            continue
        status, body = call("POST", f"{base}/auth/service-token", {"key": key})
        if status == 200 and isinstance(body, dict):
            data = body.get("data", body)
            token = data.get("access_token") if isinstance(data, dict) else None
            if token:
                print(f"::add-mask::{token}")
                print(f"registry auth: key slot {name} exchanged")
                return token
        print(f"::notice::service key slot {name} not usable (HTTP {status})")
    return None


def main() -> int:
    repo = os.environ["REPO_NAME"]
    base = (os.environ.get("NEBOS_BACKEND_URL", "").strip() or DEFAULT_BACKEND).rstrip("/")
    with open(os.environ["MANIFEST_JSON"], encoding="utf-8") as fh:
        manifest = json.load(fh)

    token = mint_token(base)
    if not token:
        print("::warning::automation registry sync skipped: no usable service key in this repo's secrets")
        return 0

    status, body = call("PUT", f"{base}/api/v1/automations/manifests/{repo}", manifest, token)
    if status in (200, 201, 204):
        print(f"registry accepted manifest for {repo}: {len(manifest['automations'])} automations (HTTP {status})")
        return 0
    if status in (400, 422):
        print(f"::error::registry rejected the manifest (HTTP {status}): {str(body)[:600]}")
        return 1
    print(f"::warning::automation registry sync skipped (HTTP {status}): {str(body)[:300]}. "
          "Expected while the registry endpoint is not deployed.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
