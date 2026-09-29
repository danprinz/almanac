"""The storage collection: create, update, delete, and what survives a reload.

D33 — one `.storage`-backed collection with websocket CRUD, no `YamlCollection`.
The websocket half is exercised here rather than assumed, because it is the
whole justification for D33: core's CRUD commands touch only the storage
collection, so anything not reachable through them is un-editable from the UI by
construction (A.6).
"""

from __future__ import annotations

from typing import Any

import pytest
import voluptuous as vol

from homeassistant.core import HomeAssistant
from homeassistant.helpers import entity_registry as er

from pytest_homeassistant_custom_component.common import MockConfigEntry
from pytest_homeassistant_custom_component.typing import WebSocketGenerator

from custom_components.almanac.const import SCHEMA_VERSION, STORAGE_KEY_SCHEDULES
from custom_components.almanac.storage import (
    AlmanacData,
    ScheduleStore,
    occupied_entity_ids,
)


async def test_create_update_delete(almanac_data: AlmanacData) -> None:
    """The CRUD path, end to end, through the collection itself."""
    collection = almanac_data.schedules
    assert collection.async_items() == []

    item = await collection.async_create_item({"name": "Shabbat lights"})
    item_id = item["id"]
    assert item["name"] == "Shabbat lights"
    assert item["object_id"] == "shabbat_lights"
    assert collection.async_items() == [item]

    updated = await collection.async_update_item(item_id, {"name": "Sanctuary lights"})
    assert updated["name"] == "Sanctuary lights"

    await collection.async_delete_item(item_id)
    assert collection.async_items() == []


async def test_update_is_a_patch_not_a_replacement(
    almanac_data: AlmanacData,
) -> None:
    """Editing one field must not silently reset the rest.

    This is the concrete failure the update schema's missing defaults prevent,
    and it is the shape the switch's arm/disarm write takes.
    """
    collection = almanac_data.schedules
    item = await collection.async_create_item(
        {
            "name": "Shabbat lights",
            "rules": [{"kind": "at", "anchor": {"kind": "clock", "at": "17:00"}}],
            "recurrence": {"kind": "weekdays", "weekdays": ["fri"]},
        }
    )

    updated = await collection.async_update_item(item["id"], {"enabled": False})

    assert updated["enabled"] is False
    assert len(updated["rules"]) == 1
    assert updated["recurrence"]["weekdays"] == ["fri"]


async def test_item_id_is_opaque_and_not_derived_from_the_name(
    almanac_data: AlmanacData,
) -> None:
    """The permanent identity is a third string, independent of the other two.

    D66 keeps the display name and the entity_id apart; making the collection id
    a slug of the name would reintroduce the coupling one level down, in the
    `unique_id` that D37 requires from the first commit and that can never be
    changed afterwards.
    """
    item = await almanac_data.schedules.async_create_item({"name": "Shabbat lights"})
    assert "shabbat" not in item["id"].lower()


async def test_object_id_collision_is_refused_not_deduped(
    almanac_data: AlmanacData,
) -> None:
    """D66's one constraint.

    Nothing appends `_2`. Storing `shabbat_lights_2` when the user asked for
    `shabbat_lights` would produce exactly the un-findable entity D54 exists to
    eliminate, and it would do it without saying so.
    """
    collection = almanac_data.schedules
    await collection.async_create_item({"name": "Shabbat lights"})

    with pytest.raises(vol.Invalid, match="already taken"):
        await collection.async_create_item({"name": "Shabbat Lights"})

    with pytest.raises(vol.Invalid, match="already taken"):
        await collection.async_create_item(
            {"name": "Something else", "object_id": "shabbat_lights"}
        )


async def test_collision_is_checked_in_every_domain_the_schedule_occupies(
    hass: HomeAssistant, almanac_data: AlmanacData
) -> None:
    """A slug free for the switch but taken for the sensor is not usable.

    Finding that out after the switch exists is worse than refusing up front.
    """
    registry = er.async_get(hass)
    registry.async_get_or_create(
        "sensor", "demo", "unique", suggested_object_id="shabbat_lights_next_trigger"
    )

    with pytest.raises(vol.Invalid, match="already taken"):
        await almanac_data.schedules.async_create_item({"name": "Shabbat lights"})


async def test_a_name_carrying_no_letters_falls_back_to_unknown(
    almanac_data: AlmanacData,
) -> None:
    """HA's `slugify` returns "unknown", never the empty string.

    Pinned because it is structural and surprising: the suggestion for a name
    made of punctuation is `switch.unknown`. It is not special-cased — D66 puts
    the fix in the form, where the user sees the suggestion and can overwrite
    it. The second such schedule collides and is refused, which is the right
    outcome for a name that carries no information.
    """
    item = await almanac_data.schedules.async_create_item({"name": "!!!"})
    assert item["object_id"] == "unknown"

    with pytest.raises(vol.Invalid, match="already taken"):
        await almanac_data.schedules.async_create_item({"name": "???"})


def test_occupied_entity_ids_covers_d54_and_d55() -> None:
    """The collision check and the entity classes must agree on what is claimed."""
    assert occupied_entity_ids("shabbat_lights") == [
        "switch.shabbat_lights",
        "sensor.shabbat_lights_next_trigger",
    ]


async def test_schedules_survive_a_reload(
    hass: HomeAssistant, setup_almanac: MockConfigEntry
) -> None:
    """What was written is what comes back, defaults and all."""
    collection = setup_almanac.runtime_data.schedules
    item = await collection.async_create_item(
        {
            "name": "Shabbat lights",
            "rules": [{"kind": "at", "anchor": {"kind": "clock", "at": "17:00"}}],
        }
    )
    await hass.async_block_till_done()
    # The collection saves on a delay; force it out so the reload sees it.
    await collection.store.async_save(collection._data_to_save())  # noqa: SLF001

    assert await hass.config_entries.async_reload(setup_almanac.entry_id)
    await hass.async_block_till_done()

    reloaded = setup_almanac.runtime_data.schedules.async_items()
    assert len(reloaded) == 1
    assert reloaded[0] == item


async def test_an_unreadable_schedule_is_dropped_not_fatal(
    hass: HomeAssistant,
    hass_storage: dict[str, Any],
    config_entry: MockConfigEntry,
) -> None:
    """One bad item must not take every other schedule down with it.

    That is the failure mode D17 rejects for resolvers, and there is no reason
    to accept it for storage: a schedule that fails validation would fail again
    at every tick, so refusing to set up merely makes it unrepairable.
    """
    hass_storage[STORAGE_KEY_SCHEDULES] = {
        "version": SCHEMA_VERSION,
        "minor_version": 1,
        "key": STORAGE_KEY_SCHEDULES,
        "data": {
            "items": [
                {
                    "id": "good",
                    "name": "Good",
                    "object_id": "good",
                    "rules": [],
                },
                {"id": "bad", "name": "Bad", "object_id": "bad", "rules": "nonsense"},
            ]
        },
    }
    config_entry.add_to_hass(hass)
    assert await hass.config_entries.async_setup(config_entry.entry_id)
    await hass.async_block_till_done()

    items = config_entry.runtime_data.schedules.async_items()
    assert [item["id"] for item in items] == ["good"]


async def test_store_refuses_a_downgrade(hass: HomeAssistant) -> None:
    """D37's migration hook, doing the only job it has at version 1.

    Silently accepting data from a newer schema means discarding fields the
    newer build wrote, and 0.x freedom to change the model (D37) is not freedom
    to eat somebody's schedules.
    """
    store = ScheduleStore(hass, SCHEMA_VERSION, STORAGE_KEY_SCHEDULES)
    with pytest.raises(NotImplementedError):
        await store._async_migrate_func(  # noqa: SLF001
            SCHEMA_VERSION + 1, 1, {"items": []}
        )


# --- websocket CRUD (D33) --------------------------------------------------


async def _ws_send(
    client: Any, msg_id: int, payload: dict[str, Any]
) -> dict[str, Any]:
    await client.send_json({"id": msg_id, **payload})
    return await client.receive_json()


async def test_websocket_crud(
    hass: HomeAssistant,
    setup_almanac: MockConfigEntry,
    hass_ws_client: WebSocketGenerator,
) -> None:
    """The UI's only path to a schedule, which is why D33 has no YAML twin."""
    client = await hass_ws_client(hass)

    result = await _ws_send(
        client, 1, {"type": "almanac/schedule/create", "name": "Shabbat lights"}
    )
    assert result["success"], result
    item_id = result["result"]["id"]
    assert result["result"]["object_id"] == "shabbat_lights"

    result = await _ws_send(client, 2, {"type": "almanac/schedule/list"})
    assert [item["id"] for item in result["result"]] == [item_id]

    result = await _ws_send(
        client,
        3,
        {
            "type": "almanac/schedule/update",
            "schedule_id": item_id,
            "name": "Sanctuary lights",
        },
    )
    assert result["success"], result
    assert result["result"]["name"] == "Sanctuary lights"

    result = await _ws_send(
        client, 4, {"type": "almanac/schedule/delete", "schedule_id": item_id}
    )
    assert result["success"], result

    result = await _ws_send(client, 5, {"type": "almanac/schedule/list"})
    assert result["result"] == []


async def test_websocket_update_cannot_carry_an_object_id(
    hass: HomeAssistant,
    setup_almanac: MockConfigEntry,
    hass_ws_client: WebSocketGenerator,
) -> None:
    """D66 reaches the wire, because the command schema is built from UPDATE_FIELDS.

    The rejection happens in the websocket layer, before the collection is
    touched — so a frontend that tries to rename the entity_id along with the
    schedule gets an error rather than a surprise.
    """
    client = await hass_ws_client(hass)
    created = await _ws_send(
        client, 1, {"type": "almanac/schedule/create", "name": "Shabbat lights"}
    )
    item_id = created["result"]["id"]

    result = await _ws_send(
        client,
        2,
        {
            "type": "almanac/schedule/update",
            "schedule_id": item_id,
            "object_id": "something_else",
        },
    )
    assert not result["success"]
    assert result["error"]["code"] == "invalid_format"


# --- D35: two stores, and they do not touch each other ---------------------


async def test_runtime_state_is_not_in_the_edited_object(
    hass: HomeAssistant, almanac_data: AlmanacData
) -> None:
    """D35 — a counter must not be resettable by saving an unrelated edit.

    The engine writes here; the editor writes to the collection. If they shared
    a store, every edit would race the engine and neither writer could see it
    happening.
    """
    collection = almanac_data.schedules
    runtime = almanac_data.runtime

    item = await collection.async_create_item({"name": "Shabbat lights"})
    runtime.async_set(item["id"], {"occurrences": 2, "last_result": "fired"})

    updated = await collection.async_update_item(item["id"], {"name": "Renamed"})

    assert "occurrences" not in updated
    assert "last_result" not in updated
    assert runtime.async_get(item["id"]) == {
        "occurrences": 2,
        "last_result": "fired",
    }


async def test_runtime_state_cannot_be_written_through_the_collection(
    almanac_data: AlmanacData,
) -> None:
    """The separation is structural, not a convention about what to pass."""
    collection = almanac_data.schedules
    item = await collection.async_create_item({"name": "Shabbat lights"})

    with pytest.raises(vol.Invalid):
        await collection.async_update_item(item["id"], {"last_fired": "2026-10-02"})
