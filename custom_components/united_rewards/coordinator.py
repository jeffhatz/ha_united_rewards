"""Data coordinator for United Rewards."""

from __future__ import annotations

from datetime import timedelta
import logging

from homeassistant.config_entries import ConfigEntry
from homeassistant.const import CONF_EMAIL, CONF_PASSWORD
from homeassistant.core import HomeAssistant
from homeassistant.helpers.aiohttp_client import async_get_clientsession
from homeassistant.helpers.update_coordinator import DataUpdateCoordinator, UpdateFailed

from .api import RewardScorecard, UnitedRewardsAuthError, UnitedRewardsClient, UnitedRewardsMfaRequired
from .const import CONF_AUTH_TOKEN, CONF_DEVICE_TOKEN, CONF_HOUSEHOLD_ID, DEFAULT_SCAN_INTERVAL, DOMAIN

_LOGGER = logging.getLogger(__name__)


class UnitedRewardsCoordinator(DataUpdateCoordinator[RewardScorecard]):
    """United Rewards update coordinator."""

    config_entry: ConfigEntry

    def __init__(self, hass: HomeAssistant, entry: ConfigEntry) -> None:
        """Initialize coordinator."""
        self.client = UnitedRewardsClient(
            async_get_clientsession(hass),
            email=entry.data.get(CONF_EMAIL),
            password=entry.data.get(CONF_PASSWORD),
            device_token=entry.data.get(CONF_DEVICE_TOKEN),
            auth_token=entry.data.get(CONF_AUTH_TOKEN),
        )
        self.household_id = entry.data[CONF_HOUSEHOLD_ID]

        super().__init__(
            hass,
            _LOGGER,
            config_entry=entry,
            name=DOMAIN,
            update_interval=entry.options.get("scan_interval", DEFAULT_SCAN_INTERVAL)
            if isinstance(entry.options.get("scan_interval"), timedelta)
            else DEFAULT_SCAN_INTERVAL,
        )

    async def _async_update_data(self) -> RewardScorecard:
        """Fetch rewards data."""
        try:
            return await self.client.async_get_scorecard(self.household_id)
        except UnitedRewardsAuthError:
            try:
                result = await self.client.refresh_login()
            except UnitedRewardsMfaRequired as err:
                raise UpdateFailed("United Rewards requires a new email verification code") from err
            self.client.auth_token = result.auth_token
            return await self.client.async_get_scorecard(self.household_id)
        except Exception as err:
            raise UpdateFailed(str(err)) from err
