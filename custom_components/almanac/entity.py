"""The entity model: one entity per collection item per platform, and how they track the collection.

Core ships `collection.sync_entity_lifecycle` for exactly this job, but it takes
an `EntityComponent` — the helper-integration shape, where the integration owns
its own entity domain. almanac's entities live in the `switch`, `sensor` and
`binary_sensor` domains and arrive through a config entry, so they come in via
`async_add_entities`. `async_setup_collection_platform` below is that same life
cycle against a config-entry platform; it is deliberately a transcription of
core's `_CollectionLifeCycle` rather than an improvement on it, including the
detail that removal goes through the entity registry when the entity is
registered and through `async_remove` when it is not.

Two collections feed it — schedules (D54, D55) and day sets (D21) — and they share
one base class because they share one identity discipline. `AlmanacCollectionEntity`
is that discipline and nothing else: it knows about `id`, `object_id` and `name`,
which is exactly the set of fields both collections have in common, and it is
deliberately ignorant of rules, recurrences and sources.
"""

from __future__ import annotations

from collections.abc import Callable, Iterable
from typing import Any

from homeassistant.const import CONF_ID
from homeassistant.core import HomeAssistant, callback
from homeassistant.helpers import entity_registry as er
from homeassistant.helpers.collection import (
    CHANGE_ADDED,
    CHANGE_REMOVED,
    CHANGE_UPDATED,
    CollectionChange,
    DictStorageCollection,
)
from homeassistant.helpers.entity import Entity
from homeassistant.helpers.entity_platform import AddConfigEntryEntitiesCallback

from .const import CONF_ENABLED, CONF_NAME, CONF_OBJECT_ID, DOMAIN


class AlmanacCollectionEntity(Entity):
    """Base for every entity almanac builds from a stored collection item.

    Three strings, three audiences, none derived from another after creation:

    - `unique_id` is the collection item id — an opaque ULID, so nothing about
      the item's name or its entity_id can drift into its permanent identity
      (D37 requires the unique_id from the first commit; a unique_id added later
      cannot rename entities that already exist).
    - `entity_id` comes from the stored `object_id` and is computed exactly
      once, here, from a value the update schema cannot change (D66).
    - the friendly name is the item's name and is free to change.
    """

    _attr_should_poll = False

    # The entity domain this class belongs to, and the suffix it appends to the
    # item's object_id. The suffix is empty for the switch, which is the entity
    # the schedule *is*, and for the day set's binary_sensor, which is likewise
    # the entity the day set is.
    _platform_domain: str
    _object_id_suffix = ""

    def __init__(
        self, collection: DictStorageCollection, item: dict[str, Any]
    ) -> None:
        """Set up an entity for a collection item."""
        self._collection = collection
        self._item = item
        self._attr_unique_id = item[CONF_ID]
        # D66's second half, and the reason this is a plain f-string rather than
        # a call to `async_generate_entity_id`: that helper dedupes by appending
        # `_2`, which is right for a machine-generated id and wrong for one a
        # person chose. Collisions are refused at create time instead
        # (`storage.async_check_entity_ids_free`), so there is nothing to dedupe
        # by the time an entity is built.
        self.entity_id = (
            f"{self._platform_domain}."
            f"{item[CONF_OBJECT_ID]}{self._object_id_suffix}"
        )
        self._apply_item(item)

    @property
    def item_id(self) -> str:
        """The collection item id behind this entity."""
        return self._item[CONF_ID]

    @callback
    def _apply_item(self, item: dict[str, Any]) -> None:
        """Adopt a new version of the stored item.

        Subclasses extend this; note what it must never do — recompute
        `entity_id`. A rename arrives through here, and re-slugging on rename is
        precisely the silent breakage D66 forbids.
        """
        self._item = item
        self._attr_name = self._friendly_name(item[CONF_NAME])

    @callback
    def _friendly_name(self, name: str) -> str:
        """The name shown to a human. Free to change, unlike the other two ids."""
        return name

    async def async_update_config(self, item: dict[str, Any]) -> None:
        """Handle an edit to the item behind this entity."""
        self._apply_item(item)
        self.async_write_ha_state()


class AlmanacScheduleEntity(AlmanacCollectionEntity):
    """Base for the entities D54 and D55 give every schedule."""

    @property
    def schedule_id(self) -> str:
        """The collection item id of the schedule behind this entity."""
        return self.item_id

    @property
    def schedule_enabled(self) -> bool:
        """Whether the schedule is armed. §2 puts `enabled` on the Schedule."""
        return bool(self._item[CONF_ENABLED])


async def async_setup_collection_platform(
    hass: HomeAssistant,
    collection: DictStorageCollection,
    platform_domain: str,
    entity_class: type[AlmanacCollectionEntity],
    async_add_entities: AddConfigEntryEntitiesCallback,
) -> Callable[[], None]:
    """Keep one entity of `entity_class` alive per collection item.

    Returns the unsubscribe callable so the platform stops tracking the
    collection when the config entry unloads.
    """
    entities: dict[str, AlmanacCollectionEntity] = {}
    registry = er.async_get(hass)

    @callback
    def _add(item: dict[str, Any]) -> AlmanacCollectionEntity:
        entity = entity_class(collection, item)
        entities[item[CONF_ID]] = entity
        entity.async_on_remove(lambda: entities.pop(item[CONF_ID], None))
        return entity

    async def _remove(item_id: str) -> None:
        # Removing from the registry is what makes a delete permanent; removing
        # the entity object alone leaves a registry row that reserves the
        # entity_id forever, which would make deleting and recreating a
        # schedule under the same name fail the D66 collision check.
        if (
            entity_id := registry.async_get_entity_id(
                platform_domain, DOMAIN, item_id
            )
        ) is not None:
            registry.async_remove(entity_id)
        elif entity := entities.get(item_id):
            await entity.async_remove(force_remove=True)
        entities.pop(item_id, None)

    async def _changed(change_set: Iterable[CollectionChange]) -> None:
        new_entities: list[AlmanacCollectionEntity] = []
        for change in change_set:
            if change.change_type == CHANGE_ADDED:
                new_entities.append(_add(change.item))
            elif change.change_type == CHANGE_REMOVED:
                await _remove(change.item_id)
            elif change.change_type == CHANGE_UPDATED:
                if entity := entities.get(change.item_id):
                    await entity.async_update_config(change.item)
        if new_entities:
            async_add_entities(new_entities)

    # The collection is loaded before the platforms are forwarded, so the
    # already-present items are added here rather than arriving as changes.
    async_add_entities([_add(item) for item in collection.async_items()])

    return collection.async_add_change_set_listener(_changed)
