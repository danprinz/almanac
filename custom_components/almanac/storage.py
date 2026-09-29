"""The schedule collection and the migration hook it carries from day one.

D33 — one `.storage`-backed collection, no `YamlCollection`. This is not a
preference: `helpers/collection.py`'s websocket CRUD operates exclusively on the
storage collection and never sees a paired YAML one, so a YAML-defined schedule
would have no update or delete path and would be un-editable from the UI by
construction (A.6). That breaks §9.1's parity rule before a line of frontend
exists. The code view is the automation editor's "Edit in YAML" pattern — the
same object rendered as text, not a second source of truth.

D37 — the migration hook and the schema version exist from the first commit,
because a store written without a version makes the first migration guesswork.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from typing import Any, cast

import voluptuous as vol

from homeassistant.core import HomeAssistant, callback
from homeassistant.helpers import entity_registry as er
from homeassistant.helpers.collection import (
    DictStorageCollection,
    DictStorageCollectionWebsocket,
)
from homeassistant.helpers.storage import Store
from homeassistant.util.ulid import ulid_now

from .const import (
    CONF_NAME,
    CONF_OBJECT_ID,
    RUNTIME_VERSION,
    SCHEMA_VERSION,
    SCHEMA_VERSION_MINOR,
    SENSOR_OBJECT_ID_SUFFIX,
    STORAGE_KEY_RUNTIME,
    STORAGE_KEY_SCHEDULES,
    WS_PREFIX_SCHEDULE,
)
from .resolver import ResolverRegistry
from .schema import (
    CREATE_FIELDS,
    CREATE_SCHEMA,
    STORAGE_SCHEMA,
    UPDATE_FIELDS,
    UPDATE_SCHEMA,
    suggested_object_id,
)

_LOGGER = logging.getLogger(__name__)

# The entity domains a schedule occupies, and therefore the ones a proposed
# object_id has to be free in. D54 gives the switch, D55 the sensor; a slug that
# is free in one and taken in the other is not usable, and finding that out
# after the switch has been created is worse than refusing up front.
_OCCUPIED_DOMAINS: tuple[tuple[str, str], ...] = (
    ("switch", ""),
    ("sensor", SENSOR_OBJECT_ID_SUFFIX),
)


@callback
def occupied_entity_ids(object_id: str) -> list[str]:
    """Return every entity_id a schedule with this object_id would claim."""
    return [
        f"{domain}.{object_id}{suffix}" for domain, suffix in _OCCUPIED_DOMAINS
    ]


class ScheduleStore(Store[dict[str, Any]]):
    """The schedule store, with D37's migration hook wired from the first commit.

    There is nothing to migrate at version 1. The hook is here anyway because
    the cost of adding it now is this docstring, and the cost of adding it later
    is reconstructing what an unversioned file meant.
    """

    async def _async_migrate_func(
        self,
        old_major_version: int,
        old_minor_version: int,
        old_data: dict[str, Any],
    ) -> dict[str, Any]:
        """Migrate stored schedules forward to the current schema version.

        The 0.x period declared by D37 means there is no compatibility
        obligation yet, but a downgrade still has to fail loudly rather than
        silently discard fields a newer build wrote.
        """
        if old_major_version > SCHEMA_VERSION:
            raise NotImplementedError(
                f"Cannot downgrade {STORAGE_KEY_SCHEDULES} from schema version "
                f"{old_major_version} to {SCHEMA_VERSION}"
            )
        return old_data


class ScheduleCollection(DictStorageCollection):
    """CRUD over schedules, with D66 enforced by the shape of the schemas."""

    CREATE_SCHEMA = CREATE_SCHEMA
    UPDATE_SCHEMA = UPDATE_SCHEMA

    async def _process_create_data(self, data: dict[str, Any]) -> dict[str, Any]:
        """Validate a new schedule and settle its entity_id suggestion.

        D66 — the slug is pre-filled from the name and may be overwritten here,
        at creation, and only here. The one constraint is collision, and it is
        checked against both domains the schedule will occupy so that the user
        finds out in the form rather than after a half-created schedule.
        """
        validated = cast(dict[str, Any], self.CREATE_SCHEMA(data))
        # HA's `slugify` never returns an empty string for a non-empty name: a
        # name made entirely of punctuation comes back as "unknown". That is a
        # poor entity_id, and it is deliberately not special-cased here — D66
        # puts the fix in the form, where the user can see the suggestion and
        # overwrite it, rather than in a rule that silently invents a better one
        # than they asked for. The second such schedule collides and is refused,
        # which is the behaviour a name carrying no letters deserves.
        object_id = validated.get(CONF_OBJECT_ID) or suggested_object_id(
            validated[CONF_NAME]
        )
        _async_check_object_id_free(self.hass, object_id)
        validated[CONF_OBJECT_ID] = object_id
        return validated

    @callback
    def _get_suggested_id(self, info: dict[str, Any]) -> str:
        """Mint an opaque id, deliberately unrelated to the name and the slug.

        D66 wants the display name, the entity_id and the permanent identity to
        be three independent strings. Core's usual pattern suggests the *name*
        here, which makes the collection id — and so the `unique_id` — a slug of
        whatever the schedule was first called. That is a fourth name-derived
        string to keep honest, so a ULID is used instead and the question does
        not arise.
        """
        return ulid_now()

    async def _update_data(
        self, item: dict[str, Any], update_data: dict[str, Any]
    ) -> dict[str, Any]:
        """Apply an edit, which structurally cannot touch the entity_id.

        `UPDATE_SCHEMA` is PREVENT_EXTRA and has no `object_id` key, so a
        request carrying one is rejected before the merge below runs. This is
        the load-bearing half of D66: re-slugging on rename would break every
        automation and template referencing the old entity_id, later and without
        warning, and a convention would have held only until someone wrote this
        same two-line merge somewhere else.
        """
        update = cast(dict[str, Any], self.UPDATE_SCHEMA(update_data))
        return item | update


class RuntimeStore:
    """Counters and last-run facts, in their own store (D35).

    Nothing here is written by step 1 — the engine (steps 3 and 5) owns the
    contents. The store exists now because D35 is a storage decision, and
    because "we will split it out later" is how a completion counter ends up
    inside the object an editor overwrites.
    """

    def __init__(self, hass: HomeAssistant) -> None:
        """Set up the runtime store."""
        self._store: Store[dict[str, Any]] = Store(
            hass, RUNTIME_VERSION, STORAGE_KEY_RUNTIME
        )
        self._data: dict[str, dict[str, Any]] = {}

    async def async_load(self) -> None:
        """Load runtime state."""
        self._data = (await self._store.async_load()) or {}

    @callback
    def async_get(self, schedule_id: str) -> dict[str, Any]:
        """Return the runtime state recorded for a schedule."""
        return self._data.get(schedule_id, {})

    @callback
    def async_set(self, schedule_id: str, state: dict[str, Any]) -> None:
        """Record runtime state for a schedule.

        D64 applies to every caller of this: the instant a run happened is a
        value the tick passed down, never one read here.
        """
        self._data[schedule_id] = state
        self._store.async_delay_save(lambda: self._data, 10)

    @callback
    def async_discard(self, schedule_id: str) -> None:
        """Drop the runtime state of a deleted schedule."""
        if self._data.pop(schedule_id, None) is not None:
            self._store.async_delay_save(lambda: self._data, 10)


@callback
def _async_check_object_id_free(hass: HomeAssistant, object_id: str) -> None:
    """Refuse an object_id that any of the schedule's entity_ids would collide with.

    D66 says the only constraint on the suggestion is collision. Note what is
    *not* done here: nothing appends `_2` to make room. Core's
    `async_generate_entity_id` would, and that is right for a machine-generated
    id and wrong for one a person typed — silently storing `shabbat_lights_2`
    when they asked for `shabbat_lights` produces exactly the un-findable entity
    D54 exists to eliminate.
    """
    registry = er.async_get(hass)
    for entity_id in occupied_entity_ids(object_id):
        if hass.states.get(entity_id) is not None or registry.async_is_registered(
            entity_id
        ):
            raise vol.Invalid(f"The entity id {entity_id} is already taken")


async def async_setup_collection(hass: HomeAssistant) -> ScheduleCollection:
    """Create, load and expose the schedule collection."""
    store = ScheduleStore(
        hass,
        SCHEMA_VERSION,
        STORAGE_KEY_SCHEDULES,
        minor_version=SCHEMA_VERSION_MINOR,
    )
    collection = ScheduleCollection(store)
    await collection.async_load()
    _async_validate_loaded(collection)

    DictStorageCollectionWebsocket(
        collection,
        WS_PREFIX_SCHEDULE,
        "schedule",
        CREATE_FIELDS,
        UPDATE_FIELDS,
    ).async_setup(hass)

    return collection


@callback
def _async_validate_loaded(collection: ScheduleCollection) -> None:
    """Check what came off disk, and drop — loudly — what cannot be read.

    A schedule that fails validation at load is not repairable by the engine and
    would fail again at every tick. Refusing to set the integration up over one
    bad item would take every other schedule down with it, which is the failure
    mode D17 rejects for resolvers and there is no reason to accept it here.
    """
    for item_id, item in list(collection.data.items()):
        try:
            collection.data[item_id] = STORAGE_SCHEMA(item)
        except vol.Invalid as err:
            _LOGGER.error(
                "Dropping unreadable schedule %s (%s): %s",
                item.get(CONF_NAME, item_id),
                item_id,
                err,
            )
            del collection.data[item_id]


@dataclass(slots=True)
class AlmanacData:
    """What the config entry hands to the platforms.

    D35's two stores, side by side and still separate: an editor writes through
    `schedules`, the engine writes through `runtime`, and neither can reset the
    other's fields by saving at the wrong moment.
    """

    schedules: ScheduleCollection
    runtime: RuntimeStore
    # D14 — one internal registry per config entry, with no discovery hook. It
    # lives here rather than in `hass.data` so that the thing which resolves a
    # schedule's anchors has the same lifetime as the schedules themselves.
    resolvers: ResolverRegistry


__all__ = [
    "AlmanacData",
    "RuntimeStore",
    "ScheduleCollection",
    "ScheduleStore",
    "async_setup_collection",
    "occupied_entity_ids",
]
