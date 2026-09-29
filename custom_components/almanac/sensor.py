"""D55 — one `device_class: timestamp` sensor per schedule, holding the next trigger.

The point of the device class is that templates, dashboards and automations get
the instant as an instant, rather than digging it out of an attribute on the
switch and parsing a string.

The value is `None` until step 2 and step 3 exist: computing it means resolving
anchors and pairing intervals, and both sit behind the resolver contract. The
entity ships now because D37 wants the `unique_id` from the first commit — a
unique_id added later cannot rename entities that already exist — and because
its entity_id is settled at creation under D66, so it cannot be introduced later
without either re-deriving a slug or inventing a second one.
"""

from __future__ import annotations

from datetime import datetime

from homeassistant.components.sensor import SensorDeviceClass, SensorEntity
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddConfigEntryEntitiesCallback

from .const import SENSOR_OBJECT_ID_SUFFIX
from .entity import AlmanacScheduleEntity, async_setup_collection_platform
from .storage import AlmanacData


async def async_setup_entry(
    hass: HomeAssistant,
    entry: ConfigEntry,
    async_add_entities: AddConfigEntryEntitiesCallback,
) -> None:
    """Set up one next-trigger sensor per schedule."""
    data: AlmanacData = entry.runtime_data
    entry.async_on_unload(
        await async_setup_collection_platform(
            hass,
            data.schedules,
            "sensor",
            AlmanacNextTriggerSensor,
            async_add_entities,
        )
    )


class AlmanacNextTriggerSensor(AlmanacScheduleEntity, SensorEntity):
    """When this schedule next does something."""

    _platform_domain = "sensor"
    _object_id_suffix = SENSOR_OBJECT_ID_SUFFIX
    _attr_device_class = SensorDeviceClass.TIMESTAMP

    @property
    def native_value(self) -> datetime | None:
        """The next instant at which this schedule fires.

        Unimplemented until the rule engine lands (§15 step 3), and it stays
        `None` rather than guessing — D13's *known / estimated / unknown* split
        exists precisely so that not knowing is a state the product can render
        honestly instead of a gap it papers over.

        D64 constrains the eventual implementation: this cannot become a
        property that reads the clock. The next trigger is a function of an
        instant the tick supplies, so the engine computes it and writes it here;
        the entity does not compute it for itself. Threading `now` in
        afterwards is a rewrite, not a refactor.
        """
        return None

    def _friendly_name(self, schedule_name: str) -> str:
        """Distinguish this from the schedule's switch in a picker.

        D55 does not say what to call it. Both entities would otherwise show the
        same string in every entity picker in HA, which reintroduces the
        find-the-right-entity problem D54 exists to solve, one domain over.
        """
        return f"{schedule_name} next trigger"
