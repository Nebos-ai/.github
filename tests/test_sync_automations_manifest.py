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
    """key value -> (status, tenant). Records exchange order and PUT calls."""

    def __init__(self, keys: dict[str, tuple[int, str | None]], put_status: int = 200) -> None:
        self.keys = keys
        self.put_status = put_status
        self.exchanged: list[str] = []
        self.puts = 0

    def __call__(self, method, url, body=None, token=None):
        if method == "POST":
            key = body["key"]
            self.exchanged.append(key)
            status, tenant = self.keys.get(key, (401, None))
            if status != 200:
                return status, {"detail": "no"}
            claims = {"sub": "sa"} if tenant is None else {"sub": "sa", "tenant_id": tenant}
            return 200, {"data": {"access_token": make_jwt(claims)}}
        self.puts += 1
        return self.put_status, {}


def run_main(backend: FakeBackend, env: dict[str, str]) -> tuple[int, str]:
    with tempfile.TemporaryDirectory() as d:
        mpath = Path(d) / "m.json"
        mpath.write_text(json.dumps({"automations": [{"slug": "x.y"}]}))
        full = {"MANIFEST_JSON": str(mpath), "REPO_NAME": "r", "NEBOS_BACKEND_URL": BASE, **env}
        out = io.StringIO()
        with mock.patch.dict(os.environ, full, clear=True), mock.patch.object(sync, "call", backend), \
                contextlib.redirect_stdout(out):
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

    def test_registry_not_deployed_after_auth_stays_soft(self) -> None:
        code, _ = run_main(FakeBackend({"kb": (200, OP)}, put_status=404), {"KB": "kb"})
        self.assertEqual(code, 0)

    def test_manifest_rejected_fails(self) -> None:
        code, _ = run_main(FakeBackend({"kb": (200, OP)}, put_status=422), {"KB": "kb"})
        self.assertEqual(code, 1)


class ExpectedTenant(unittest.TestCase):
    def test_jwt_tenant_reads_claim_and_tolerates_junk(self) -> None:
        self.assertEqual(sync.jwt_tenant(make_jwt({"tenant_id": OP})), OP)
        self.assertIsNone(sync.jwt_tenant("not-a-jwt"))
        self.assertIsNone(sync.jwt_tenant(make_jwt({"sub": "x"})))

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
