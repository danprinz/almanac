"""almanac — a schedule engine whose rule model is enumerable.

Set-up order for DESIGN.md §15's build: the storage collections and the resolver
registry, then the platforms, then the tick — and the tick last because it is the
only part that acts on the world.

Two constraints are honoured from the first function because neither can be
retrofitted:

- **D64 — nothing below the top-level scheduler tick reads a clock.** `now` is a
  parameter. As of step 5 the tick exists, and it is the *only* module in this
  package that reads a clock: `tests/test_design_constraints.py` sweeps the source
  for clock reads and names `tick.py` as the one exception. The timeline, the dry
  run and the live engine are one code path evaluated at three instants, and that
  is only true because no layer beneath the tick has a clock of its own.
- **D66 — the slugged entity_id is a suggestion, editable at creation, never
  re-derived.** Made structural rather than conventional: `object_id` exists in
  the create schema and in no other, so an update cannot carry one and the merge
  in `ScheduleCollection._update_data` has nothing to overwrite.
"""

from __future__ import annotations

from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant

from .const import CONFIG_ENTRY_VERSION, DOMAIN, PLATFORMS
from .day_sets import async_setup_day_sets
from .resolver import async_create_registry
from .storage import AlmanacData, RuntimeStore, async_setup_collection
from .tick import SERVICE_RUN_NOW, AlmanacTick, async_register_services
from .websocket import async_register_websocket

type AlmanacConfigEntry = ConfigEntry[AlmanacData]


async def async_setup_entry(hass: HomeAssistant, entry: AlmanacConfigEntry) -> bool:
    """Set up almanac from its one config entry (D65)."""
    schedules = await async_setup_collection(hass)
    # Loaded before the schedules are ever enumerated, because a day-set
    # reference the engine cannot resolve degrades every occurrence that uses it
    # (D12). Two collections rather than one object graph: D18 makes a day set
    # first-class, and the two stores are independent so that a schedule edit
    # cannot rewrite a definition several other schedules share.
    day_sets = await async_setup_day_sets(hass)

    runtime = RuntimeStore(hass)
    await runtime.async_load()

    entry.runtime_data = AlmanacData(
        schedules=schedules,
        day_sets=day_sets,
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

    # The tick is started last, and it then defers its first pass to
    # `async_at_started`. Both delays are D41's: the recovery pass reconciles every
    # interval that is in force, which means applying desired state, which needs
    # the target entities to exist. Reconciling against a half-assembled world
    # would report most of it as missing.
    tick = AlmanacTick(hass, entry.runtime_data)
    entry.runtime_data.tick = tick
    await tick.async_start()
    async_register_services(hass, tick)

    # D63's timeline and D64's dry run, after the tick because the dry run is a
    # method on it. Registered rather than unregistered on unload, like the CRUD
    # commands `helpers/collection.py` installs: websocket commands live in one
    # per-`hass` table with no removal API, the handlers look the entry up per call
    # and answer "almanac is not loaded" when it is not, and re-registering on a
    # reload simply replaces the entry in that table.
    async_register_websocket(hass)
    return True


async def async_unload_entry(hass: HomeAssistant, entry: AlmanacConfigEntry) -> bool:
    """Unload the config entry.

    The tick drops its subscriptions and deliberately runs no exit paths — an
    unload is not a schedule ending. See `AlmanacTick.async_shutdown`; the runtime
    store is what carries held intervals across it, and D90's `RESUME` is what picks
    them up on the way back.
    """
    if (tick := entry.runtime_data.tick) is not None:
        tick.async_shutdown()
        entry.runtime_data.tick = None
    # D65 — one config entry, so unloading it means nothing is left to serve the
    # service. Registering in setup and removing here keeps the two symmetrical
    # rather than leaving a service that raises on every call.
    hass.services.async_remove(DOMAIN, SERVICE_RUN_NOW)
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
