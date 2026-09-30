"""The day-set collection — D18's "first class", made structural.

D18 says a day set is its own object with its own storage and its own lifecycle,
not a field inside a schedule. The alternative would have been cheaper today and
wrong by next week: the whole point of a day set is that *several* schedules share
one definition of "Shabbat", and a definition living inside one of its users has
no answer to "which schedules does editing this affect" (D22) and nothing for D21
to hang a `binary_sensor` on.

Three things this file is responsible for that the schema cannot be:

- **D19's one level of composition, checked in both directions.** Whether a member
  is itself a composition is a fact about a *different* stored item, so a
  voluptuous validator cannot see it. And the check has to run on edits to the
  *member* too, not only on the composition: promoting a plain day set to a
  composition would otherwise create the second level from the far end, which is
  how a one-level rule quietly stops being one.
- **D66's slug discipline**, via the same `async_settle_object_id` the schedule
  collection uses. A day set claims one `binary_sensor` (D21), and that entity_id
  must not move when the set is renamed.
- **Loading defensively.** An unreadable day set is dropped with a log rather than
  failing the integration's setup, for the reason D17 gives about resolvers: one
  bad item must not take the other twenty down.

What this file deliberately does *not* do is refuse to delete a day set that
schedules reference. A dangling reference degrades those schedules' occurrences to
`Unresolved` (see `engine/day_set.py`), which is visible and repairable, where a
delete the UI refuses is a day set the user can never get rid of. D22's impact
preview is the mitigation the design chose, and it is the honest one.
"""

from __future__ import annotations

import logging
from typing import Any, cast

import voluptuous as vol

from homeassistant.const import CONF_ID
from homeassistant.core import HomeAssistant, callback
from homeassistant.helpers.collection import (
    DictStorageCollection,
    DictStorageCollectionWebsocket,
)
from homeassistant.helpers.storage import Store
from homeassistant.util.ulid import ulid_now

from .const import (
    CONF_KIND,
    CONF_MEMBERS,
    CONF_NAME,
    CONF_SOURCE,
    SCHEMA_VERSION,
    SCHEMA_VERSION_MINOR,
    SOURCE_COMPOSITION,
    STORAGE_KEY_DAY_SETS,
    WS_PREFIX_DAY_SET,
)
from .schema import (
    DAY_SET_CREATE_FIELDS,
    DAY_SET_CREATE_SCHEMA,
    DAY_SET_STORAGE_SCHEMA,
    DAY_SET_UPDATE_FIELDS,
    DAY_SET_UPDATE_SCHEMA,
)
from .storage import async_settle_object_id

_LOGGER = logging.getLogger(__name__)

# D21 — one `binary_sensor` per day set, whose state is whether the set covers
# *now*. One domain rather than the schedule's two, but routed through the same
# collision check so the two collections cannot disagree about what "taken" means.
DAY_SET_PLATFORM_DOMAIN = "binary_sensor"


@callback
def day_set_entity_ids(object_id: str) -> list[str]:
    """Return every entity_id a day set with this object_id would claim."""
    return [f"{DAY_SET_PLATFORM_DOMAIN}.{object_id}"]


class DaySetStore(Store[dict[str, Any]]):
    """The day-set store, sharing the schema version with the schedule store.

    Same version, separate file. D37's version numbers the integration's storage
    *shape*, not one file's contents: a migration that changes how a recurrence is
    spelled has to change it in both places at once, because D20 makes them the
    same four generators. Two independently-versioned stores would let a migration
    land on one and not the other, and the resulting store would be internally
    inconsistent in a way no single version number could describe.
    """

    async def _async_migrate_func(
        self,
        old_major_version: int,
        old_minor_version: int,
        old_data: dict[str, Any],
    ) -> dict[str, Any]:
        """Migrate stored day sets forward to the current schema version."""
        if old_major_version > SCHEMA_VERSION:
            raise NotImplementedError(
                f"Cannot downgrade {STORAGE_KEY_DAY_SETS} from schema version "
                f"{old_major_version} to {SCHEMA_VERSION}"
            )
        return old_data


class DaySetCollection(DictStorageCollection):
    """CRUD over day sets, with D19's one level enforced at both ends."""

    CREATE_SCHEMA = DAY_SET_CREATE_SCHEMA
    UPDATE_SCHEMA = DAY_SET_UPDATE_SCHEMA

    async def _process_create_data(self, data: dict[str, Any]) -> dict[str, Any]:
        """Validate a new day set, settle its slug, and check D19."""
        validated = cast(dict[str, Any], self.CREATE_SCHEMA(data))
        async_settle_object_id(self.hass, validated, day_set_entity_ids)
        # No id yet — the collection mints one after this returns — so a create
        # cannot be self-referential and only the downward check applies.
        self._check_composition(validated[CONF_SOURCE], item_id=None)
        return validated

    @callback
    def _get_suggested_id(self, info: dict[str, Any]) -> str:
        """Mint an opaque id, for D66's reason (see `ScheduleCollection`)."""
        return ulid_now()

    async def _update_data(
        self, item: dict[str, Any], update_data: dict[str, Any]
    ) -> dict[str, Any]:
        """Apply an edit, refusing one that would create a second level (D19)."""
        update = cast(dict[str, Any], self.UPDATE_SCHEMA(update_data))
        merged = item | update
        item_id = item[CONF_ID]
        # Downward: does this set now reference a composition, or itself?
        self._check_composition(merged[CONF_SOURCE], item_id=item_id)
        # Upward: is this set a member of some composition, and has this edit just
        # turned it into a composition? Checked on edits because the promotion is
        # the same illegal shape approached from the other side, and only the
        # collection can see both ends of it.
        if merged[CONF_SOURCE][CONF_KIND] == SOURCE_COMPOSITION:
            self._check_not_a_member(item_id)
        return merged

    @callback
    def async_get_day_set(self, day_set_id: str) -> dict[str, Any] | None:
        """Return a stored day set, satisfying `engine.DaySetLookup`.

        The engine reaches day sets through this one method and nothing else, so
        D22's preview can substitute a different answer without touching the store
        (see `DaySetLookup`'s docstring).
        """
        return self.data.get(day_set_id)

    @callback
    def _check_composition(
        self, source: dict[str, Any], *, item_id: str | None
    ) -> None:
        """Refuse a composition whose members are not plain day sets (D19).

        A missing member is *allowed* here, deliberately: the create form may
        reference a set that exists, and a later delete of that set is permitted
        (see the module docstring), so refusing an unknown id at write time would
        be a rule the store cannot keep. What is refused is a member that exists
        and is itself a composition, and a member that is this set.
        """
        if source[CONF_KIND] != SOURCE_COMPOSITION:
            return
        for member_id in source[CONF_MEMBERS]:
            if item_id is not None and member_id == item_id:
                raise vol.Invalid("A day set cannot be a member of itself")
            member = self.data.get(member_id)
            if member is None:
                continue
            if member[CONF_SOURCE][CONF_KIND] == SOURCE_COMPOSITION:
                raise vol.Invalid(
                    f"{member[CONF_NAME]} is itself a combination, and a "
                    "combination can only be built from plain day sets"
                )

    @callback
    def _check_not_a_member(self, item_id: str) -> None:
        """Refuse turning a day set into a composition while something composes it."""
        for other in self.data.values():
            source = other[CONF_SOURCE]
            if source[CONF_KIND] != SOURCE_COMPOSITION:
                continue
            if item_id in source[CONF_MEMBERS]:
                raise vol.Invalid(
                    f"{other[CONF_NAME]} is built from this day set, so this one "
                    "cannot itself become a combination"
                )


async def async_setup_day_sets(hass: HomeAssistant) -> DaySetCollection:
    """Create, load and expose the day-set collection."""
    store = DaySetStore(
        hass,
        SCHEMA_VERSION,
        STORAGE_KEY_DAY_SETS,
        minor_version=SCHEMA_VERSION_MINOR,
    )
    collection = DaySetCollection(store)
    await collection.async_load()
    _async_validate_loaded(collection)

    DictStorageCollectionWebsocket(
        collection,
        WS_PREFIX_DAY_SET,
        "day_set",
        DAY_SET_CREATE_FIELDS,
        DAY_SET_UPDATE_FIELDS,
    ).async_setup(hass)

    return collection


@callback
def _async_validate_loaded(collection: DaySetCollection) -> None:
    """Drop — loudly — what cannot be read, for `storage.py`'s reason.

    Note that D19 is *not* re-checked here. A store containing a nested
    composition still loads, and `engine/day_set.py` returns `Unresolved` naming
    D19 when it is evaluated. Dropping the item instead would make an upgrade
    destructive, and the nested set is still something the user can see and fix.
    """
    for item_id, item in list(collection.data.items()):
        try:
            collection.data[item_id] = DAY_SET_STORAGE_SCHEMA(item)
        except vol.Invalid as err:
            _LOGGER.error(
                "Dropping unreadable day set %s (%s): %s",
                item.get(CONF_NAME, item_id),
                item_id,
                err,
            )
            del collection.data[item_id]


__all__ = [
    "DaySetCollection",
    "DaySetStore",
    "async_setup_day_sets",
    "day_set_entity_ids",
]
