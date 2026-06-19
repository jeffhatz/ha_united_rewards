"""Sensors for United Rewards."""

from __future__ import annotations

from dataclasses import dataclass
from collections.abc import Callable
from typing import Any

from homeassistant.components.sensor import SensorDeviceClass, SensorEntity, SensorEntityDescription, SensorStateClass
from homeassistant.config_entries import ConfigEntry
from homeassistant.const import CURRENCY_DOLLAR
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddEntitiesCallback
from homeassistant.helpers.update_coordinator import CoordinatorEntity

from .api import RewardScorecard
from .const import DOMAIN
from .coordinator import UnitedRewardsCoordinator


def _next_expiration_date(data: RewardScorecard) -> str | None:
    """Return the next expiration date with positive points."""
    return next(
        (
            bucket.validity_end_date.isoformat()
            for bucket in data.points
            if bucket.validity_end_date and bucket.value > 0
        ),
        None,
    )


@dataclass(frozen=True, kw_only=True)
class UnitedRewardsSensorDescription(SensorEntityDescription):
    """United Rewards sensor description."""

    value_fn: Callable[[RewardScorecard], int | str | None]
    attrs_fn: Callable[[RewardScorecard], dict[str, Any]] | None = None


SENSORS: tuple[UnitedRewardsSensorDescription, ...] = (
    UnitedRewardsSensorDescription(
        key="points",
        translation_key="points",
        native_unit_of_measurement="points",
        state_class=SensorStateClass.TOTAL,
        value_fn=lambda data: data.balance,
        attrs_fn=lambda data: {
            "household_id": data.household_id,
            "program_type": data.program_type,
            "auto_rewards_points": data.auto_rewards_points,
            "dollar_discount": data.dollar_discount,
            "expirations": [
                {"points": bucket.value, "date": bucket.validity_end_date.isoformat() if bucket.validity_end_date else None}
                for bucket in data.points
            ],
        },
    ),
    UnitedRewardsSensorDescription(
        key="expiring_points",
        translation_key="expiring_points",
        native_unit_of_measurement="points",
        state_class=SensorStateClass.MEASUREMENT,
        value_fn=lambda data: data.will_expire,
        attrs_fn=lambda data: {"next_expiration_date": _next_expiration_date(data)},
    ),
    UnitedRewardsSensorDescription(
        key="next_expiration_date",
        translation_key="next_expiration_date",
        device_class=SensorDeviceClass.DATE,
        value_fn=_next_expiration_date,
    ),
    UnitedRewardsSensorDescription(
        key="dollar_discount",
        translation_key="dollar_discount",
        device_class=SensorDeviceClass.MONETARY,
        native_unit_of_measurement=CURRENCY_DOLLAR,
        state_class=SensorStateClass.MEASUREMENT,
        value_fn=lambda data: data.dollar_discount,
    ),
)


async def async_setup_entry(
    hass: HomeAssistant,
    entry: ConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    """Set up United Rewards sensors."""
    coordinator: UnitedRewardsCoordinator = hass.data[DOMAIN][entry.entry_id]
    async_add_entities(UnitedRewardsSensor(coordinator, entry, description) for description in SENSORS)


class UnitedRewardsSensor(CoordinatorEntity[UnitedRewardsCoordinator], SensorEntity):
    """United Rewards sensor."""

    entity_description: UnitedRewardsSensorDescription
    _attr_has_entity_name = True

    def __init__(
        self,
        coordinator: UnitedRewardsCoordinator,
        entry: ConfigEntry,
        description: UnitedRewardsSensorDescription,
    ) -> None:
        """Initialize sensor."""
        super().__init__(coordinator)
        self.entity_description = description
        self._attr_unique_id = f"{entry.entry_id}_{description.key}"
        self._attr_device_info = {
            "identifiers": {(DOMAIN, entry.entry_id)},
            "name": "United Rewards",
            "manufacturer": "United Supermarkets",
        }

    @property
    def native_value(self) -> int | str | None:
        """Return the sensor value."""
        if not self.coordinator.data:
            return None
        return self.entity_description.value_fn(self.coordinator.data)

    @property
    def extra_state_attributes(self) -> dict[str, Any] | None:
        """Return extra state attributes."""
        if not self.coordinator.data or not self.entity_description.attrs_fn:
            return None
        return self.entity_description.attrs_fn(self.coordinator.data)
