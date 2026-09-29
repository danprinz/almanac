"""almanac — a schedule engine whose rule model is enumerable.

Build step 1 of DESIGN.md §15: schema, storage collection, entity model, config
flow. The engine, the resolvers, conditions, actions and every frontend surface
land in later steps; what is here is the shape they attach to.

Two constraints are honoured from the first function because neither can be
retrofitted:

- **D64 — nothing below the top-level scheduler tick reads a clock.** `now` is a
  parameter. Step 1 has no tick, so what it can do is decline to introduce the
  habit: nothing in this package calls `dt_util.now()` or `utcnow()`, and the
  sensor that will one day hold the next trigger returns `None` rather than
  computing anything for itself (see `sensor.py`). The timeline, the dry run and
  the live engine are meant to be one code path evaluated at three instants, and
  that is only true if no layer has a clock of its own.
- **D66 — the slugged entity_id is a suggestion, editable at creation, never
  re-derived.** Made structural rather than conventional: `object_id` exists in
  the create schema and in no other, so an update cannot carry one and the merge
  in `ScheduleCollection._update_data` has nothing to overwrite.
"""

from __future__ import annotations

from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant

from .const import CONFIG_ENTRY_VERSION, PLATFORMS
from .resolver import async_create_registry
from .storage import AlmanacData, RuntimeStore, async_setup_collection

type AlmanacConfigEntry = ConfigEntry[AlmanacData]


async def async_setup_entry(hass: HomeAssistant, entry: AlmanacConfigEntry) -> bool:
    """Set up almanac from its one config entry (D65)."""
    schedules = await async_setup_collection(hass)

    runtime = RuntimeStore(hass)
    await runtime.async_load()

    entry.runtime_data = AlmanacData(
        schedules=schedules,
        runtime=runtime,
        # D14 keeps the registry internal for v1, which is why it is built here
        # rather than discovered: the moment a third party can ship a resolver,
        # the contract becomes a compatibility commitment, and it has not yet
        # met its second implementation (step 7's `hdate`).
        resolvers=async_create_registry(hass),
    )

    # The collection is loaded before the platforms are forwarded, so each
    # platform sees the full set of schedules as existing items rather than as a
    # burst of additions it has to race.
    await hass.config_entries.async_forward_entry_setups(entry, PLATFORMS)
    return True


async def async_unload_entry(hass: HomeAssistant, entry: AlmanacConfigEntry) -> bool:
    """Unload the config entry."""
    return await hass.config_entries.async_unload_platforms(entry, PLATFORMS)


async def async_migrate_entry(hass: HomeAssistant, entry: AlmanacConfigEntry) -> bool:
    """Migrate a config entry forward.

    D37 pairs this with the store's own migration hook. The entry carries no
    data today — D65 means there is nothing per-instance to configure — so there
    is nothing to move; the hook is here so that the first time there is, the
    place to put it already exists and has a version to key off.
    """
    if entry.version > CONFIG_ENTRY_VERSION:
        return False
    return True
