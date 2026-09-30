"""D21 — one `binary_sensor` per day set, whose state is whether the set covers *now*.

This is the entity that makes a day set useful outside almanac. Once "Shabbat" is
a `binary_sensor`, every automation, template and dashboard condition in Home
Assistant can use it without knowing this integration exists — which is the whole
argument for publishing it rather than keeping the answer internal to the engine.

**The state is written by the tick, not computed here.** D64 forbids this file from
asking what time it is, and "covers *now*" is a question about the clock, so the
only honest implementation is a value the top-level tick pushes in. Until it does,
`is_on` is `None` and the entity renders `unknown` — verified against the installed
2026.9.4 `BinarySensorEntity`, whose `_attr_is_on` is typed `bool | None` and whose
`state` property returns `None` for that case, so this is the documented shape and
not a trick.

That is the same choice `AlmanacNextTriggerSensor` makes and for the same reason,
and it is worth stating plainly: an entity that read the clock itself would be a
second implementation of `covers`, and a `binary_sensor` that disagreed with the
timeline drawn from the same day set would be worse than no `binary_sensor`.
"""

from __future__ import annotations

from typing import Any

from homeassistant.components.binary_sensor import BinarySensorEntity
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant, callback
from homeassistant.helpers.entity_platform import AddConfigEntryEntitiesCallback

from .const import CONF_DESCRIPTION, CONF_OWNER, CONF_SOURCE
from .day_sets import DAY_SET_PLATFORM_DOMAIN
from .entity import AlmanacCollectionEntity, async_setup_collection_platform
from .storage import AlmanacData


async def async_setup_entry(
    hass: HomeAssistant,
    entry: ConfigEntry,
    async_add_entities: AddConfigEntryEntitiesCallback,
) -> None:
    """Set up one binary_sensor per day set."""
    data: AlmanacData = entry.runtime_data
    entry.async_on_unload(
        await async_setup_collection_platform(
            hass,
            data.day_sets,
            DAY_SET_PLATFORM_DOMAIN,
            AlmanacDaySetBinarySensor,
            async_add_entities,
        )
    )


class AlmanacDaySetBinarySensor(AlmanacCollectionEntity, BinarySensorEntity):
    """Whether a day set is in force at the instant the tick last evaluated."""

    _platform_domain = DAY_SET_PLATFORM_DOMAIN
    _attr_icon = "mdi:calendar-check"

    def __init__(
        self, collection: Any, item: dict[str, Any], data: Any = None
    ) -> None:
        """Set up the entity with no opinion about the current instant.

        `data` is accepted and ignored: D52's execution cache and D55's next
        trigger are facts about a *schedule*, and a day set is neither. The
        parameter is here because `async_setup_collection_platform` builds every
        collection entity the same way, and a second construction path would be a
        second place for the identity discipline in `entity.py` to drift.
        """
        self._covers: bool | None = None
        super().__init__(collection, item, data)

    @property
    def is_on(self) -> bool | None:
        """Whether the day set covers the instant the tick last supplied.

        `None` — rendered as `unknown` — until the tick has evaluated the set
        once. Not `False`, which would be a claim: "today is not in this set" and
        "nobody has asked yet" are different facts, and an automation gated on
        this entity deserves to be able to tell them apart across a restart.
        """
        return self._covers

    @property
    def extra_state_attributes(self) -> dict[str, Any]:
        """What the set is and who put it there.

        `owner` is here because D18 records it and does not enforce it: a user
        about to edit a day set an integration shipped should be able to see that
        before they change it, and an attribute is the cheapest place that fact is
        visible without opening the editor. `source` is published as its stored
        shape so the day set is self-describing from the entity page — the same
        reasoning as the code view in §9.1, at a smaller scale.
        """
        return {
            CONF_DESCRIPTION: self._item[CONF_DESCRIPTION],
            CONF_OWNER: self._item[CONF_OWNER],
            CONF_SOURCE: self._item[CONF_SOURCE],
        }

    @callback
    def async_set_covers(self, covers: bool | None) -> None:
        """Record what the tick computed, and publish it.

        The only way this entity's state changes. `covers` may be `None`, which is
        how an unresolvable day set — a dangling composition member, a resolver
        that timed out (D17) — reports itself: back to `unknown` rather than
        stuck on its last answer, because a stale `on` is indistinguishable from a
        current one to everything downstream.
        """
        if self._covers is covers:
            return
        self._covers = covers
        self.async_write_ha_state()
