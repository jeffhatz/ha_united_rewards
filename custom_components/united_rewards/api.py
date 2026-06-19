"""API client for United Rewards."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime
from email.utils import formatdate
import base64
import hashlib
import json
import logging
import secrets
import time
from typing import Any
from urllib.parse import parse_qs, urlencode, urlparse
from uuid import uuid4

import aiohttp

from .const import (
    BANNER,
    BASE_URL,
    CSMS_SUBSCRIPTION_KEY,
    OKTA_CLIENT_ID,
    OKTA_ISSUER,
    OKTA_REDIRECT_URI,
    OKTA_SCOPE,
    POINTS_HISTORY_SUBSCRIPTION_KEY,
    USER_AGENT,
)

_LOGGER = logging.getLogger(__name__)


class UnitedRewardsError(Exception):
    """Base United Rewards API error."""


class UnitedRewardsAuthError(UnitedRewardsError):
    """Authentication failed."""


class UnitedRewardsMfaRequired(UnitedRewardsAuthError):
    """Raised when an email code is required."""

    def __init__(self, challenge: LoginChallenge) -> None:
        """Initialize MFA challenge."""
        super().__init__("Email verification code required")
        self.challenge = challenge


@dataclass(slots=True)
class LoginChallenge:
    """Information needed to complete an email MFA challenge."""

    state_token: str
    okta_id: str
    email_factor_id: str
    expires_at: str | None = None
    auth_token: str | None = None


@dataclass(slots=True)
class LoginResult:
    """Successful login result."""

    auth_token: str
    okta_id: str | None = None
    household_id: str | None = None
    customer_uuid: str | None = None


@dataclass(slots=True)
class RewardPointBucket:
    """Points expiring on a specific date."""

    value: int
    validity_end_date: date | None


@dataclass(slots=True)
class RewardScorecard:
    """Parsed reward scorecard."""

    household_id: str
    program_type: str
    balance: int
    dollar_discount: int
    auto_rewards_points: int
    will_expire: int
    points: list[RewardPointBucket]


class UnitedRewardsClient:
    """Small async client for the United Rewards web APIs."""

    def __init__(
        self,
        session: aiohttp.ClientSession,
        *,
        email: str | None = None,
        password: str | None = None,
        device_token: str | None = None,
        auth_token: str | None = None,
    ) -> None:
        """Initialize client."""
        self._session = session
        self.email = email
        self.password = password
        self.device_token = device_token or secrets.token_hex(16)
        self.auth_token = auth_token

    async def close(self) -> None:
        """Close the underlying session if the caller owns it."""
        await self._session.close()

    async def start_login(self, email: str, password: str) -> LoginResult:
        """Start login and either return tokens or raise an MFA challenge."""
        self.email = email
        self.password = password

        initial = await self._request_json(
            "post",
            f"{BASE_URL}/abs/pub/cnc/csmsservice/api/csms/authn?mode=nonotp",
            headers=self._csms_headers(email),
            json={"userId": email, "context": {"deviceToken": self.device_token}},
        )

        state_token = _find_first(initial, "stateToken", "state_token")
        okta_id = _extract_okta_id(initial)
        if not state_token or not okta_id:
            raise UnitedRewardsAuthError("Login did not return a state token")

        password_result = await self._request_json(
            "post",
            f"{BASE_URL}/abs/pub/cnc/csmsservice/api/csms/authn/factors/password/verify",
            headers=self._csms_headers(email),
            json={
                "stateToken": state_token,
                "passCode": password,
                "id": okta_id,
            },
        )

        token = _extract_auth_token(password_result)
        if token:
            auth_token = await self._finish_sso(token)
            self.auth_token = auth_token
            return LoginResult(
                auth_token=auth_token,
                okta_id=okta_id,
                household_id=household_id_from_token(auth_token),
                customer_uuid=customer_uuid_from_token(auth_token),
            )

        email_factor_id = _extract_email_factor_id(password_result)
        state_token = _find_first(password_result, "stateToken", "state_token") or state_token
        expires_at = _find_first(password_result, "expiresAt", "expires_at")
        if not email_factor_id:
            status = _find_first(password_result, "status", "errorCode", "message")
            raise UnitedRewardsAuthError(f"Password accepted but no email factor was returned: {status}")

        challenge = LoginChallenge(
            state_token=state_token,
            okta_id=okta_id,
            email_factor_id=email_factor_id,
            expires_at=expires_at,
        )
        await self.send_email_code(challenge)
        raise UnitedRewardsMfaRequired(challenge)

    async def send_email_code(self, challenge: LoginChallenge) -> None:
        """Ask United/Okta to send an email verification code."""
        if not self.email:
            raise UnitedRewardsAuthError("Email is required to send the MFA code")

        data = await self._request_json(
            "post",
            f"{BASE_URL}/abs/pub/cnc/csmsservice/api/csms/authn/factors/{challenge.email_factor_id}/send",
            headers=self._csms_headers(self.email),
            json={
                "stateToken": challenge.state_token,
                "oktaId": challenge.okta_id,
                "expiresAt": challenge.expires_at,
                "loginId": self.email,
            },
        )
        challenge.state_token = _find_first(data, "stateToken", "state_token") or challenge.state_token
        challenge.expires_at = _find_first(data, "expiresAt", "expires_at") or challenge.expires_at

    async def verify_email_code(self, challenge: LoginChallenge, code: str) -> LoginResult:
        """Complete an email MFA challenge."""
        if not self.email:
            raise UnitedRewardsAuthError("Email is required to verify the MFA code")

        data = await self._request_json(
            "post",
            f"{BASE_URL}/abs/pub/cnc/csmsservice/api/csms/authn/factors/{challenge.email_factor_id}/verify",
            headers=self._csms_headers(self.email),
            json={
                "stateToken": challenge.state_token,
                "passCode": code,
                "id": challenge.okta_id,
            },
        )
        token = _extract_auth_token(data)
        if not token:
            raise UnitedRewardsAuthError("Verification succeeded but no session token was returned")

        auth_token = await self._finish_sso(token)
        self.auth_token = auth_token
        return LoginResult(
            auth_token=auth_token,
            okta_id=challenge.okta_id,
            household_id=household_id_from_token(auth_token),
            customer_uuid=customer_uuid_from_token(auth_token),
        )

    async def refresh_login(self) -> LoginResult:
        """Login again with saved credentials."""
        if not self.email or not self.password:
            raise UnitedRewardsAuthError("Email and password are required to refresh login")
        return await self.start_login(self.email, self.password)

    async def async_get_household_id(self, customer_uuid: str) -> str:
        """Fetch the United household id from the customer profile."""
        data = await self._request_json(
            "get",
            f"{BASE_URL}/abs/pub/cnc/ucaservice/api/uca/customers/{customer_uuid}/profile?fl=address,factors",
            headers=self._auth_headers("application/vnd.safeway.v4+json"),
        )

        for alternate_id in data.get("alternateIds", []):
            if alternate_id.get("type") == "UNITED_HHID" and alternate_id.get("value"):
                return str(alternate_id["value"])

        raise UnitedRewardsError("United household id was not present in the profile response")

    async def async_get_scorecard(self, household_id: str) -> RewardScorecard:
        """Fetch and parse the rewards scorecard."""
        data = await self._request_json(
            "post",
            f"{BASE_URL}/abs/pub/xapi/ocrp/rewards/scorecard",
            headers=self._auth_headers(
                "application/vnd.safeway.v3+json",
                subscription_key=POINTS_HISTORY_SUBSCRIPTION_KEY,
                extra={"X-ABS-Client-ID": "CAMP", "isOktaToken": "true"},
            ),
            json={"hhid": household_id, "programType": ["BASEPOINTS"]},
        )

        if errors := data.get("errors"):
            if any(error.get("code") == "140409" for error in errors if isinstance(error, dict)):
                return RewardScorecard(
                    household_id=household_id,
                    program_type="BASEPOINTS",
                    balance=0,
                    dollar_discount=0,
                    auto_rewards_points=0,
                    will_expire=0,
                    points=[],
                )
            message = errors[0].get("message") if isinstance(errors, list) and errors else data.get("message")
            raise UnitedRewardsError(message or "Scorecard response contained errors")

        return parse_scorecard(data)

    async def _finish_sso(self, session_token: str) -> str:
        """Complete the Okta authorize redirect and return a bearer token.

        The web app ultimately sends an Okta bearer token to XAPI. In some
        responses the CSMS session token is already accepted as that token; in
        others the redirect chain or URL fragment can expose a more specific
        access/id token. Prefer the most specific token we can discover.
        """
        params = {
            "client_id": OKTA_CLIENT_ID,
            "redirect_uri": OKTA_REDIRECT_URI,
            "response_type": "code",
            "response_mode": "query",
            "nonce": secrets.token_urlsafe(32),
            "scope": OKTA_SCOPE,
            "state": secrets.token_urlsafe(24),
            "sessionToken": session_token,
        }
        url = f"{OKTA_ISSUER}/v1/authorize?{urlencode(params)}"

        try:
            async with self._session.get(
                url,
                headers={"User-Agent": USER_AGENT},
                allow_redirects=True,
            ) as response:
                text = await response.text()
                response.raise_for_status()
                _extract_token_from_url(str(response.url)) or _extract_token_from_text(text)
                userinfo = await self._request_json(
                    "get",
                    f"{BASE_URL}/bin/safeway/unified/userinfo?banner={BANNER}&rand={uuid4()}",
                    headers={
                        "Accept": "application/json, text/plain, */*",
                        "User-Agent": USER_AGENT,
                    },
                )
                if token := userinfo.get("SWY_SHOP_TOKEN"):
                    return str(token)
                raise UnitedRewardsAuthError("United Rewards userinfo did not return a shop token")
        except aiohttp.ClientResponseError:
            raise
        except aiohttp.ClientError as err:
            _LOGGER.debug("SSO redirect did not expose a token: %s", err)

        return session_token

    async def _request_json(
        self,
        method: str,
        url: str,
        *,
        headers: dict[str, str],
        json: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        """Make a JSON request."""
        try:
            async with self._session.request(method, url, headers=headers, json=json) as response:
                text = await response.text()
                if response.status in (401, 403):
                    raise UnitedRewardsAuthError(f"United Rewards returned HTTP {response.status}")
                if response.status >= 400:
                    raise UnitedRewardsError(f"United Rewards returned HTTP {response.status}: {text[:200]}")
                if not text:
                    return {}
                try:
                    parsed = await response.json(content_type=None)
                except (aiohttp.ContentTypeError, ValueError) as err:
                    raise UnitedRewardsError("United Rewards returned a non-JSON response") from err
        except TimeoutError as err:
            raise UnitedRewardsError("Timed out connecting to United Rewards") from err
        except aiohttp.ClientError as err:
            raise UnitedRewardsError(f"Could not connect to United Rewards: {err}") from err

        if not isinstance(parsed, dict):
            raise UnitedRewardsError("United Rewards returned an unexpected response shape")
        return parsed

    def _csms_headers(self, email: str) -> dict[str, str]:
        """Headers for CSMS auth endpoints."""
        return {
            "Accept": "application/vnd.safeway.v2+json",
            "Content-Type": "application/vnd.safeway.v2+json",
            "Origin": BASE_URL,
            "Referer": f"{BASE_URL}/account/sign-in.html",
            "User-Agent": USER_AGENT,
            "ocp-apim-subscription-key": CSMS_SUBSCRIPTION_KEY,
            "x-aci-user-hash": hashlib.sha256(email.upper().encode()).hexdigest(),
            "x-swy-banner": BANNER,
            "x-swy-client-id": "web-portal",
            "x-swy-correlation-id": str(uuid4()),
            "x-swy-date": formatdate(time.time(), usegmt=True),
        }

    def _auth_headers(
        self,
        content_type: str,
        *,
        subscription_key: str = CSMS_SUBSCRIPTION_KEY,
        extra: dict[str, str] | None = None,
    ) -> dict[str, str]:
        """Headers for authenticated United endpoints."""
        if not self.auth_token:
            raise UnitedRewardsAuthError("No auth token is available")

        headers = {
            "Accept": content_type,
            "Authorization": f"Bearer {self.auth_token}",
            "Content-Type": content_type,
            "Origin": BASE_URL,
            "Referer": f"{BASE_URL}/customer-account/rewards",
            "User-Agent": USER_AGENT,
            "Ocp-Apim-Subscription-Key": subscription_key,
            "x-swy-banner": BANNER,
            "x-swy-client-name": "web-portal",
            "x-swy-correlation-id": str(uuid4()),
            "x-swy-date": datetime.now().astimezone().strftime("%a %b %d %Y %H:%M:%S GMT%z"),
        }
        if extra:
            headers.update(extra)
        return headers


def parse_scorecard(data: dict[str, Any]) -> RewardScorecard:
    """Parse a rewards scorecard response."""
    scorecards = data.get("scorecards")
    if not isinstance(scorecards, list) or not scorecards:
        raise UnitedRewardsError("Scorecard response did not contain a scorecard")

    raw = scorecards[0]
    points = [
        RewardPointBucket(
            value=int(point.get("value", 0)),
            validity_end_date=_parse_date(point.get("validityEndDate")),
        )
        for point in raw.get("points", [])
        if isinstance(point, dict)
    ]

    return RewardScorecard(
        household_id=str(data.get("hhId") or data.get("hhid") or ""),
        program_type=str(raw.get("programType") or "BASEPOINTS"),
        balance=int(raw.get("balance") or 0),
        dollar_discount=int(raw.get("dollarDiscount") or 0),
        auto_rewards_points=int(raw.get("autoRewardsPoints") or 0),
        will_expire=int(raw.get("willExpire") or 0),
        points=points,
    )


def household_id_from_token(token: str) -> str | None:
    """Extract household id from a SWY shop token."""
    return _jwt_payload(token).get("hid")


def customer_uuid_from_token(token: str) -> str | None:
    """Extract customer UUID from a SWY shop token."""
    return _jwt_payload(token).get("uuid")


def _jwt_payload(token: str) -> dict[str, str]:
    parts = token.split(".")
    if len(parts) < 2:
        return {}
    try:
        payload = parts[1] + "=" * ((4 - len(parts[1]) % 4) % 4)
        data = json.loads(base64.urlsafe_b64decode(payload).decode())
    except (ValueError, json.JSONDecodeError):
        return {}
    return {key: value for key, value in data.items() if isinstance(value, str)}


def _parse_date(value: Any) -> date | None:
    if not value:
        return None
    if isinstance(value, date):
        return value
    try:
        return date.fromisoformat(str(value))
    except ValueError:
        return None


def _extract_okta_id(data: dict[str, Any]) -> str | None:
    return (
        _find_first(data, "oktaId", "okta_id", "id")
        or _find_path(data, "_embedded", "user", "id")
        or _find_path(data, "loginInfo", "id")
    )


def _extract_email_factor_id(data: dict[str, Any]) -> str | None:
    factors = _find_factors(data)
    for factor in factors:
        factor_type = str(factor.get("factorType") or factor.get("type") or "").lower()
        provider = str(factor.get("provider") or "").lower()
        profile = factor.get("profile") if isinstance(factor.get("profile"), dict) else {}
        if "email" in factor_type or "email" in provider or profile.get("email"):
            return str(factor.get("id") or factor.get("factorId") or "")
    if factors:
        return str(factors[0].get("id") or factors[0].get("factorId") or "")
    return None


def _find_factors(data: Any) -> list[dict[str, Any]]:
    if isinstance(data, dict):
        factors = data.get("factors")
        if isinstance(factors, list):
            return [factor for factor in factors if isinstance(factor, dict)]
        embedded = data.get("_embedded")
        if isinstance(embedded, dict):
            factors = embedded.get("factors")
            if isinstance(factors, list):
                return [factor for factor in factors if isinstance(factor, dict)]
        for value in data.values():
            found = _find_factors(value)
            if found:
                return found
    if isinstance(data, list):
        for item in data:
            found = _find_factors(item)
            if found:
                return found
    return []


def _extract_auth_token(data: dict[str, Any]) -> str | None:
    return _find_first(
        data,
        "authToken",
        "accessToken",
        "access_token",
        "idToken",
        "id_token",
        "sessionToken",
    )


def _find_first(data: Any, *keys: str) -> str | None:
    if isinstance(data, dict):
        for key in keys:
            value = data.get(key)
            if isinstance(value, str) and value:
                return value
        for value in data.values():
            found = _find_first(value, *keys)
            if found:
                return found
    if isinstance(data, list):
        for item in data:
            found = _find_first(item, *keys)
            if found:
                return found
    return None


def _find_path(data: dict[str, Any], *path: str) -> str | None:
    current: Any = data
    for key in path:
        if not isinstance(current, dict):
            return None
        current = current.get(key)
    return current if isinstance(current, str) and current else None


def _extract_token_from_url(url: str) -> str | None:
    parsed = urlparse(url)
    query = parse_qs(parsed.query)
    fragment = parse_qs(parsed.fragment)
    for params in (fragment, query):
        for key in ("access_token", "id_token", "authToken", "token"):
            if value := params.get(key):
                return value[0]
    return None


def _extract_token_from_text(text: str) -> str | None:
    for marker in ("access_token", "id_token", "authToken", "SWY_SHOP_TOKEN"):
        index = text.find(marker)
        if index == -1:
            continue
        chunk = text[index : index + 300]
        for separator in ('"', "'", ":", "="):
            parts = chunk.split(separator)
            for part in parts:
                if len(part) > 40 and "." in part:
                    return part.strip(" ;,'\"")
    return None
