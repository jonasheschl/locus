from __future__ import annotations

import asyncio
import base64
import binascii
import json
import secrets
import time
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any

import httpx

from .database import Database


CLIENT_ID = "app_EMoamEEZ73f0CkXaXp7hrann"
AUTH_BASE_URL = "https://auth.openai.com"
DEVICE_USER_CODE_URL = f"{AUTH_BASE_URL}/api/accounts/deviceauth/usercode"
DEVICE_TOKEN_URL = f"{AUTH_BASE_URL}/api/accounts/deviceauth/token"
DEVICE_VERIFICATION_URI = f"{AUTH_BASE_URL}/codex/device"
DEVICE_REDIRECT_URI = f"{AUTH_BASE_URL}/deviceauth/callback"
TOKEN_URL = f"{AUTH_BASE_URL}/oauth/token"
CODEX_USAGE_URL = "https://chatgpt.com/backend-api/wham/usage"
JWT_CLAIM_PATH = "https://api.openai.com/auth"
PROVIDER = "openai-codex"
DEVICE_TIMEOUT_SECONDS = 15 * 60


class AuthError(RuntimeError):
    pass


@dataclass
class DeviceFlow:
    device_auth_id: str
    user_code: str
    interval_seconds: float
    created_at: float
    next_poll_at: float


def _utc_now() -> str:
    return datetime.now(UTC).isoformat()


def _decode_jwt(token: str) -> dict[str, Any]:
    try:
        parts = token.split(".")
        if len(parts) != 3:
            raise ValueError("not a JWT")
        payload = parts[1]
        payload += "=" * (-len(payload) % 4)
        return json.loads(base64.urlsafe_b64decode(payload.encode("ascii")))
    except (ValueError, TypeError, json.JSONDecodeError, binascii.Error) as error:
        raise AuthError("OpenAI returned an unreadable access token") from error


def _identity_from_token(access_token: str) -> tuple[str, str | None]:
    payload = _decode_jwt(access_token)
    auth_claim = payload.get(JWT_CLAIM_PATH) or {}
    account_id = auth_claim.get("chatgpt_account_id")
    if not isinstance(account_id, str) or not account_id:
        raise AuthError("The Codex token does not contain a ChatGPT account ID")
    label = payload.get("email") or payload.get("name")
    return account_id, label if isinstance(label, str) else None


class CodexAuth:
    def __init__(self, database: Database, client: httpx.AsyncClient | None = None):
        self.database = database
        self.client = client or httpx.AsyncClient(timeout=30)
        self._owns_client = client is None
        self._flows: dict[str, DeviceFlow] = {}
        self._lock = asyncio.Lock()

    async def close(self) -> None:
        if self._owns_client:
            await self.client.aclose()

    async def start_device_login(self) -> dict[str, Any]:
        try:
            response = await self.client.post(
                DEVICE_USER_CODE_URL,
                headers={"Content-Type": "application/json"},
                json={"client_id": CLIENT_ID},
            )
        except httpx.HTTPError as error:
            raise AuthError("Could not reach OpenAI authentication") from error
        if response.status_code == 404:
            raise AuthError("Codex device login is currently unavailable")
        if response.is_error:
            raise AuthError(f"OpenAI device login failed ({response.status_code})")
        payload = response.json()
        try:
            interval = max(1.0, float(payload["interval"]))
            flow = DeviceFlow(
                device_auth_id=str(payload["device_auth_id"]),
                user_code=str(payload["user_code"]),
                interval_seconds=interval,
                created_at=time.monotonic(),
                next_poll_at=0,
            )
        except (KeyError, TypeError, ValueError) as error:
            raise AuthError("OpenAI returned an invalid device-login response") from error
        flow_id = secrets.token_urlsafe(24)
        async with self._lock:
            self._flows[flow_id] = flow
        return {
            "flow_id": flow_id,
            "user_code": flow.user_code,
            "verification_uri": DEVICE_VERIFICATION_URI,
            "interval_seconds": flow.interval_seconds,
            "expires_in_seconds": DEVICE_TIMEOUT_SECONDS,
        }

    async def poll_device_login(self, flow_id: str) -> dict[str, Any]:
        async with self._lock:
            flow = self._flows.get(flow_id)
        if not flow:
            raise AuthError("This login session is no longer active")
        now = time.monotonic()
        if now - flow.created_at >= DEVICE_TIMEOUT_SECONDS:
            async with self._lock:
                self._flows.pop(flow_id, None)
            raise AuthError("The device code expired; start a new login")
        if now < flow.next_poll_at:
            return {"status": "pending", "retry_after": round(flow.next_poll_at - now, 1)}
        flow.next_poll_at = now + flow.interval_seconds

        try:
            response = await self.client.post(
                DEVICE_TOKEN_URL,
                headers={"Content-Type": "application/json"},
                json={"device_auth_id": flow.device_auth_id, "user_code": flow.user_code},
            )
        except httpx.HTTPError as error:
            raise AuthError("Could not check the OpenAI login status") from error

        if response.status_code in (403, 404):
            return {"status": "pending", "retry_after": flow.interval_seconds}
        if response.is_error:
            payload = self._safe_json(response)
            code = self._error_code(payload)
            if code == "slow_down":
                flow.interval_seconds += 5
                return {"status": "pending", "retry_after": flow.interval_seconds}
            if code == "deviceauth_authorization_pending":
                return {"status": "pending", "retry_after": flow.interval_seconds}
            raise AuthError(f"OpenAI device authorization failed ({response.status_code})")

        payload = response.json()
        authorization_code = payload.get("authorization_code")
        code_verifier = payload.get("code_verifier")
        if not authorization_code or not code_verifier:
            raise AuthError("OpenAI returned an invalid authorization response")
        token = await self._exchange_code(str(authorization_code), str(code_verifier))
        self._store_token(token)
        async with self._lock:
            self._flows.pop(flow_id, None)
        return {"status": "complete", **self.status()}

    def status(self) -> dict[str, Any]:
        credential = self.database.fetch_one(
            "SELECT expires_at, account_id, account_label, updated_at FROM credentials WHERE provider = ?",
            (PROVIDER,),
        )
        if not credential:
            return {"authenticated": False, "provider": PROVIDER}
        return {
            "authenticated": True,
            "provider": PROVIDER,
            "account_id_suffix": credential["account_id"][-6:],
            "account_label": credential["account_label"],
            "expires_at": credential["expires_at"],
            "updated_at": credential["updated_at"],
        }

    def logout(self) -> None:
        with self.database.write() as connection:
            connection.execute("DELETE FROM credentials WHERE provider = ?", (PROVIDER,))

    async def valid_credential(self) -> dict[str, Any]:
        credential = self.database.fetch_one(
            "SELECT * FROM credentials WHERE provider = ?", (PROVIDER,)
        )
        if not credential:
            raise AuthError("Sign in with Codex before asking the wiki")
        if int(credential["expires_at"]) <= int(time.time() * 1000) + 60_000:
            credential = await self._refresh(credential["refresh_token"])
            self._store_token(credential)
        return credential

    async def usage(self) -> dict[str, Any]:
        """Read the current ChatGPT Codex allowance without exposing credentials.

        Codex itself uses this account endpoint, but it is not a stable public
        Platform API. Keep failure non-fatal so Settings remains useful if the
        endpoint is unavailable or its response changes.
        """
        credential = await self.valid_credential()
        try:
            response = await self.client.get(
                CODEX_USAGE_URL,
                headers={
                    "Authorization": f"Bearer {credential['access_token']}",
                    "chatgpt-account-id": credential["account_id"],
                    "originator": "locus-wiki",
                    "User-Agent": "locus-wiki/1.0",
                },
            )
        except httpx.HTTPError:
            return {
                "available": False,
                "reason": "Codex usage could not be reached. Try refreshing in a moment.",
            }

        if response.status_code == 401:
            raise AuthError("Codex could not read account usage; reconnect your account")
        if response.is_error:
            return {
                "available": False,
                "reason": f"Codex usage is temporarily unavailable ({response.status_code}).",
            }
        payload = self._safe_json(response)
        if not isinstance(payload, dict):
            return {
                "available": False,
                "reason": "Codex returned an unreadable usage response.",
            }
        return self._normalize_usage(payload)

    @classmethod
    def _normalize_usage(cls, payload: dict[str, Any]) -> dict[str, Any]:
        limits: list[dict[str, Any]] = []
        primary_limit = cls._normalize_limit("Codex", payload.get("rate_limit"))
        if primary_limit:
            limits.append(primary_limit)

        additional = payload.get("additional_rate_limits")
        if isinstance(additional, list):
            for entry in additional:
                if not isinstance(entry, dict):
                    continue
                name = entry.get("limit_name") or entry.get("metered_feature") or "Additional"
                normalized = cls._normalize_limit(str(name), entry.get("rate_limit"))
                if normalized:
                    limits.append(normalized)

        credits_payload = payload.get("credits")
        credits = None
        if isinstance(credits_payload, dict):
            balance = credits_payload.get("balance")
            credits = {
                "has_credits": bool(credits_payload.get("has_credits")),
                "unlimited": bool(credits_payload.get("unlimited")),
                "balance": str(balance) if balance is not None else None,
            }

        spend_payload = payload.get("spend_control")
        spend_control = None
        if isinstance(spend_payload, dict):
            individual = spend_payload.get("individual_limit")
            spend_control = {"reached": bool(spend_payload.get("reached"))}
            if isinstance(individual, dict):
                spend_control["individual_limit"] = {
                    key: individual.get(key)
                    for key in (
                        "limit",
                        "used",
                        "remaining",
                        "used_percent",
                        "remaining_percent",
                        "reset_at",
                    )
                    if individual.get(key) is not None
                }

        reset_payload = payload.get("rate_limit_reset_credits")
        reset_credits = None
        if isinstance(reset_payload, dict):
            available_count = reset_payload.get("available_count")
            if isinstance(available_count, (int, float)) and not isinstance(
                available_count, bool
            ):
                reset_credits = max(0, int(available_count))

        plan_type = payload.get("plan_type")
        return {
            "available": bool(limits or credits or spend_control),
            "plan_type": str(plan_type) if plan_type else None,
            "limits": limits,
            "credits": credits,
            "spend_control": spend_control,
            "reset_credits_available": reset_credits,
            "fetched_at": _utc_now(),
            "reason": None
            if limits or credits or spend_control
            else "This Codex account did not return usage statistics.",
        }

    @classmethod
    def _normalize_limit(cls, name: str, payload: Any) -> dict[str, Any] | None:
        if not isinstance(payload, dict):
            return None
        primary = cls._normalize_window(payload.get("primary_window"))
        secondary = cls._normalize_window(payload.get("secondary_window"))
        if not primary and not secondary:
            return None
        allowed = payload.get("allowed")
        return {
            "name": name,
            "allowed": allowed if isinstance(allowed, bool) else None,
            "limit_reached": bool(payload.get("limit_reached")),
            "windows": [window for window in (primary, secondary) if window],
        }

    @staticmethod
    def _normalize_window(payload: Any) -> dict[str, Any] | None:
        if not isinstance(payload, dict):
            return None
        used = payload.get("used_percent")
        if not isinstance(used, (int, float)) or isinstance(used, bool):
            return None
        duration = payload.get("limit_window_seconds")
        reset_at = payload.get("reset_at")
        return {
            "used_percent": max(0.0, min(100.0, float(used))),
            "window_minutes": int(duration / 60)
            if isinstance(duration, (int, float)) and not isinstance(duration, bool)
            else None,
            "resets_at": int(reset_at)
            if isinstance(reset_at, (int, float)) and not isinstance(reset_at, bool)
            else None,
        }

    async def _exchange_code(self, code: str, verifier: str) -> dict[str, Any]:
        try:
            response = await self.client.post(
                TOKEN_URL,
                headers={"Content-Type": "application/x-www-form-urlencoded"},
                data={
                    "grant_type": "authorization_code",
                    "client_id": CLIENT_ID,
                    "code": code,
                    "code_verifier": verifier,
                    "redirect_uri": DEVICE_REDIRECT_URI,
                },
            )
        except httpx.HTTPError as error:
            raise AuthError("Could not exchange the Codex authorization code") from error
        return self._parse_token_response(response, "exchange")

    async def _refresh(self, refresh_token: str) -> dict[str, Any]:
        try:
            response = await self.client.post(
                TOKEN_URL,
                headers={"Content-Type": "application/x-www-form-urlencoded"},
                data={
                    "grant_type": "refresh_token",
                    "refresh_token": refresh_token,
                    "client_id": CLIENT_ID,
                },
            )
        except httpx.HTTPError as error:
            raise AuthError("Could not refresh the Codex login") from error
        return self._parse_token_response(response, "refresh")

    def _parse_token_response(self, response: httpx.Response, operation: str) -> dict[str, Any]:
        if response.is_error:
            raise AuthError(f"OpenAI token {operation} failed ({response.status_code}); sign in again")
        payload = response.json()
        access = payload.get("access_token")
        refresh = payload.get("refresh_token")
        expires_in = payload.get("expires_in")
        if not access or not refresh or not isinstance(expires_in, (int, float)):
            raise AuthError(f"OpenAI token {operation} returned incomplete credentials")
        account_id, account_label = _identity_from_token(str(access))
        return {
            "provider": PROVIDER,
            "access_token": str(access),
            "refresh_token": str(refresh),
            "expires_at": int(time.time() * 1000 + float(expires_in) * 1000),
            "account_id": account_id,
            "account_label": account_label,
        }

    def _store_token(self, token: dict[str, Any]) -> None:
        with self.database.write() as connection:
            connection.execute(
                """
                INSERT INTO credentials(provider, access_token, refresh_token, expires_at,
                                        account_id, account_label, updated_at)
                VALUES (?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(provider) DO UPDATE SET access_token=excluded.access_token,
                    refresh_token=excluded.refresh_token, expires_at=excluded.expires_at,
                    account_id=excluded.account_id, account_label=excluded.account_label,
                    updated_at=excluded.updated_at
                """,
                (
                    PROVIDER,
                    token["access_token"],
                    token["refresh_token"],
                    token["expires_at"],
                    token["account_id"],
                    token.get("account_label"),
                    _utc_now(),
                ),
            )

    @staticmethod
    def _safe_json(response: httpx.Response) -> Any:
        try:
            return response.json()
        except ValueError:
            return {}

    @staticmethod
    def _error_code(payload: Any) -> str | None:
        if not isinstance(payload, dict):
            return None
        error = payload.get("error")
        if isinstance(error, dict):
            code = error.get("code")
            return str(code) if code else None
        return str(error) if error else None
