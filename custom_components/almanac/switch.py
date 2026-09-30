"""D54 — one switch per schedule, at an entity_id someone can find.

`switch.schedule_<6 hex>` is the core of the "entities you cannot find"
complaint the brief records. A slug is not cosmetic: it is what makes a schedule
referenceable from an automation somebody else wrote. D66 governs where the slug
comes from and, more importantly, when it stops being recomputed — see
`entity.AlmanacScheduleEntity`.
"""

from __future__ import annotations

from typing import Any

from homeassistant.components.switch import SwitchEntity
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddConfigEntryEntitiesCallback

from .const import ATTR_RECENT_EXECUTIONS, CONF_ENABLED, SCHEDULE_PLATFORM
from .entity import AlmanacScheduleEntity, async_setup_collection_platform
from .storage import AlmanacData


async def async_setup_entry(
    hass: HomeAssistant,
    entry: ConfigEntry,
    async_add_entities: AddConfigEntryEntitiesCallback,
) -> None:
    """Set up one switch per schedule."""
    data: AlmanacData = entry.runtime_data
    entry.async_on_unload(
        await async_setup_collection_platform(
            hass,
            data.schedules,
            SCHEDULE_PLATFORM,
            AlmanacScheduleSwitch,
            async_add_entities,
            data,
        )
    )


class AlmanacScheduleSwitch(AlmanacScheduleEntity, SwitchEntity):
    """Arms and disarms a schedule, and shows what it has lately done."""

    _platform_domain = SCHEDULE_PLATFORM
    _attr_icon = "mdi:calendar-clock"

    # D52 — the execution cache is a *display* attribute and is declared out of
    # the database here. Verified against 2026.9.4: `helpers/entity.py` collects
    # `_unrecorded_attributes` across the class hierarchy in `__init_subclass__`
    # and publishes the union through `self._state_info["unrecorded_attributes"]`,
    # which `components/recorder/db_schema.py::shared_attrs_bytes_from_event`
    # reads to drop those keys before writing the attribute blob.
    #
    # The point is not size, it is churn: every occurrence changes this list, and
    # the recorder stores one attribute set per distinct blob, so leaving it
    # recorded would write a new row for every schedule every time anything fired
    # — to store, badly and twice, the events D48 already stores well. The
    # accepted consequence is that the attribute shows the last few occurrences
    # *since Home Assistant started* and nothing older; the older answer is a
    # recorder query against `almanac_occurrence`, which is the trade D48 makes.
    _unrecorded_attributes = frozenset({ATTR_RECENT_EXECUTIONS})

    @property
    def extra_state_attributes(self) -> dict[str, Any]:
        """D52's last-N executions, newest first.

        On the switch rather than on the sensor because D54 makes this the entity
        a schedule *is*: it is the one the D49 event names, the one the logbook
        rows attach to, and therefore the one a person opens to ask what this
        schedule has been doing. The rejected alternative was the next-trigger
        sensor, which would have made a `device_class: timestamp` entity's
        more-info dialog mostly about something other than its own value.
        """
        status = self.status
        return {
            ATTR_RECENT_EXECUTIONS: (
                status.async_recent(self.schedule_id) if status is not None else []
            )
        }

    @property
    def is_on(self) -> bool:
        """Whether the schedule is armed.

        The state is a projection of the stored `enabled` field rather than
        entity state restored at startup: §2 puts `enabled` on the Schedule, so
        the code view and the switch cannot disagree, and there is no window
        after a restart in which the switch has not yet decided what it is.
        """
        return self.schedule_enabled

    async def async_turn_on(self, **kwargs: Any) -> None:
        """Arm the schedule."""
        await self._async_set_enabled(True)

    async def async_turn_off(self, **kwargs: Any) -> None:
        """Disarm the schedule.

        D47 — a schedule that stops does not stop mid-interval; it finishes the
        current interval and runs its exit path first. That is engine behaviour
        and lands with the rule engine in step 3, so today this only writes the
        flag. It is called out here because the obvious later change is to make
        this method do the teardown itself, and the teardown belongs to the
        engine: a switch that can leave the lights on is the failure D47 names.
        """
        await self._async_set_enabled(False)

    async def _async_set_enabled(self, enabled: bool) -> None:
        """Write the armed flag back through the collection.

        Going through the collection rather than setting an attribute is what
        makes the change persist, notify the sensor entity and reach any open
        editor, all through the one path D33 established.
        """
        if self.schedule_enabled is enabled:
            return
        await self._collection.async_update_item(
            self.schedule_id, {CONF_ENABLED: enabled}
        )
