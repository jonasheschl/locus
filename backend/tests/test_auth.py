import base64
import json
from pathlib import Path

import httpx
import pytest

from app.auth import CLIENT_ID, CODEX_USAGE_URL, CodexAuth
from app.database import Database


def access_token(account_id: str, email: str = "reader@example.com") -> str:
    def encode(value: dict) -> str:
        return base64.urlsafe_b64encode(json.dumps(value).encode()).decode().rstrip("=")

    return f"{encode({'alg': 'none'})}.{encode({'https://api.openai.com/auth': {'chatgpt_account_id': account_id}, 'email': email})}.signature"


@pytest.mark.asyncio
async def test_device_login_is_stored_and_redacted(tmp_path: Path) -> None:
    token = access_token("account-123456")

    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path.endswith("/usercode"):
            assert json.loads(request.content) == {"client_id": CLIENT_ID}
            return httpx.Response(
                200,
                json={"device_auth_id": "device-id", "user_code": "ABCD-EFGH", "interval": 0},
            )
        if request.url.path.endswith("/deviceauth/token"):
            return httpx.Response(
                200,
                json={"authorization_code": "authorization-code", "code_verifier": "verifier"},
            )
        if request.url.path.endswith("/oauth/token"):
            assert b"client_id=" in request.content
            return httpx.Response(
                200,
                json={"access_token": token, "refresh_token": "refresh-secret", "expires_in": 3600},
            )
        raise AssertionError(f"Unexpected URL: {request.url}")

    database = Database(tmp_path / "wiki.sqlite3")
    database.initialize()
    client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    auth = CodexAuth(database, client)

    flow = await auth.start_device_login()
    result = await auth.poll_device_login(flow["flow_id"])

    assert result["status"] == "complete"
    assert result["authenticated"] is True
    assert result["account_id_suffix"] == "123456"
    assert "access_token" not in result
    stored = await auth.valid_credential()
    assert stored["access_token"] == token
    auth.logout()
    assert auth.status()["authenticated"] is False
    await client.aclose()


@pytest.mark.asyncio
async def test_codex_usage_is_normalized_and_credentials_are_not_returned(
    tmp_path: Path,
) -> None:
    token = access_token("account-usage-123456")

    def handler(request: httpx.Request) -> httpx.Response:
        assert str(request.url) == CODEX_USAGE_URL
        assert request.headers["authorization"] == f"Bearer {token}"
        assert request.headers["chatgpt-account-id"] == "account-usage-123456"
        return httpx.Response(
            200,
            json={
                "plan_type": "plus",
                "rate_limit": {
                    "allowed": True,
                    "limit_reached": False,
                    "primary_window": {
                        "used_percent": 23,
                        "limit_window_seconds": 18_000,
                        "reset_at": 1_800_000_000,
                    },
                    "secondary_window": {
                        "used_percent": 41.5,
                        "limit_window_seconds": 604_800,
                        "reset_at": 1_800_500_000,
                    },
                },
                "credits": {"has_credits": True, "unlimited": False, "balance": "8.25"},
                "rate_limit_reset_credits": {"available_count": 2},
            },
        )

    database = Database(tmp_path / "wiki.sqlite3")
    database.initialize()
    client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    auth = CodexAuth(database, client)
    auth._store_token(
        {
            "provider": "openai-codex",
            "access_token": token,
            "refresh_token": "refresh-secret",
            "expires_at": 4_000_000_000_000,
            "account_id": "account-usage-123456",
            "account_label": "reader@example.com",
        }
    )

    usage = await auth.usage()

    assert usage["available"] is True
    assert usage["plan_type"] == "plus"
    assert usage["limits"][0]["windows"][0] == {
        "used_percent": 23.0,
        "window_minutes": 300,
        "resets_at": 1_800_000_000,
    }
    assert usage["credits"]["balance"] == "8.25"
    assert usage["reset_credits_available"] == 2
    assert "access_token" not in json.dumps(usage)
    assert "account-usage-123456" not in json.dumps(usage)
    await client.aclose()
