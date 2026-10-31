"""Fixtures for scripts/sync_automations_manifest.py (AUT-70). Standard library only, no network, no secrets."""

from __future__ import annotations

import base64
import contextlib
import io
import json
import os
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "scripts"))

import sync_automations_manifest as sync  # noqa: E402

OP = "00000000-0000-4000-8000-000000000010"
OTHER = "11111111-1111-4111-8111-111111111111"
BASE = "https://backend.example"


def make_jwt(claims: dict) -> str:
    enc = lambda d: base64.urlsafe_b64encode(json.dumps(d).encode()).decode().rstrip("=")  # noqa: E731
    return f"{enc({'alg': 'none'})}.{enc(claims)}.sig"


class FakeBackend:
    """key value -> (status, tenant). Records exchange order and PUT calls.

    put_status is one status, or a list answered in turn (the last one repeats). A 2xx PUT answers
    put_body, by default the registry's real reply for the one-row manifest run_main writes.
    """

    def __init__(self, keys: dict[str, tuple[int, str | None]], put_status: int | list[int] = 200,
                 claim: str = "tenant_id", put_body: object = None) -> None:
        self.claim = claim
        self.keys = keys
        self.put_statuses = put_status if isinstance(put_status, list) else [put_status]
        self.put_body = put_body if put_body is not None else {
            "repo": "r", "created": 1, "updated": 0, "needs_review": []}
        self.exchanged: list[str] = []
        self.puts = 0

    def __call__(self, method, url, body=None, token=None):
        if method == "POST":
            key = body["key"]
            self.exchanged.append(key)
            status, tenant = self.keys.get(key, (401, None))
            if status != 200:
                return status, {"detail": "no"}
            claims = {"sub": "sa"} if tenant is None else {"sub": "sa", self.claim: tenant}
            return 200, {"data": {"access_token": make_jwt(claims)}}
        status = self.put_statuses[min(self.puts, len(self.put_statuses) - 1)]
        self.puts += 1
        return status, (self.put_body if 200 <= status < 300 else {"detail": "no"})


def run_main(backend: FakeBackend, env: dict[str, str]) -> tuple[int, str]:
    with tempfile.TemporaryDirectory() as d:
        mpath = Path(d) / "m.json"
        mpath.write_text(json.dumps({"automations": [{"slug": "x.y"}]}))
        full = {"MANIFEST_JSON": str(mpath), "REPO_NAME": "r", "NEBOS_BACKEND_URL": BASE, **env}
        out = io.StringIO()
        with mock.patch.dict(os.environ, full, clear=True), mock.patch.object(sync, "call", backend), \
                mock.patch.object(sync.time, "sleep"), contextlib.redirect_stdout(out):
            code = sync.main()
    return code, out.getvalue()


class SlotOrder(unittest.TestCase):
    def test_backend_key_is_first_and_old_slots_kept(self) -> None:
        self.assertEqual([v for v, _ in sync.SLOTS], ["KB", "K1", "K2", "K3", "K4"])
        self.assertEqual(sync.SLOTS[0][1], "NEBOS_BACKEND_SERVICE_KEY")

    def test_backend_key_tried_first_and_stops_on_success(self) -> None:
        be = FakeBackend({"kb": (200, OP), "k1": (200, OTHER)})
        code, out = run_main(be, {"KB": "kb", "K1": "k1"})
        self.assertEqual(code, 0)
        self.assertEqual(be.exchanged, ["kb"])
        self.assertIn(f"tenant {OP}", out)

    def test_falls_through_to_later_slot(self) -> None:
        be = FakeBackend({"k4": (200, OP)})
        code, _ = run_main(be, {"K1": "k1", "K4": "k4"})
        self.assertEqual(code, 0)
        self.assertEqual(be.exchanged, ["k1", "k4"])


class FailRed(unittest.TestCase):
    def test_no_slot_exchanges_fails_red_naming_task_and_statuses(self) -> None:
        be = FakeBackend({})
        code, out = run_main(be, {"K1": "k1", "K2": "k2"})
        self.assertEqual(code, 1)
        self.assertEqual(be.puts, 0)
        self.assertIn("::error::", out)
        self.assertIn("AUT-70", out)
        self.assertIn("NEBOS_SERVICE_KEY (K1): HTTP 401", out)
        self.assertIn("NEBOS_PROJECTS_SERVICE_KEY (K2): HTTP 401", out)
        self.assertIn("NEBOS_BACKEND_SERVICE_KEY (KB): not set", out)

    def test_no_keys_at_all_fails_red(self) -> None:
        code, out = run_main(FakeBackend({}), {})
        self.assertEqual(code, 1)
        self.assertIn("AUT-70", out)

    def test_secret_values_never_printed(self) -> None:
        _, out = run_main(FakeBackend({}), {"K1": "super-secret-value"})
        self.assertNotIn("super-secret-value", out)

    def test_manifest_rejected_fails(self) -> None:
        code, _ = run_main(FakeBackend({"kb": (200, OP)}, put_status=422), {"KB": "kb"})
        self.assertEqual(code, 1)


class RegistryAnswer(unittest.TestCase):
    """AUT-76: after auth, only a 2xx confirming every manifest row passes."""

    def test_404_fails_red_without_retry(self) -> None:
        be = FakeBackend({"kb": (200, OP)}, put_status=404)
        code, out = run_main(be, {"KB": "kb"})
        self.assertEqual(code, 1)
        self.assertEqual(be.puts, 1)
        self.assertIn("::error::AUT-76", out)
        self.assertIn("HTTP 404", out)

    def test_5xx_retried_then_fails_red(self) -> None:
        be = FakeBackend({"kb": (200, OP)}, put_status=503)
        code, out = run_main(be, {"KB": "kb"})
        self.assertEqual(code, 1)
        self.assertEqual(be.puts, 3)
        self.assertIn("::error::AUT-76", out)

    def test_unreachable_retried_then_fails_red(self) -> None:
        be = FakeBackend({"kb": (200, OP)}, put_status=0)
        code, out = run_main(be, {"KB": "kb"})
        self.assertEqual(code, 1)
        self.assertEqual(be.puts, 3)
        self.assertIn("unreachable", out)

    def test_5xx_then_success_passes(self) -> None:
        be = FakeBackend({"kb": (200, OP)}, put_status=[502, 200])
        code, out = run_main(be, {"KB": "kb"})
        self.assertEqual(code, 0)
        self.assertEqual(be.puts, 2)
        self.assertNotIn("::error::", out)

    def test_full_count_passes_and_logs_it(self) -> None:
        code, out = run_main(FakeBackend({"kb": (200, OP)}), {"KB": "kb"})
        self.assertEqual(code, 0)
        self.assertIn("1 of 1 automations (created 1, updated 0", out)

    def test_shortfall_fails_red(self) -> None:
        body = {"repo": "r", "created": 0, "updated": 0, "needs_review": []}
        code, out = run_main(FakeBackend({"kb": (200, OP)}, put_body=body), {"KB": "kb"})
        self.assertEqual(code, 1)
        self.assertIn("accepted 0 of 1 manifest rows", out)

    def test_reply_without_counts_fails_red(self) -> None:
        code, out = run_main(FakeBackend({"kb": (200, OP)}, put_body={"ok": True}), {"KB": "kb"})
        self.assertEqual(code, 1)
        self.assertIn("without created/updated counts", out)

    def test_needs_review_rows_are_named_and_pass(self) -> None:
        body = {"repo": "r", "created": 0, "updated": 1, "needs_review": ["r.retired-job"]}
        code, out = run_main(FakeBackend({"kb": (200, OP)}, put_body=body), {"KB": "kb"})
        self.assertEqual(code, 0)
        self.assertIn("r.retired-job", out)


class ExpectedTenant(unittest.TestCase):
    def test_jwt_tenant_reads_claim_and_tolerates_junk(self) -> None:
        self.assertEqual(sync.jwt_tenant(make_jwt({"tenant_id": OP})), OP)
        self.assertIsNone(sync.jwt_tenant("not-a-jwt"))
        self.assertIsNone(sync.jwt_tenant(make_jwt({"sub": "x"})))

    def test_real_backend_claim_shape_org_id(self) -> None:
        claims = {"sub": "service:abc", "org_id": OP, "name": "sync", "scopes": ["automations:write"],
                  "token_use": "service", "iss": "nebos", "aud": "nebos", "iat": 1, "exp": 2, "jti": "j"}
        self.assertEqual(sync.jwt_tenant(make_jwt(claims)), OP)
        # tid is a token id in this codebase, never a tenant
        self.assertIsNone(sync.jwt_tenant(make_jwt({"tid": OP})))

    def test_org_id_token_logs_tenant_and_passes_expected(self) -> None:
        be = FakeBackend({"kb": (200, OP)}, claim="org_id")
        code, out = run_main(be, {"KB": "kb", "EXPECTED_TENANT_ID": OP})
        self.assertEqual(code, 0)
        self.assertIn(f"is for tenant {OP}", out)

    def test_mismatch_fails_before_any_write(self) -> None:
        be = FakeBackend({"k4": (200, OTHER)})
        code, out = run_main(be, {"K4": "k4", "EXPECTED_TENANT_ID": OP})
        self.assertEqual(code, 1)
        self.assertEqual(be.puts, 0)
        self.assertIn(f"token is for tenant {OTHER}, expected {OP}", out)

    def test_match_passes(self) -> None:
        be = FakeBackend({"kb": (200, OP)})
        code, _ = run_main(be, {"KB": "kb", "EXPECTED_TENANT_ID": OP})
        self.assertEqual(code, 0)
        self.assertEqual(be.puts, 1)

    def test_unknown_tenant_with_expectation_fails(self) -> None:
        code, _ = run_main(FakeBackend({"kb": (200, None)}), {"KB": "kb", "EXPECTED_TENANT_ID": OP})
        self.assertEqual(code, 1)

    def test_unset_expectation_does_not_block(self) -> None:
        code, _ = run_main(FakeBackend({"kb": (200, OTHER)}), {"KB": "kb"})
        self.assertEqual(code, 0)


if __name__ == "__main__":
    unittest.main()
