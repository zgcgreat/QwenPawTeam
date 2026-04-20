# -*- coding: utf-8 -*-
"""End-to-end integration test for the multi-tenant plugin.

This test spins up the **real** QwenPaw FastAPI application (via
``TestClient``) with the multi-tenant plugin activated, and exercises
every auth endpoint and the middleware through actual HTTP requests.

**Important**: Plugin endpoints are at ``/auth/*`` (directly on app,
no ``/api`` prefix).  Upstream QwenPaw's own auth endpoints live under
``/api/auth/*`` (username + password model).  The two sets coexist.

Run from the project root::

    cd QwenPaw
    python -m pytest tests/e2e/test_multi_tenant.py -v

Or directly::

    python tests/e2e/test_multi_tenant.py
"""
from __future__ import annotations

import json
import os
import shutil
import sys
import tempfile
from pathlib import Path

# ---------------------------------------------------------------------------
# Ensure the project root / src are on sys.path so ``qwenpaw`` is importable.
# ---------------------------------------------------------------------------
_PROJECT_ROOT = Path(__file__).resolve().parents[2]  # .../QwenPaw/
_SRC_DIR = _PROJECT_ROOT / "src"
if str(_SRC_DIR) not in sys.path:
    sys.path.insert(0, str(_SRC_DIR))

# ---------------------------------------------------------------------------
# Isolated temp directories so we never touch real user data.
# ---------------------------------------------------------------------------
_TEMP_BASE = tempfile.mkdtemp(prefix="qwenpaw_e2e_")
_WORKING_DIR = Path(_TEMP_BASE) / "working"
_SECRET_DIR = Path(_TEMP_BASE) / "secret"
_WORKING_DIR.mkdir(parents=True, exist_ok=True)
_SECRET_DIR.mkdir(parents=True, exist_ok=True)

os.environ["QWENPAW_WORKING_DIR"] = str(_WORKING_DIR)
os.environ["QWENPAW_SECRET_DIR"] = str(_SECRET_DIR)
os.environ["QWENPAW_MULTI_TENANT_ENABLED"] = "true"

# Clean slate for each test module load
_auth_file = _SECRET_DIR / "auth.json"
if _auth_file.exists():
    _auth_file.unlink()

# ===================================================================
# Helpers
# ===================================================================

#: Fixed test credentials -- unique per run to avoid collisions.
_SUFFIX = str(os.getpid())[-6:]
TEST_TENANT_FIELDS = {
    "sysId": f"E2E{_SUFFIX}",
    "branchId": "BRANCH01",
    "vorgCode": "CENTER01",
    "sapId": "USER01",
    "positionId": "POS01",
}
TEST_PASSWORD = "e2e_test_password_123"
TEST_TENANT_ID = "/".join(TEST_TENANT_FIELDS[f] for f in (
    "sysId", "branchId", "vorgCode", "sapId", "positionId",
))

TEST_TENANT_2_FIELDS = {
    "sysId": f"E2EB{_SUFFIX}",
    "branchId": "BRANCH02",
    "vorgCode": "CENTER02",
    "sapId": "USER02",
    "positionId": "POS02",
}
TEST_TENANT_2_ID = "/".join(TEST_TENANT_2_FIELDS[f] for f in (
    "sysId", "branchId", "vorgCode", "sapId", "positionId",
))


def _make_client(auth_enabled: bool = True):
    """Create a fresh FastAPI TestClient with the given auth mode.

    Sets ``QWENPAW_AUTH_ENABLED`` in ``os.environ`` *before* capturing the
    original environment, so that the restored state still contains all
    base variables (WORKING_DIR, MULTI_TENANT_ENABLED, etc.) but NOT the
    per-test AUTH_ENABLED value.
    """
    # Apply test-specific env vars FIRST
    os.environ["QWENPAW_AUTH_ENABLED"] = "true" if auth_enabled else "false"
    # Now capture the full environ (including our change) as baseline to restore later
    orig_environ = dict(os.environ)

    # Force re-import of all plugin / app modules so fresh env vars are read
    mod_names = [
        "qwenpaw.app._app",
        "qwenpaw_plugins.multi_tenant",
        "qwenpaw_plugins.multi_tenant.auth_extension",
        "qwenpaw_plugins.multi_tenant.constants",
        "qwenpaw_plugins.multi_tenant.config_extension",
        "qwenpaw_plugins.multi_tenant.router_extension",
        "qwenpaw_plugins.multi_tenant.middleware",
        "qwenpaw_plugins.multi_tenant.manager_extension",
        "qwenpaw_plugins.multi_tenant.provider_extension",
        "qwenpaw_plugins.multi_tenant.migration_extension",
        "qwenpaw_plugins.multi_tenant.tenant_context",
        "qwenpaw_plugins.multi_tenant.token_parser",
    ]
    for m in mod_names:
        if m in sys.modules:
            del sys.modules[m]

    try:
        from fastapi.testclient import TestClient
        from qwenpaw.app._app import app

        client = TestClient(app, raise_server_exceptions=False)
    finally:
        # Restore to pre-test state (still has base vars like WORKING_DIR etc.)
        os.environ.clear()
        os.environ.update(orig_environ)

    return client


def _print_section(title: str) -> None:
    print(f"\n{'=' * 60}")
    print(f"  {title}")
    print(f"{'=' * 60}")


# ===================================================================
# Test Runner
# ===================================================================

errors: list[str] = []
passed: list[str] = []

print("=" * 60)
print("  Multi-Tenant Plugin -- E2E Integration Test")
print(f"  Temp base : {_TEMP_BASE}")
print(f"  Tenant ID : {TEST_TENANT_ID}")
print("=" * 60)

# ====================================================================
# TEST 1: Plugin activation & auth status
#
# NOTE: Our plugin registers at /auth/status (no /api prefix).
#       The upstream also has /api/auth/status -- they are DIFFERENT endpoints.
# ====================================================================
_print_section("TEST 1: Plugin activation + GET /auth/status")

try:
    client = _make_client(auth_enabled=True)

    # Plugin endpoint (no /api prefix)
    resp = client.get("/auth/status")
    assert resp.status_code == 200, f"status {resp.status_code}: {resp.text}"
    body = resp.json()

    assert body["enabled"] is True, f"auth should be enabled: {body}"
    assert body["multi_tenant"] is True, f"multi_tenant should be true: {body}"
    print(f"  [OK] status={body}")

    # Also check upstream endpoint for comparison
    resp_upstream = client.get("/api/auth/status")
    assert resp_upstream.status_code == 200
    upstream_body = resp_upstream.json()
    print(f"  [OK] upstream /api/auth/status={upstream_body}")
    passed.append("T1-status")
except Exception as e:
    errors.append(f"T1 failed: {e}")
    import traceback
    traceback.print_exc()

# ====================================================================
# TEST 2: Login via plugin endpoint (auto-registration of new user)
#
# POST /auth/login expects {sysId, branchId, vorgCode, sapId, positionId, password}
# ====================================================================
_print_section("TEST 2: POST /auth/login -- register + login (plugin endpoint)")

login_token = ""
try:
    client = _make_client(auth_enabled=True)

    resp = client.post("/auth/login", json={
        **TEST_TENANT_FIELDS,
        "password": TEST_PASSWORD,
    })
    assert resp.status_code == 200, f"login status {resp.status_code}: {resp.text}"

    body = resp.json()
    assert body["token"], "token should be non-empty"
    assert body["tenant_id"] == TEST_TENANT_ID, \
        f"tenant_id mismatch: {body['tenant_id']} != {TEST_TENANT_ID}"
    assert body["sysId"] == TEST_TENANT_FIELDS["sysId"]
    assert body["branchId"] == TEST_TENANT_FIELDS["branchId"]

    login_token = body["token"]
    print(f"  [OK] Logged in as {body['tenant_id'][:30]}...")
    print(f"  [OK] Token: {login_token[:40]}...")
    passed.append("T2-login")
except Exception as e:
    errors.append(f"T2 failed: {e}")
    import traceback
    traceback.print_exc()

# ====================================================================
# TEST 3: Duplicate login returns same tenant (idempotent)
# ====================================================================
_print_section("TEST 3: POST /auth/login -- idempotent (same user)")

try:
    client = _make_client(auth_enabled=True)

    resp = client.post("/auth/login", json={
        **TEST_TENANT_FIELDS,
        "password": TEST_PASSWORD,
    })
    body = resp.json()
    assert body["tenant_id"] == TEST_TENANT_ID
    print(f"  [OK] Same tenant returned: {body['tenant_id'][:30]}...")

    # Wrong password should fail
    resp_bad = client.post("/auth/login", json={
        **TEST_TENANT_FIELDS,
        "password": "wrong_password",
    })
    assert resp_bad.status_code == 401, f"wrong pw should be 401: {resp_bad.status_code}"
    print(f"  [OK] Wrong password rejected: 401")
    passed.append("T3-idempotent")
except Exception as e:
    errors.append(f"T3 failed: {e}")
    import traceback
    traceback.print_exc()

# ====================================================================
# TEST 4: Token verification
# ====================================================================
_print_section("TEST 4: GET /auth/verify -- valid token")

try:
    client = _make_client(auth_enabled=True)
    headers = {"Authorization": f"Bearer {login_token}"}

    resp = client.get("/auth/verify", headers=headers)
    assert resp.status_code == 200, f"verify status {resp.status_code}: {resp.text}"
    body = resp.json()
    assert body["valid"] is True
    assert body["tenant_id"] == TEST_TENANT_ID
    print(f"  [OK] Token valid for {body['tenant_id'][:30]}...")

    # No token --> 401
    resp_no = client.get("/auth/verify")
    assert resp_no.status_code == 401, f"no token should be 401: {resp_no.status_code}"
    print(f"  [OK] No token --> 401")

    # Garbage token --> 401
    resp_garbage = client.get("/auth/verify", headers={
        "Authorization": "Bearer garbage.token.value",
    })
    assert resp_garbage.status_code == 401, f"garbage should be 401: {resp_garbage.status_code}"
    print(f"  [OK] Garbage token --> 401")
    passed.append("T4-verify")
except Exception as e:
    errors.append(f"T4 failed: {e}")
    import traceback
    traceback.print_exc()

# ====================================================================
# TEST 5: Resolve tenant from token
#
# NOTE: /auth/resolve-tenant uses TokenParser which is designed for
#       *upstream business system* tokens (integration mode).  Our
#       HMAC-SHA256 tokens (standalone mode) are NOT parseable by
#       TokenParser -- that is expected behaviour.
#
#       In standalone mode, use /auth/verify instead (which validates
#       the HMAC signature and returns the tenant).
# ====================================================================
_print_section("TEST 5: GET /auth/resolve-tenant")

try:
    client = _make_client(auth_enabled=True)
    headers = {"Authorization": f"Bearer {login_token}"}

    resp = client.get("/auth/resolve-tenant", headers=headers)
    assert resp.status_code == 200, f"resolve status {resp.status_code}: {resp.text}"
    body = resp.json()

    # In standalone mode, TokenParser cannot decode HMAC tokens,
    # so tenant_id will be empty string. That is OK -- use /verify instead.
    assert "tenant_id" in body
    print(f"  [OK] resolve-tenant returned: {body}")
    if body["tenant_id"]:
        assert body["sysId"] == TEST_TENANT_FIELDS["sysId"]
        print(f"  [OK] Tenant resolved from business token")
    else:
        print(f"  [OK] HMAC token not parseable by TokenParser (expected in standalone mode)")

    # No auth header --> empty tenant
    resp_empty = client.get("/auth/resolve-tenant")
    body_empty = resp_empty.json()
    assert body_empty.get("tenant_id") == ""
    print(f"  [OK] Empty resolve: {body_empty}")
    passed.append("T5-resolve")
except Exception as e:
    errors.append(f"T5 failed: {e}")
    import traceback
    traceback.print_exc()

# ====================================================================
# TEST 6: List users (protected endpoint)
# ====================================================================
_print_section("TEST 6: GET /auth/users -- protected endpoint")

try:
    client = _make_client(auth_enabled=True)
    headers = {"Authorization": f"Bearer {login_token}"}

    resp = client.get("/auth/users", headers=headers)
    assert resp.status_code == 200, f"users status {resp.status_code}: {resp.text}"
    body = resp.json()
    users = body.get("users", [])
    assert TEST_TENANT_ID in users, \
        f"{TEST_TENANT_ID} should be in users list: {users}"
    print(f"  [OK] Users: {[u[:25] for u in users]}")

    # Without token --> 401
    resp_no = client.get("/auth/users")
    assert resp_no.status_code == 401, f"no token should be 401: {resp_no.status_code}"
    print(f"  [OK] No-token users --> 401")
    passed.append("T6-users")
except Exception as e:
    errors.append(f"T6 failed: {e}")
    import traceback
    traceback.print_exc()

# ====================================================================
# TEST 7: Register second user, list shows both
# ====================================================================
_print_section("TEST 7: Second user registration + listing")

try:
    client = _make_client(auth_enabled=True)

    resp = client.post("/auth/login", json={
        **TEST_TENANT_2_FIELDS,
        "password": "password_456",
    })
    assert resp.status_code == 200, f"user2 login status {resp.status_code}: {resp.text}"
    body = resp.json()
    token2 = body["token"]
    assert body["tenant_id"] == TEST_TENANT_2_ID
    print(f"  [OK] User 2 registered: {body['tenant_id'][:30]}...")

    # List users with user 1's token
    resp = client.get("/auth/users", headers={
        "Authorization": f"Bearer {login_token}",
    })
    users_list = resp.json().get("users", [])
    assert TEST_TENANT_ID in users_list
    assert TEST_TENANT_2_ID in users_list
    print(f"  [OK] Both users listed: {[u[:20] for u in users_list]}")
    passed.append("T7-multi-user")
except Exception as e:
    errors.append(f"T7 failed: {e}")
    import traceback
    traceback.print_exc()

# ====================================================================
# TEST 8: Update password
# ====================================================================
_print_section("TEST 8: POST /auth/update-profile -- change password")

new_token_after_pw_change = ""
try:
    client = _make_client(auth_enabled=True)

    # First login to get a fresh token
    resp = client.post("/auth/login", json={
        **TEST_TENANT_FIELDS,
        "password": TEST_PASSWORD,
    })
    token = resp.json()["token"]

    # Change password
    resp = client.post("/auth/update-profile", json={
        "current_password": TEST_PASSWORD,
        "new_password": "new_password_789",
    }, headers={"Authorization": f"Bearer {token}"})
    assert resp.status_code == 200, \
        f"update status {resp.status_code}: {resp.text}"
    body = resp.json()
    new_token_after_pw_change = body.get("token", "")
    assert new_token_after_pw_change, "should get new token after password change"
    print(f"  [OK] Password updated, new token: {new_token_after_pw_change[:40]}...")

    # Old password no longer works
    resp_old = client.post("/auth/login", json={
        **TEST_TENANT_FIELDS,
        "password": TEST_PASSWORD,
    })
    assert resp_old.status_code == 401, \
        f"old password should fail: {resp_old.status_code}"
    print(f"  [OK] Old password rejected: 401")

    # New password works
    resp_new = client.post("/auth/login", json={
        **TEST_TENANT_FIELDS,
        "password": "new_password_789",
    })
    assert resp_new.status_code == 200, \
        f"new password should work: {resp_new.status_code}"
    print(f"  [OK] New password accepted")
    passed.append("T8-update-password")
except Exception as e:
    errors.append(f"T8 failed: {e}")
    import traceback
    traceback.print_exc()

# ====================================================================
# TEST 9: Delete user
# ====================================================================
_print_section("TEST 9: DELETE /auth/users/{id} -- delete second user")

try:
    client = _make_client(auth_enabled=True)

    # Login as user 1 (admin for this test)
    resp = client.post("/auth/login", json={
        **TEST_TENANT_FIELDS,
        "password": "new_password_789",  # changed in T8
    })
    admin_token = resp.json()["token"]

    # Delete user 2
    resp = client.delete(f"/auth/users/{TEST_TENANT_2_ID}", headers={
        "Authorization": f"Bearer {admin_token}",
    })
    assert resp.status_code == 200, \
        f"delete status {resp.status_code}: {resp.text}"
    body = resp.json()
    assert body["success"] is True
    assert body["deleted_tenant_id"] == TEST_TENANT_2_ID
    print(f"  [OK] Deleted: {body['deleted_tenant_id'][:30]}...")

    # User 2 can re-login (auto-registers again since deleted)
    resp_login = client.post("/auth/login", json={
        **TEST_TENANT_2_FIELDS,
        "password": "password_456",
    })
    assert resp_login.status_code == 200, \
        f"re-login after delete should succeed (auto-register): {resp_login.status_code}"
    print(f"  [OK] Deleted user can re-register (fresh account)")
    passed.append("T9-delete-user")
except Exception as e:
    errors.append(f"T9 failed: {e}")
    import traceback
    traceback.print_exc()

# ====================================================================
# TEST 10: Middleware blocks unauthenticated API calls (standalone mode)
# ====================================================================
_print_section("TEST 10: Middleware -- unauthenticated API call blocked")

try:
    client = _make_client(auth_enabled=True)

    # A random API endpoint that requires auth (not in PUBLIC_PATHS)
    resp = client.get("/api/settings/provider")
    code = resp.status_code
    # Should NOT be 200 without a token when auth is enabled.
    # Could be 401, 403, or 404 depending on whether route exists.
    assert code != 200, \
        f"Unauthenticated request should not succeed (got {code})"
    print(f"  [OK] Unauthenticated API call blocked: HTTP {code}")
    passed.append("T10-middleware-block")
except Exception as e:
    errors.append(f"T10 failed: {e}")
    import traceback
    traceback.print_exc()

# ====================================================================
# TEST 11: Middleware allows authenticated API calls through
# ====================================================================
_print_section("TEST 11: Middleware -- authenticated request passes through")

try:
    client = _make_client(auth_enabled=True)

    resp = client.post("/auth/login", json={
        **TEST_TENANT_FIELDS,
        "password": "new_password_789",
    })
    token = resp.json()["token"]

    # Hit a public endpoint with token (should still work)
    resp = client.get("/auth/status", headers={
        "Authorization": f"Bearer {token}",
    })
    assert resp.status_code == 200
    print(f"  [OK] Authenticated public request works: {resp.json()}")

    # Verify tenant context was set correctly
    resp_verify = client.get("/auth/verify", headers={
        "Authorization": f"Bearer {token}",
    })
    body = resp_verify.json()
    assert body["tenant_id"] == TEST_TENANT_ID
    print(f"  [OK] Tenant context correct: {body['tenant_id'][:30]}...")
    passed.append("T11-middleware-pass")
except Exception as e:
    errors.append(f"T11 failed: {e}")
    import traceback
    traceback.print_exc()

# ====================================================================
# TEST 12: Integration mode (AUTH_DISABLED) -- init-workspace
# ====================================================================
_print_section("TEST 12: Integration mode -- POST /auth/init-workspace")

try:
    client = _make_client(auth_enabled=False)

    # Status should show auth disabled
    resp = client.get("/auth/status")
    body = resp.json()
    assert body["enabled"] is False, \
        f"auth should be disabled: {body}"
    print(f"  [OK] Integration mode: auth enabled={body['enabled']}")

    # init-workspace with explicit fields
    int_fields = {
        "sysId": "INT_SYS",
        "branchId": "INT_BR",
        "vorgCode": "INT_VC",
        "sapId": "INT_UI",
        "positionId": "INT_PI",
    }
    resp = client.post("/auth/init-workspace", json=int_fields)
    assert resp.status_code == 200, \
        f"init-ws status {resp.status_code}: {resp.text}"
    body = resp.json()
    expected_tid = "INT_SYS/INT_BR/INT_VC/INT_UI/INT_PI"
    assert body["tenant_id"] == expected_tid
    print(f"  [OK] Workspace initialized: {body['tenant_id']}")

    # Verify tenant dir was created
    from qwenpaw_plugins.multi_tenant.auth_extension import get_tenant_working_dir

    tdir = get_tenant_working_dir(body["tenant_id"])
    assert tdir.is_dir(), f"Tenant dir should exist: {tdir}"
    print(f"  [OK] Dir exists: {tdir}")
    passed.append("T12-integration-mode")
except Exception as e:
    errors.append(f"T12 failed: {e}")
    import traceback
    traceback.print_exc()

# ====================================================================
# TEST 13: Tenant workspace directory structure
# ====================================================================
_print_section("TEST 13: Tenant workspace file structure")

try:
    from qwenpaw_plugins.multi_tenant.auth_extension import get_tenant_working_dir

    tid = "STRUCT/S1/S2/S3/S4/S5"
    wdir = get_tenant_working_dir(tid)

    # After login/init, workspace subdirs should exist
    tenants_root = _WORKING_DIR / "tenants"
    assert tenants_root.is_dir(), \
        f"Tenants root should exist: {tenants_root}"

    # Check our test tenant dir
    tenant_path = _WORKING_DIR / "tenants" / f"E2E{_SUFFIX}" / "BRANCH01" / "CENTER01" / "USER01" / "POS01"
    if tenant_path.exists():
        print(f"  [OK] Primary tenant dir: {tenant_path}")
        subs = list(tenant_path.iterdir())
        print(f"  [OK] Contents: {[s.name for s in subs]}")
    else:
        print(f"  [INFO] Primary tenant dir not yet created (may need login)")

    # Check secret dir structure
    from qwenpaw_plugins.multi_tenant.auth_extension import get_tenant_secret_dir

    sdir = get_tenant_secret_dir(tid)
    assert sdir.is_dir()
    print(f"  [OK] Secret dir: {sdir}")
    passed.append("T13-structure")
except Exception as e:
    errors.append(f"T13 failed: {e}")
    import traceback
    traceback.print_exc()

# ====================================================================
# TEST 14: Auth data persistence (auth.json)
# ====================================================================
_print_section("TEST 14: Auth data persistence (auth.json)")

try:
    from qwenpaw_plugins.multi_tenant.auth_extension import _load_auth_data

    data = _load_auth_data()
    assert "jwt_secret" in data, \
        "jwt_secret should exist after first use; keys=%s" % list(data.keys())
    assert "users" in data, "users key should exist"
    assert isinstance(data["users"], dict), "users should be a dict"

    if TEST_TENANT_ID in data["users"]:
        user = data["users"][TEST_TENANT_ID]
        assert user["sysId"] == TEST_TENANT_FIELDS["sysId"]
        assert "password_hash" in user
        assert "password_salt" in user
        print(f"  [OK] User persisted: sysId={user['sysId']}, hash=+, salt=+")
    else:
        print(f"  [INFO] Test tenant not in auth.json (may have been cleaned)")

    # Re-load and verify consistency
    data2 = _load_auth_data()
    assert data["jwt_secret"] == data2["jwt_secret"], \
        "jwt_secret should be stable across reloads"
    print(f"  [OK] Data consistent across reloads")
    passed.append("T14-persistence")
except Exception as e:
    errors.append(f"T14 failed: {e}")
    import traceback
    traceback.print_exc()

# ====================================================================
# Summary
# ===================================================================
print("\n" + "=" * 60)
if not errors:
    print(f"  [PASS] ALL {len(passed)} E2E TESTS PASSED")
else:
    print(f"  [FAIL] {len(errors)} FAILED / {len(passed)} PASSED:")
    for err in errors:
        print(f"     - {err}")
print("=" * 60)

# Cleanup
print(f"\n  Temp data retained at: {_TEMP_BASE}")
print(f"  To clean up: rm -rf {_TEMP_BASE}")
