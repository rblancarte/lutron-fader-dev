#!/usr/bin/env python3
"""Live check of the light entity state machine against a real Lutron hub.

Drives real LutronFaderLight objects on a real connection, standing in for the
few Home Assistant pieces the light uses (entity base, 1s timer, state writes).
A second Telnet session plays the part of a Pico remote.

Changes the test zone's level several times, then restores it. Takes about a
minute. Needs Python 3.10+ (like Home Assistant).

Usage:
    python3.12 light_check.py
    python3.12 light_check.py --host 10.0.1.14 --zone 28
"""
import argparse
import asyncio
import enum
import importlib
import logging
import os
import sys
import time
import types
from datetime import datetime
from unittest.mock import MagicMock

if sys.version_info < (3, 10):
    sys.exit("light_check.py needs Python 3.10+; try: python3.12 light_check.py "
             "(or /opt/homebrew/bin/python3 light_check.py)")

HOST = "10.0.1.14"   # default; override with --host
ZONE = 28            # default (Living Room Floor Lamp); override with --zone
MISSING_ZONE = 250   # an ID with no zone behind it

T0 = time.monotonic()
results: list[tuple[str, bool, str]] = []


# ---------------------------------------------------------------------------
# Stand-ins for the Home Assistant pieces light.py imports
# ---------------------------------------------------------------------------

def install_ha_stubs() -> None:
    for name in (
        "voluptuous",
        "homeassistant", "homeassistant.components", "homeassistant.components.light",
        "homeassistant.config_entries", "homeassistant.const", "homeassistant.core",
        "homeassistant.exceptions", "homeassistant.helpers",
        "homeassistant.helpers.config_validation", "homeassistant.helpers.entity_platform",
        "homeassistant.helpers.event", "homeassistant.helpers.typing",
    ):
        sys.modules[name] = MagicMock()

    class ColorMode(str, enum.Enum):
        BRIGHTNESS = "brightness"

    light_mod = sys.modules["homeassistant.components.light"]
    light_mod.ATTR_BRIGHTNESS = "brightness"
    light_mod.ATTR_TRANSITION = "transition"
    light_mod.ColorMode = ColorMode
    light_mod.LightEntityFeature = MagicMock(TRANSITION=32)
    light_mod.LightEntity = object

    sys.modules["homeassistant.core"].callback = lambda fn: fn
    sys.modules["homeassistant.const"].CONF_NAME = "name"
    sys.modules["homeassistant.exceptions"].HomeAssistantError = type(
        "HomeAssistantError", (Exception,), {})

    def async_track_time_interval(hass, action, interval):
        """A real repeating timer on the running loop."""
        async def run():
            while True:
                await asyncio.sleep(interval.total_seconds())
                action(datetime.now())
        return asyncio.get_running_loop().create_task(run()).cancel

    sys.modules["homeassistant.helpers.event"].async_track_time_interval = async_track_time_interval

    # Load light.py as part of its package without running the package
    # __init__.py (which pulls in the rest of Home Assistant)
    root = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "lutron-fader", "custom_components")
    for name, path in (("custom_components", root),
                       ("custom_components.lutron_fader", os.path.join(root, "lutron_fader"))):
        pkg = types.ModuleType(name)
        pkg.__path__ = [path]
        sys.modules[name] = pkg


install_ha_stubs()
light_mod = importlib.import_module("custom_components.lutron_fader.light")
telnet = importlib.import_module("custom_components.lutron_fader.lutron_telnet")
LutronFaderLight = light_mod.LutronFaderLight
Unavailable, Steady, Sending, Fading = (
    light_mod.Unavailable, light_mod.Steady, light_mod.Sending, light_mod.Fading)
LutronTelnetConnection, HubError = telnet.LutronTelnetConnection, telnet.HubError


# ---------------------------------------------------------------------------
# Output helpers
# ---------------------------------------------------------------------------

def setup_logging():
    """Show light state changes, connection transitions, and warnings."""
    class _Filter(logging.Filter):
        def filter(self, record):
            msg = record.getMessage()
            return (record.levelno >= logging.WARNING
                    or msg.startswith("Connection ")
                    or (record.name.endswith(".light") and msg.startswith("Zone ")))

    handler = logging.StreamHandler()
    handler.setFormatter(logging.Formatter("        %(message)s"))
    handler.addFilter(_Filter())
    for name in ("custom_components.lutron_fader.light", "custom_components.lutron_fader.lutron_telnet"):
        log = logging.getLogger(name)
        log.setLevel(logging.DEBUG)
        log.addHandler(handler)
        log.propagate = False


def say(msg):
    print(f"[{time.monotonic() - T0:6.1f}s] {msg}")


def record(name, ok, detail=""):
    results.append((name, ok, detail))
    say(f"{'PASS' if ok else 'FAIL'}  {name}{' - ' + detail if detail else ''}")


def make_light(conn, zone):
    """A LutronFaderLight wired to a fake HA: records every state write."""
    light = LutronFaderLight(hass=MagicMock(), connection=conn, name=f"Zone {zone}",
                             zone_id=zone, unique_id=f"lutron_fader_zone_{zone}")
    light.writes = []  # (state type, brightness) for every state write

    def write():
        light.writes.append((type(light._state).__name__, light.brightness))

    def schedule_update(force_refresh=False):
        async def run():
            if force_refresh:
                await light.async_update()
            write()
        asyncio.get_running_loop().create_task(run())

    light.async_write_ha_state = write
    light.async_schedule_update_ha_state = schedule_update
    return light


async def wait_until(predicate, timeout):
    """Return seconds until predicate() is true, or None on timeout."""
    start = time.monotonic()
    while time.monotonic() - start < timeout:
        if predicate():
            return time.monotonic() - start
        await asyncio.sleep(0.1)
    return None


def near(a, b, tol=1.0):
    return a is not None and b is not None and abs(a - b) <= tol


def level_of(light):
    return light._state.level if isinstance(light._state, Steady) else None


# ---------------------------------------------------------------------------
# Checks
# ---------------------------------------------------------------------------

async def check_startup(conn, light):
    say("1. Startup: UNAVAILABLE until connected and the level is read")
    record("starts unavailable", isinstance(light._state, Unavailable), repr(light._state))
    await conn.connect()
    took = await wait_until(lambda: isinstance(light._state, Steady), 5)
    hub = await conn.query_light_level(ZONE)
    record("steady at the hub's level", took is not None and near(level_of(light), hub),
           f"light={light._state!r}, hub={hub}%")
    return hub


async def check_instant(conn, light):
    say("2. Instant set 50%: SENDING -> STEADY(50)")
    light.writes.clear()
    await light.async_turn_on(brightness=128)
    hub = await conn.query_light_level(ZONE)
    kinds = [k for k, _ in light.writes]
    record("went SENDING then STEADY", kinds[:2] == ["Sending", "Steady"], " -> ".join(kinds))
    record("steady at 50%, hub agrees", near(level_of(light), 50) and near(hub, 50),
           f"light={light._state!r}, hub={hub}%")


async def check_fade(conn, light, echoes):
    say("3. Fade to 100% over 10s: SENDING -> FADING -> STEADY(100)")
    light.writes.clear()
    echoes.clear()
    await light.async_turn_on(brightness=255, transition=10)
    record("fading after the ack", isinstance(light._state, Fading), repr(light._state))

    await asyncio.sleep(11.5)
    fading = [b for k, b in light.writes if k == "Fading"]
    rising = all(b2 >= b1 for b1, b2 in zip(fading, fading[1:]))
    record("brightness climbed each tick", len(fading) >= 8 and rising,
           f"{len(fading)} ticks: {fading[:3]} ... {fading[-2:]}")

    hub = await conn.query_light_level(ZONE)
    record("steady at 100% at the end, hub agrees", near(level_of(light), 100) and near(hub, 100),
           f"light={light._state!r}, hub={hub}%")
    record("timer stopped", light._tick_unsub is None)
    say(f"   info: the hub pushed {len(echoes)} external ~OUTPUT line(s) for zone {ZONE} "
        f"during the fade: {echoes} (echoes of our target are ignored)")


async def check_override(conn, other, light):
    say("4. Override mid-fade: a second session (the 'Pico') sets 30%")
    await light.async_turn_off(transition=20)
    await asyncio.sleep(4)
    record("fading before the override", isinstance(light._state, Fading), repr(light._state))

    await other.set_light_level(ZONE, 30, 0)
    took = await wait_until(lambda: isinstance(light._state, Steady), 3)
    record("dropped straight to STEADY(30)", took is not None and near(level_of(light), 30),
           f"after {took:.1f}s, {light._state!r}" if took is not None else f"still {light._state!r}")
    record("timer stopped", light._tick_unsub is None)


async def check_link_drop(conn, light):
    say("5. Link drop mid-fade: UNAVAILABLE, then STEADY at the hub's level once back")
    await light.async_turn_on(brightness=204, transition=20)  # 80%
    await asyncio.sleep(3)
    record("fading before the drop", isinstance(light._state, Fading), repr(light._state))

    conn._writer.transport.abort()
    took = await wait_until(lambda: isinstance(light._state, Unavailable), 3)
    record("went unavailable", took is not None, f"after {took:.1f}s" if took is not None else repr(light._state))
    record("timer stopped", light._tick_unsub is None)

    back = await wait_until(lambda: isinstance(light._state, Steady), 20)
    hub = await conn.query_light_level(ZONE) if conn.is_connected else None
    record("steady again at the hub's level", back is not None and near(level_of(light), hub, tol=5),
           f"after {back:.1f}s, light={light._state!r}, hub={hub}%" if back is not None else repr(light._state))
    say("   info: the hub may still be fading; the light shows the level it read and "
        "picks up the next push")
    await asyncio.sleep(18)  # let the hub's fade finish before the next check


async def check_rejection(conn, light):
    say(f"6. Hub rejection: a second light on zone {MISSING_ZONE} gets HubError and stays put")
    phantom = make_light(conn, MISSING_ZONE)
    await phantom.async_added_to_hass()
    # Zone 250 has no level to read, so mark it known by hand to get past UNAVAILABLE
    phantom._state = Steady(0.0)
    before = light._state

    try:
        await phantom.async_turn_on(brightness=255)
        record("HubError raised", False, "command succeeded?")
    except HubError as e:
        record("HubError raised", e.code == 2, f"code={e.code}")
    except Exception as e:
        record("HubError raised", False, f"{type(e).__name__}: {e}")
    record("phantom back at STEADY(0)", phantom._state == Steady(0.0), repr(phantom._state))
    record("zone light unaffected", light._state is before, repr(light._state))
    await phantom.async_will_remove_from_hass()


async def main():
    global HOST, ZONE
    parser = argparse.ArgumentParser(description="Live check of the Lutron light state machine.")
    parser.add_argument("--host", default=HOST, help=f"hub address (default {HOST})")
    parser.add_argument("--zone", type=int, default=ZONE, help=f"zone safe to change (default {ZONE})")
    args = parser.parse_args()
    HOST, ZONE = args.host, args.zone
    setup_logging()

    conn = LutronTelnetConnection(HOST, ping_zone=ZONE)
    other = LutronTelnetConnection(HOST)  # plays the Pico
    light = make_light(conn, ZONE)
    await light.async_added_to_hass()

    echoes = []
    conn.add_push_callback(
        lambda line, source: echoes.append(line)
        if source == "external" and line.startswith(f"~OUTPUT,{ZONE},") else None)

    original = None
    try:
        original = await check_startup(conn, light)
        if not conn.is_connected:
            return
        if not await other.connect():
            record("second session connects", False)
            return
        await check_instant(conn, light)
        await check_fade(conn, light, echoes)
        await check_override(conn, other, light)
        await check_link_drop(conn, light)
        await check_rejection(conn, light)
    finally:
        if original is not None and conn.is_connected:
            say(f"Restoring zone {ZONE} to {original}%")
            await conn.set_light_level(ZONE, round(original), 2)
        await light.async_will_remove_from_hass()
        await other.disconnect()
        await conn.disconnect()

    print()
    passed = sum(ok for _, ok, _ in results)
    print(f"{passed}/{len(results)} checks passed")
    for name, ok, detail in results:
        if not ok:
            print(f"  FAIL  {name}: {detail}")


if __name__ == "__main__":
    asyncio.run(main())
