"""Config flow for United Rewards."""

from __future__ import annotations

import logging
from typing import Any

import aiohttp
import voluptuous as vol

from homeassistant import config_entries
from homeassistant.const import CONF_EMAIL, CONF_PASSWORD
from homeassistant.helpers.aiohttp_client import async_get_clientsession

from .api import (
    LoginChallenge,
    UnitedRewardsAuthError,
    UnitedRewardsClient,
    UnitedRewardsError,
    UnitedRewardsMfaRequired,
)
from .const import (
    CONF_AUTH_TOKEN,
    CONF_DEVICE_TOKEN,
    CONF_HOUSEHOLD_ID,
    CONF_OKTA_ID,
    DOMAIN,
)

_LOGGER = logging.getLogger(__name__)

STEP_USER_SCHEMA = vol.Schema(
    {
        vol.Required(CONF_EMAIL): str,
        vol.Required(CONF_PASSWORD): str,
        vol.Optional(CONF_HOUSEHOLD_ID): str,
    }
)

STEP_CODE_SCHEMA = vol.Schema({vol.Required("code"): str})


class UnitedRewardsConfigFlow(config_entries.ConfigFlow, domain=DOMAIN):
    """Handle a United Rewards config flow."""

    VERSION = 1

    def __init__(self) -> None:
        """Initialize flow."""
        self._email: str | None = None
        self._password: str | None = None
        self._household_id: str | None = None
        self._client: UnitedRewardsClient | None = None
        self._session: aiohttp.ClientSession | None = None
        self._challenge: LoginChallenge | None = None
        self._okta_id: str | None = None
        self._login_household_id: str | None = None
        self._customer_uuid: str | None = None

    async def async_step_user(
        self, user_input: dict[str, Any] | None = None
    ) -> config_entries.ConfigFlowResult:
        """Handle the initial step."""
        errors: dict[str, str] = {}

        if user_input is not None:
            self._email = user_input[CONF_EMAIL]
            self._password = user_input[CONF_PASSWORD]
            self._household_id = user_input.get(CONF_HOUSEHOLD_ID) or None

            await self.async_set_unique_id(self._email.lower())
            self._abort_if_unique_id_configured()

            self._session = async_get_clientsession(self.hass)
            self._client = UnitedRewardsClient(self._session)

            try:
                result = await self._client.start_login(self._email, self._password)
            except UnitedRewardsMfaRequired as err:
                self._challenge = err.challenge
                return await self.async_step_code()
            except UnitedRewardsAuthError:
                errors["base"] = "invalid_auth"
            except UnitedRewardsError:
                errors["base"] = "cannot_connect"
            except Exception:
                _LOGGER.exception("Unexpected exception during United Rewards setup")
                errors["base"] = "unknown"
            else:
                self._okta_id = result.okta_id
                self._login_household_id = result.household_id
                self._customer_uuid = result.customer_uuid
                return await self._async_create_entry(result.auth_token)

        return self.async_show_form(step_id="user", data_schema=STEP_USER_SCHEMA, errors=errors)

    async def async_step_code(
        self, user_input: dict[str, Any] | None = None
    ) -> config_entries.ConfigFlowResult:
        """Handle email verification code."""
        errors: dict[str, str] = {}

        if user_input is not None and self._client and self._challenge:
            try:
                result = await self._client.verify_email_code(self._challenge, user_input["code"].strip())
            except UnitedRewardsAuthError:
                errors["base"] = "invalid_code"
            except UnitedRewardsError:
                errors["base"] = "cannot_connect"
            except Exception:
                _LOGGER.exception("Unexpected exception verifying United Rewards code")
                errors["base"] = "unknown"
            else:
                self._okta_id = result.okta_id
                self._login_household_id = result.household_id
                self._customer_uuid = result.customer_uuid
                return await self._async_create_entry(result.auth_token)

        return self.async_show_form(
            step_id="code",
            data_schema=STEP_CODE_SCHEMA,
            errors=errors,
            description_placeholders={"email": self._email or ""},
        )

    async def _async_create_entry(self, auth_token: str) -> config_entries.ConfigFlowResult:
        """Create config entry after authentication."""
        if not self._client or not self._email or not self._password:
            return self.async_abort(reason="unknown")

        household_id = self._household_id or self._login_household_id
        if not household_id:
            errors = {"base": "household_id_required"}
            return self.async_show_form(step_id="user", data_schema=STEP_USER_SCHEMA, errors=errors)

        data = {
            CONF_EMAIL: self._email,
            CONF_PASSWORD: self._password,
            CONF_DEVICE_TOKEN: self._client.device_token,
            CONF_AUTH_TOKEN: auth_token,
            CONF_HOUSEHOLD_ID: household_id,
        }
        if self._okta_id:
            data[CONF_OKTA_ID] = self._okta_id
        if self._customer_uuid:
            data[CONF_CUSTOMER_UUID] = self._customer_uuid

        return self.async_create_entry(title=f"United Rewards {self._email}", data=data)
