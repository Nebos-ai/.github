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
- Once authenticated, anything short of every row landing FAILS (exit 1, AUT-76): a 5xx or network failure
  is retried twice (5 s, then 15 s) first; 404/405 fail at once; a 2xx whose created+updated differs from
  the manifest's row count fails. These used to warn and pass, so a sync that never landed showed green.
  A 400/422 means the manifest itself is wrong and exits 1.

Env: MANIFEST_JSON, REPO_NAME, NEBOS_BACKEND_URL (optional), EXPECTED_TENANT_ID (optional), KB, K1..K4.
No secret value is ever printed.
"""
from __future__ import annotations

import base64
import json
import os
import sys
import time
import urllib.error
import urllib.request

DEFAULT_BACKEND = "https://api.nebbos.ai"
TASK = "AUT-70"
SYNC_TASK = "AUT-76"
# Waits before the 2nd and 3rd PUT when the registry answers 5xx or cannot be reached.
RETRY_WAIT_SECONDS = (5, 15)

# (env var, secret name), tried in this order. The backend key goes first.
SLOTS: list[tuple[str, str]] = [
    ("KB", "NEBOS_BACKEND_SERVICE_KEY"),
    ("K1", "NEBOS_SERVICE_KEY"),
    ("K2", "NEBOS_PROJECTS_SERVICE_KEY"),
    ("K3", "NEBOS_SERVICE_TOKEN"),
    ("K4", "BILLING_SERVICE_ACCOUNT_KEY"),
]
# org_id is what create_service_jwt writes (nebos-backend api/service_token.py). "tid" is NOT a tenant claim here.
TENANT_CLAIMS = ("org_id", "tenant_id", "tenant")


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


def transient(status: int) -> bool:
    """A network failure (0) or a 5xx: worth another try."""
    return status == 0 or status >= 500


def put_manifest(url: str, manifest: object, token: str) -> tuple[int, object]:
    """PUT the manifest; a transient answer is retried, 1 + len(RETRY_WAIT_SECONDS) tries in all."""
    status, body = call("PUT", url, manifest, token)
    for attempt, wait in enumerate(RETRY_WAIT_SECONDS, start=1):
        if not transient(status):
            break
        got = "unreachable" if status == 0 else f"HTTP {status}"
        print(f"::notice::registry PUT got {got} on try {attempt}; retrying in {wait}s")
        time.sleep(wait)
        status, body = call("PUT", url, manifest, token)
    return status, body


def count_mismatch(body: object, expected: int) -> str | None:
    """Error text when the registry's created+updated is not the manifest's row count; else None."""
    created = body.get("created") if isinstance(body, dict) else None
    updated = body.get("updated") if isinstance(body, dict) else None
    if not isinstance(created, int) or not isinstance(updated, int):
        return (f"{SYNC_TASK}: registry answered 2xx without created/updated counts, so nothing shows the "
                f"{expected} manifest rows landed.")
    if created + updated != expected:
        return (f"{SYNC_TASK}: registry accepted {created + updated} of {expected} manifest rows "
                f"(created {created}, updated {updated}); the rest are not in the registry.")
    return None


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

    expected = len(manifest["automations"])
    status, body = put_manifest(f"{base}/api/v1/automations/manifests/{repo}", manifest, token)
    if 200 <= status < 300:
        bad = count_mismatch(body, expected)
        if bad:
            print(f"::error::{bad}")
            return 1
        assert isinstance(body, dict)
        print(f"registry accepted manifest for {repo}: {expected} of {expected} automations "
              f"(created {body['created']}, updated {body['updated']}, HTTP {status}, tenant {tenant or 'unknown'})")
        stale = body.get("needs_review") or []
        if stale:
            print(f"::notice::{len(stale)} registry rows of {repo} are no longer in the manifest and are now "
                  f"needs_review: {', '.join(map(str, stale[:20]))}")
        return 0
    if status in (400, 422):
        print(f"::error::registry rejected the manifest (HTTP {status}): {str(body)[:600]}")
        return 1
    got = "unreachable" if status == 0 else f"HTTP {status}"
    print(f"::error::{SYNC_TASK}: automation registry sync FAILED ({got}): {str(body)[:300]}. "
          f"The registry did not confirm the {expected} manifest rows.")
    return 1


if __name__ == "__main__":
    sys.exit(main())
