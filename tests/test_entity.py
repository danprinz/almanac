"""The entity model: D54's switch, D55's sensor, and D66's never-re-derived slug.

The rename test is the one that matters. Everything else here would still pass
if the slug were recomputed on every edit, and that is precisely the change that
would break every automation referencing a schedule — later, silently, and long
after anyone would connect the two.
"""

from __future__ import annotations

from homeassistant.core import HomeAssistant
from homeassistant.helpers import entity_registry as er

from pytest_homeassistant_custom_component.common import MockConfigEntry

from custom_components.almanac.const import DOMAIN
from custom_components.almanac.storage import AlmanacData


async def test_one_switch_and_one_sensor_per_schedule(
    hass: HomeAssistant, almanac_data: AlmanacData
) -> None:
    """D54 and D55 — two entities, at slugged ids a person can guess."""
    item = await almanac_data.schedules.async_create_item({"name": "Shabbat lights"})
    await hass.async_block_till_done()

    switch = hass.states.get("switch.shabbat_lights")
    sensor = hass.states.get("sensor.shabbat_lights_next_trigger")
    assert switch is not None
    assert sensor is not None

    assert switch.attributes["friendly_name"] == "Shabbat lights"
    # D55 — the instant is typed, so templates and automations do not parse it
    # out of an attribute.
    assert sensor.attributes["device_class"] == "timestamp"
    # Unknown until the engine exists (§15 step 3). D13's known / estimated /
    # unknown split is what makes not knowing a state rather than a gap.
    assert sensor.state == "unknown"

    registry = er.async_get(hass)
    for entity_id in ("switch.shabbat_lights", "sensor.shabbat_lights_next_trigger"):
        entry = registry.async_get(entity_id)
        assert entry is not None
        # D37 — the unique_id exists from the first commit, because one added
        # later cannot rename entities that already exist.
        assert entry.unique_id == item["id"]
        assert entry.platform == DOMAIN


async def test_entity_id_is_the_supplied_object_id_not_the_name(
    hass: HomeAssistant, almanac_data: AlmanacData
) -> None:
    """D66's example: a good display name and a good entity_id are different strings."""
    await almanac_data.schedules.async_create_item(
        {"name": "Shabbat — sanctuary lights & PA", "object_id": "shabbat_sanctuary"}
    )
    await hass.async_block_till_done()

    state = hass.states.get("switch.shabbat_sanctuary")
    assert state is not None
    assert state.attributes["friendly_name"] == "Shabbat — sanctuary lights & PA"
    assert hass.states.get("switch.shabbat_sanctuary_lights_pa") is None


async def test_renaming_never_moves_the_entity_id(
    hass: HomeAssistant, almanac_data: AlmanacData
) -> None:
    """D66, the half that matters.

    Renaming for clarity is something people do freely and expect to be safe.
    Re-slugging would break every automation, script, dashboard card and
    template referencing the old entity_id — the discoverability failure D54
    exists to fix, inverted and made worse because it arrives later.
    """
    item = await almanac_data.schedules.async_create_item({"name": "Shabbat lights"})
    await hass.async_block_till_done()
    assert hass.states.get("switch.shabbat_lights") is not None

    await almanac_data.schedules.async_update_item(
        item["id"], {"name": "Sanctuary evening"}
    )
    await hass.async_block_till_done()

    state = hass.states.get("switch.shabbat_lights")
    assert state is not None
    assert state.attributes["friendly_name"] == "Sanctuary evening"
    assert hass.states.get("switch.sanctuary_evening") is None

    sensor = hass.states.get("sensor.shabbat_lights_next_trigger")
    assert sensor is not None
    assert sensor.attributes["friendly_name"] == "Sanctuary evening next trigger"
    assert hass.states.get("sensor.sanctuary_evening_next_trigger") is None


async def test_the_switch_is_the_stored_enabled_flag(
    hass: HomeAssistant, almanac_data: AlmanacData
) -> None:
    """§2 puts `enabled` on the Schedule, so the switch and the code view agree."""
    item = await almanac_data.schedules.async_create_item({"name": "Shabbat lights"})
    await hass.async_block_till_done()
    assert hass.states.get("switch.shabbat_lights").state == "on"

    await hass.services.async_call(
        "switch",
        "turn_off",
        {"entity_id": "switch.shabbat_lights"},
        blocking=True,
    )

    assert hass.states.get("switch.shabbat_lights").state == "off"
    assert almanac_data.schedules.data[item["id"]]["enabled"] is False

    await hass.services.async_call(
        "switch", "turn_on", {"entity_id": "switch.shabbat_lights"}, blocking=True
    )
    assert almanac_data.schedules.data[item["id"]]["enabled"] is True


async def test_disarming_does_not_discard_the_rules(
    hass: HomeAssistant, almanac_data: AlmanacData
) -> None:
    """The switch's write is a one-field patch, and patches stay patches."""
    item = await almanac_data.schedules.async_create_item(
        {
            "name": "Shabbat lights",
            "rules": [{"kind": "at", "anchor": {"kind": "clock", "at": "17:00"}}],
        }
    )
    await hass.async_block_till_done()

    await hass.services.async_call(
        "switch", "turn_off", {"entity_id": "switch.shabbat_lights"}, blocking=True
    )

    assert len(almanac_data.schedules.data[item["id"]]["rules"]) == 1


async def test_deleting_a_schedule_removes_both_entities(
    hass: HomeAssistant, almanac_data: AlmanacData
) -> None:
    """Including the registry rows.

    A leftover registry row reserves the entity_id forever, so deleting and
    recreating a schedule under the same name would fail D66's collision check
    for no reason the user could see.
    """
    item = await almanac_data.schedules.async_create_item({"name": "Shabbat lights"})
    await hass.async_block_till_done()

    await almanac_data.schedules.async_delete_item(item["id"])
    await hass.async_block_till_done()

    registry = er.async_get(hass)
    assert hass.states.get("switch.shabbat_lights") is None
    assert hass.states.get("sensor.shabbat_lights_next_trigger") is None
    assert registry.async_get("switch.shabbat_lights") is None
    assert registry.async_get("sensor.shabbat_lights_next_trigger") is None

    # And the slug is free again, which is what the registry cleanup buys.
    recreated = await almanac_data.schedules.async_create_item(
        {"name": "Shabbat lights"}
    )
    assert recreated["object_id"] == "shabbat_lights"


async def test_unloading_the_entry_releases_the_entities(
    hass: HomeAssistant, setup_almanac: MockConfigEntry
) -> None:
    """A clean unload is what makes a reload a reload rather than a restart.

    Note what unloading is *not*: HA leaves the registry row in place and marks
    the state restored-unavailable, which is right — the schedule still exists,
    the integration is merely not running. Deletion is the only thing that
    releases the entity_id, which is why the two are tested apart.
    """
    await setup_almanac.runtime_data.schedules.async_create_item(
        {"name": "Shabbat lights"}
    )
    await hass.async_block_till_done()

    assert await hass.config_entries.async_unload(setup_almanac.entry_id)
    await hass.async_block_till_done()

    state = hass.states.get("switch.shabbat_lights")
    assert state is not None
    assert state.state == "unavailable"
    assert state.attributes["restored"] is True
