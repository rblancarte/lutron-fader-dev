"""End-to-end tests in a real Home Assistant.

Fake Lutron lights stand in for lutron_caseta: their turn_on/turn_off jump
straight to the target (as a real Lutron hub reports it) and carry the
caller's context. The fade sensors are checked as HA would see them.
"""
from datetime import timedelta

import pytest
from pytest_homeassistant_custom_component.common import (
    MockConfigEntry,
    async_fire_time_changed,
    mock_restore_cache_with_extra_data,
)

from homeassistant.core import HomeAssistant, ServiceCall, State
from homeassistant.helpers import area_registry as ar, device_registry as dr, entity_registry as er
from homeassistant.helpers.service import async_extract_entity_ids
from homeassistant.util import dt as dt_util

from custom_components.lutron_fader.const import DOMAIN

LAMP = "light.living_room_floor_lamp"
LAMP_FADE = "sensor.living_room_floor_lamp_fade"
TABLE = "light.living_room_table_lamps"
TABLE_FADE = "sensor.living_room_table_lamps_fade"

DIMMABLE = {"supported_features": 32, "capabilities": {"supported_color_modes": ["brightness"]}}


def register_light(hass, object_id, platform="lutron_caseta", supported_features=32,
                   color_modes=("brightness",), brightness=None):
    """Add a light to the entity registry and give it a state."""
    er.async_get(hass).async_get_or_create(
        "light", platform, f"uid_{object_id}",
        suggested_object_id=object_id,
        supported_features=supported_features,
        capabilities={"supported_color_modes": list(color_modes)},
    )
    set_light(hass, f"light.{object_id}", brightness)


def set_light(hass, entity_id, brightness, context=None):
    """Set a light's state the way a Lutron light reports it (None = off)."""
    name = entity_id.split(".", 1)[1].replace("_", " ").title()
    attrs = {"friendly_name": name, "supported_features": 32, "supported_color_modes": ["brightness"]}
    if brightness:
        hass.states.async_set(entity_id, "on", {**attrs, "brightness": brightness}, context=context)
    else:
        hass.states.async_set(entity_id, "off", attrs, context=context)


def install_fake_light_services(hass):
    """light.turn_on/turn_off that jump to the target, like a Lutron light."""
    async def handle(call: ServiceCall):
        for entity_id in await async_extract_entity_ids(call):
            if call.service == "turn_off":
                set_light(hass, entity_id, None, context=call.context)
            elif "brightness_pct" in call.data:
                set_light(hass, entity_id, round(call.data["brightness_pct"] * 255 / 100), context=call.context)
            else:
                set_light(hass, entity_id, call.data.get("brightness", 255), context=call.context)

    hass.services.async_register("light", "turn_on", handle)
    hass.services.async_register("light", "turn_off", handle)


async def setup_tracker(hass, version=2, data=None):
    entry = MockConfigEntry(domain=DOMAIN, version=version, data=data or {})
    entry.add_to_hass(hass)
    assert await hass.config_entries.async_setup(entry.entry_id)
    await hass.async_block_till_done()
    return entry


@pytest.fixture
async def lamps(hass: HomeAssistant):
    """Two dimmable Lutron lights, both off, with fake light services."""
    install_fake_light_services(hass)
    register_light(hass, "living_room_floor_lamp")
    register_light(hass, "living_room_table_lamps")


async def advance(hass, freezer, seconds):
    freezer.tick(timedelta(seconds=seconds))
    async_fire_time_changed(hass)
    await hass.async_block_till_done()


async def fade(hass, target, transition, **target_kw):
    data = {"transition": transition, **target_kw}
    service = "turn_off" if target is None else "turn_on"
    if target is not None:
        data["brightness"] = target
    await hass.services.async_call("light", service, data, blocking=True)
    await hass.async_block_till_done()


# ---------------------------------------------------------------------------
# Setup and discovery
# ---------------------------------------------------------------------------

async def test_one_sensor_per_fadeable_lutron_light(hass, lamps):
    install_fake_light_services(hass)
    register_light(hass, "porch_switch", color_modes=("onoff",))            # can't dim
    register_light(hass, "no_transition", supported_features=0)            # can't fade
    register_light(hass, "hue_lamp", platform="hue")                       # not Lutron
    register_light(hass, "ra2_lamp", platform="lutron")                    # RadioRA 2
    register_light(hass, "pro_lamp", platform="lutron_caseta_pro")         # Telnet custom

    await setup_tracker(hass)

    fade_sensors = {s.entity_id for s in hass.states.async_all("sensor")}
    assert fade_sensors == {
        LAMP_FADE, TABLE_FADE, "sensor.ra2_lamp_fade", "sensor.pro_lamp_fade",
    }
    state = hass.states.get(LAMP_FADE)
    assert state.state == "idle"
    assert state.attributes["light_entity_id"] == LAMP


async def test_sensor_id_follows_light_id_when_on_a_device_in_an_area(hass):
    """Found in real HA: the area was doubled into the entity ID."""
    install_fake_light_services(hass)
    caseta = MockConfigEntry(domain="lutron_caseta")
    caseta.add_to_hass(hass)
    area = ar.async_get(hass).async_create("Master Bedroom")
    device = dr.async_get(hass).async_get_or_create(
        config_entry_id=caseta.entry_id, identifiers={("lutron_caseta", "ron")},
        name="Master Bedroom Ron Lamp",
    )
    dr.async_get(hass).async_update_device(device.id, area_id=area.id)
    er.async_get(hass).async_get_or_create(
        "light", "lutron_caseta", "uid_ron", suggested_object_id="master_bedroom_ron_lamp",
        device_id=device.id, config_entry=caseta, supported_features=32,
        capabilities={"supported_color_modes": ["brightness"]},
    )
    set_light(hass, "light.master_bedroom_ron_lamp", None)

    await setup_tracker(hass)

    assert hass.states.get("sensor.master_bedroom_ron_lamp_fade") is not None
    sensor = er.async_get(hass).async_get("sensor.master_bedroom_ron_lamp_fade")
    assert sensor.device_id == device.id  # sits on the light's device


async def test_only_one_entry_allowed(hass, lamps):
    await setup_tracker(hass)
    result = await hass.config_entries.flow.async_init(DOMAIN, context={"source": "user"})
    assert result["type"] == "abort"
    assert result["reason"] == "single_instance_allowed"


async def test_config_flow_creates_entry(hass, lamps):
    result = await hass.config_entries.flow.async_init(DOMAIN, context={"source": "user"})
    assert result["type"] == "form"
    result = await hass.config_entries.flow.async_configure(result["flow_id"], {})
    await hass.async_block_till_done()
    assert result["type"] == "create_entry"
    assert hass.states.get(LAMP_FADE).state == "idle"


# ---------------------------------------------------------------------------
# Tracking fades
# ---------------------------------------------------------------------------

async def test_fade_is_tracked_from_start_to_end(hass, lamps, freezer):
    await setup_tracker(hass)

    await fade(hass, 255, 100, entity_id=LAMP)

    state = hass.states.get(LAMP_FADE)
    assert state.state == "fading"
    assert state.attributes["start_pct"] == 0
    assert state.attributes["target_pct"] == 100
    assert state.attributes["progress"] == 0

    await advance(hass, freezer, 50)
    state = hass.states.get(LAMP_FADE)
    assert state.attributes["progress"] == 50
    assert state.attributes["brightness_pct"] == 50

    await advance(hass, freezer, 51)
    state = hass.states.get(LAMP_FADE)
    assert state.state == "idle"
    assert "progress" not in state.attributes


async def test_fade_off_is_tracked(hass, lamps, freezer):
    set_light(hass, LAMP, 200)
    await setup_tracker(hass)

    await fade(hass, None, 60, entity_id=LAMP)

    state = hass.states.get(LAMP_FADE)
    assert state.state == "fading"
    assert state.attributes["target_pct"] == 0


async def test_brightness_pct_is_tracked(hass, lamps):
    await setup_tracker(hass)
    await hass.services.async_call(
        "light", "turn_on", {"entity_id": LAMP, "brightness_pct": 40, "transition": 30}, blocking=True
    )
    await hass.async_block_till_done()
    assert hass.states.get(LAMP_FADE).attributes["target_pct"] == 40


async def test_area_target_is_tracked(hass, lamps):
    area = ar.async_get(hass).async_create("Living Room")
    registry = er.async_get(hass)
    for light in (LAMP, TABLE):
        registry.async_update_entity(light, area_id=area.id)
    await setup_tracker(hass)

    await fade(hass, 128, 30, area_id=area.id)

    assert hass.states.get(LAMP_FADE).state == "fading"
    assert hass.states.get(TABLE_FADE).state == "fading"


async def test_only_targeted_light_is_tracked(hass, lamps):
    await setup_tracker(hass)
    await fade(hass, 255, 30, entity_id=LAMP)
    assert hass.states.get(LAMP_FADE).state == "fading"
    assert hass.states.get(TABLE_FADE).state == "idle"


async def test_command_without_transition_is_ignored(hass, lamps):
    await setup_tracker(hass)
    await hass.services.async_call("light", "turn_on", {"entity_id": LAMP, "brightness": 255}, blocking=True)
    await hass.async_block_till_done()
    assert hass.states.get(LAMP_FADE).state == "idle"


async def test_already_at_target_never_shows_fading(hass, lamps, freezer):
    set_light(hass, LAMP, 255)
    await setup_tracker(hass)

    await fade(hass, 255, 30, entity_id=LAMP)
    assert hass.states.get(LAMP_FADE).state == "idle"

    await advance(hass, freezer, 6)  # pending times out quietly
    assert hass.states.get(LAMP_FADE).state == "idle"


# ---------------------------------------------------------------------------
# Overrides
# ---------------------------------------------------------------------------

async def test_pico_override_ends_the_fade(hass, lamps, freezer):
    await setup_tracker(hass)
    await fade(hass, 255, 100, entity_id=LAMP)
    await advance(hass, freezer, 10)

    set_light(hass, LAMP, 76)  # Pico sets 30%, outside HA
    await hass.async_block_till_done()

    assert hass.states.get(LAMP_FADE).state == "idle"


async def test_instant_command_mid_fade_ends_it(hass, lamps, freezer):
    await setup_tracker(hass)
    await fade(hass, 255, 100, entity_id=LAMP)
    await advance(hass, freezer, 10)

    await hass.services.async_call("light", "turn_on", {"entity_id": LAMP, "brightness": 50}, blocking=True)
    await hass.async_block_till_done()

    assert hass.states.get(LAMP_FADE).state == "idle"


async def test_new_fade_mid_fade_starts_from_calculated_level(hass, lamps, freezer):
    await setup_tracker(hass)
    await fade(hass, 255, 100, entity_id=LAMP)
    await advance(hass, freezer, 50)  # really at ~50%, though HA shows 100%

    await fade(hass, None, 20, entity_id=LAMP)

    state = hass.states.get(LAMP_FADE)
    assert state.state == "fading"
    assert state.attributes["start_pct"] == 50
    assert state.attributes["target_pct"] == 0


async def test_light_unavailable_ends_the_fade(hass, lamps):
    await setup_tracker(hass)
    await fade(hass, 255, 100, entity_id=LAMP)

    hass.states.async_set(LAMP, "unavailable")
    await hass.async_block_till_done()

    assert hass.states.get(LAMP_FADE).state == "idle"


# ---------------------------------------------------------------------------
# Restarts, migration and registry changes
# ---------------------------------------------------------------------------

async def test_fade_resumes_after_restart(hass, lamps, freezer):
    now = dt_util.utcnow()
    set_light(hass, LAMP, 255)  # the light reports the fade's target
    mock_restore_cache_with_extra_data(hass, [(
        State(LAMP_FADE, "fading"),
        {"start": 0.0, "target": 255.0,
         "started_at": (now - timedelta(seconds=40)).isoformat(),
         "ends_at": (now + timedelta(seconds=60)).isoformat()},
    )])

    await setup_tracker(hass)

    state = hass.states.get(LAMP_FADE)
    assert state.state == "fading"
    assert state.attributes["progress"] == 40


async def test_stale_fade_is_not_resumed(hass, lamps):
    now = dt_util.utcnow()
    set_light(hass, LAMP, 76)  # someone changed it while HA was down
    mock_restore_cache_with_extra_data(hass, [(
        State(LAMP_FADE, "fading"),
        {"start": 0.0, "target": 255.0,
         "started_at": (now - timedelta(seconds=40)).isoformat(),
         "ends_at": (now + timedelta(seconds=60)).isoformat()},
    )])

    await setup_tracker(hass)

    assert hass.states.get(LAMP_FADE).state == "idle"


async def test_migration_from_telnet_version_removes_old_lights(hass, lamps):
    entry = MockConfigEntry(domain=DOMAIN, version=1, data={"host": "10.0.1.14", "zones": {"28": "Lamp"}})
    entry.add_to_hass(hass)
    registry = er.async_get(hass)
    old = registry.async_get_or_create(
        "light", DOMAIN, "lutron_fader_zone_28", suggested_object_id="lutron_zone_28", config_entry=entry,
    )

    assert await hass.config_entries.async_setup(entry.entry_id)
    await hass.async_block_till_done()

    assert entry.version == 2
    assert entry.data == {}
    assert registry.async_get(old.entity_id) is None
    assert hass.states.get(LAMP_FADE).state == "idle"


async def test_new_lutron_light_gets_a_sensor(hass, lamps):
    await setup_tracker(hass)

    register_light(hass, "kitchen_pendants")
    await hass.async_block_till_done()

    assert hass.states.get("sensor.kitchen_pendants_fade").state == "idle"


async def test_sensor_attributes_are_not_recorded(hass, lamps):
    await setup_tracker(hass)
    entity = hass.data["entity_components"]["sensor"].get_entity(LAMP_FADE)
    assert {"progress", "ends_at", "brightness"} <= entity._unrecorded_attributes
