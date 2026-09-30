"""D55 — one `device_class: timestamp` sensor per schedule, holding the next trigger.

The point of the device class is that templates, dashboards and automations get
the instant as an instant, rather than digging it out of an attribute on the
switch and parsing a string.

The value is the one the tick published for this schedule on its last pass, and
that direction is D64's rather than a convenience: computing a next trigger means
knowing the instant to compute it from, this entity has no clock, and so the
engine tells it. `ScheduleStatus` in `events.py` is the channel, and the same
number is persisted in the runtime record so the engine can reason about it
across a restart.
"""

from __future__ import annotations

from datetime import datetime

from homeassistant.components.sensor import SensorDeviceClass, SensorEntity
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddConfigEntryEntitiesCallback

from .const import SENSOR_OBJECT_ID_SUFFIX, SENSOR_PLATFORM
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
            SENSOR_PLATFORM,
            AlmanacNextTriggerSensor,
            async_add_entities,
            data,
        )
    )


class AlmanacNextTriggerSensor(AlmanacScheduleEntity, SensorEntity):
    """When this schedule next does something."""

    _platform_domain = SENSOR_PLATFORM
    _object_id_suffix = SENSOR_OBJECT_ID_SUFFIX
    _attr_device_class = SensorDeviceClass.TIMESTAMP

    @property
    def native_value(self) -> datetime | None:
        """The next instant at which this schedule fires.

        Read from what the tick published, never computed here. D64 is the whole
        of it: the next trigger is a function of an instant, this entity has no
        clock, and an entity that worked the answer out for itself would be a
        second implementation of the engine that could disagree with the timeline
        drawn from the same rules.

        `None` stays a real answer rather than a gap — D13's *unknown*. It is what
        a schedule with nothing inside D44's ninety-day budget reports, and what
        every schedule reports until the first tick has run.
        """
        status = self.status
        return status.async_next_at(self.schedule_id) if status is not None else None

    def _friendly_name(self, schedule_name: str) -> str:
        """Distinguish this from the schedule's switch in a picker.

        D55 does not say what to call it. Both entities would otherwise show the
        same string in every entity picker in HA, which reintroduces the
        find-the-right-entity problem D54 exists to solve, one domain over.
        """
        return f"{schedule_name} next trigger"
