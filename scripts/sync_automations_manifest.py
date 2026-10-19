#!/usr/bin/env python3
"""PUT a validated automations manifest (JSON) into the Nebbos automation registry.

PUT {backend}/api/v1/automations/manifests/{repo}   (nebos-backend, bearer from /auth/service-token)

Contract (AUT-70):
- Key slots are tried in order: KB (NEBOS_BACKEND_SERVICE_KEY, a backend key for the operator tenant),
  then K1 NEBOS_SERVICE_KEY, K2 NEBOS_PROJECTS_SERVICE_KEY, K3 NEBOS_SERVICE_TOKEN, K4
  BILLING_SERVICE_ACCOUNT_KEY. K1-K3 are tracker keys the backend rejects with 401; they stay so nothing
  that works today breaks.
- When no slot exchanges, the run FAILS (exit 1) with an ::error:: naming AUT-70 and each slot's HTTP status.
  It used to warn and pass, which hid weeks of missing syncs.
- After an exchange the tenant id is read from the JWT claims and logged (never the key or token). When
  EXPECTED_TENANT_ID is set and differs, the run fails before anything is written.
- Still fail-soft once authenticated: registry endpoint not deployed (404/405), 5xx or unreachable warn and
  exit 0. A 400/422 means the manifest itself is wrong and exits 1.

Env: MANIFEST_JSON, REPO_NAME, NEBOS_BACKEND_URL (optional), EXPECTED_TENANT_ID (optional), KB, K1..K4.
No secret value is ever printed.
"""
from __future__ import annotations

import base64
import json
import os
import sys
import urllib.error
import urllib.request

DEFAULT_BACKEND = "https://api.nebbos.ai"
TASK = "AUT-70"

# (env var, secret name), tried in this order. The backend key goes first.
SLOTS: list[tuple[str, str]] = [
    ("KB", "NEBOS_BACKEND_SERVICE_KEY"),
    ("K1", "NEBOS_SERVICE_KEY"),
    ("K2", "NEBOS_PROJECTS_SERVICE_KEY"),
    ("K3", "NEBOS_SERVICE_TOKEN"),
    ("K4", "BILLING_SERVICE_ACCOUNT_KEY"),
]
TENANT_CLAIMS = ("tenant_id", "tid", "tenant")


def jwt_tenant(token: str) -> str | None:
    """Tenant id from the JWT payload (unverified, for logging and the expected-tenant check only)."""
    try:
        part = token.split(".")[1]
        claims = json.loads(base64.urlsafe_b64decode(part + "=" * (-len(part) % 4)))
    except Exception:
        return None
    if not isinstance(claims, dict):
        return None
    for name in TENANT_CLAIMS:
        value = claims.get(name)
        if isinstance(value, str) and value:
            return value
    return None


def exchange_failure_message(attempts: list[tuple[str, str, str]]) -> str:
    """attempts: (env var, secret name, 'HTTP n' or 'not set')."""
    tried = ", ".join(f"{secret} ({var}): {result}" for var, secret, result in attempts)
    return (f"{TASK}: automation registry sync FAILED, no service key slot exchanged at /auth/service-token. "
            f"Slots tried in order: {tried}. Mint NEBOS_BACKEND_SERVICE_KEY (a backend key for the operator "
            f"tenant) in this repo's secrets; K1-K3 are tracker keys the backend rejects with 401.")


def check_tenant(tenant: str | None, expected: str) -> str | None:
    """Error text when an expected tenant is set and the token's tenant differs or is unknown; else None."""
    expected = expected.strip()
    if not expected:
        return None
    if tenant is None:
        return f"{TASK}: expected tenant {expected} but the token carries no readable tenant claim; refusing to write."
    if tenant.lower() != expected.lower():
        return (f"{TASK}: token is for tenant {tenant}, expected {expected}; refusing to write the manifest "
                "into the wrong tenant.")
    return None


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


def mint_token(base: str) -> tuple[str | None, str | None, list[tuple[str, str, str]]]:
    """Returns (token, slot name, attempts). attempts lists every slot tried with its result."""
    attempts: list[tuple[str, str, str]] = []
    for var, secret in SLOTS:
        key = os.environ.get(var, "").strip()
        if not key:
            attempts.append((var, secret, "not set"))
            continue
        status, body = call("POST", f"{base}/auth/service-token", {"key": key})
        if status == 200 and isinstance(body, dict):
            data = body.get("data", body)
            token = data.get("access_token") if isinstance(data, dict) else None
            if token:
                print(f"::add-mask::{token}")
                print(f"registry auth: key slot {var} ({secret}) exchanged")
                return token, var, attempts
        attempts.append((var, secret, f"HTTP {status}"))
        print(f"::notice::service key slot {var} ({secret}) not usable (HTTP {status})")
    return None, None, attempts


def main() -> int:
    repo = os.environ["REPO_NAME"]
    base = (os.environ.get("NEBOS_BACKEND_URL", "").strip() or DEFAULT_BACKEND).rstrip("/")
    with open(os.environ["MANIFEST_JSON"], encoding="utf-8") as fh:
        manifest = json.load(fh)

    token, slot, attempts = mint_token(base)
    if not token:
        print(f"::error::{exchange_failure_message(attempts)}")
        return 1

    tenant = jwt_tenant(token)
    print(f"registry auth: token from slot {slot} is for tenant {tenant or 'unknown (no tenant claim)'}")
    bad = check_tenant(tenant, os.environ.get("EXPECTED_TENANT_ID", ""))
    if bad:
        print(f"::error::{bad}")
        return 1

    status, body = call("PUT", f"{base}/api/v1/automations/manifests/{repo}", manifest, token)
    if status in (200, 201, 204):
        print(f"registry accepted manifest for {repo}: {len(manifest['automations'])} automations "
              f"(HTTP {status}, tenant {tenant or 'unknown'})")
        return 0
    if status in (400, 422):
        print(f"::error::registry rejected the manifest (HTTP {status}): {str(body)[:600]}")
        return 1
    print(f"::warning::automation registry sync skipped (HTTP {status}): {str(body)[:300]}. "
          "Expected while the registry endpoint is not deployed.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
