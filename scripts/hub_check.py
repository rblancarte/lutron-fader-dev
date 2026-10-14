#!/usr/bin/env python3
"""Live check of the connection state machine against a real Lutron hub.

Changes the test zone's level a few times, then restores it.

Usage:
    python3 hub_check.py                        # automatic checks (about 20s)
    python3 hub_check.py --wifi                 # also the Wi-Fi-off check (you toggle Wi-Fi)
    python3 hub_check.py --host 10.0.1.14 --zone 28
"""
import argparse
import asyncio
import logging
import os
import sys
import time

sys.path.insert(0, os.path.join(
    os.path.dirname(os.path.abspath(__file__)),
    "..", "lutron-fader", "custom_components", "lutron_fader",
))

import lutron_telnet  # noqa: E402
from lutron_telnet import ConnState, HubError, LutronTelnetConnection  # noqa: E402

HOST = "10.0.1.14"   # default; override with --host
ZONE = 28            # default (Living Room Floor Lamp); override with --zone
MISSING_ZONE = 250  # an ID with no zone behind it

T0 = time.monotonic()
results: list[tuple[str, bool, str]] = []


class _Filter(logging.Filter):
    """Show state transitions plus anything INFO and above."""

    def filter(self, record):
        return record.levelno >= logging.INFO or record.getMessage().startswith("Connection ")


def setup_logging():
    handler = logging.StreamHandler()
    handler.setFormatter(logging.Formatter("        %(levelname)-7s %(message)s"))
    handler.addFilter(_Filter())
    log = logging.getLogger("lutron_telnet")
    log.setLevel(logging.DEBUG)
    log.addHandler(handler)
    log.propagate = False


def say(msg):
    print(f"[{time.monotonic() - T0:6.1f}s] {msg}")


def record(name, ok, detail=""):
    results.append((name, ok, detail))
    say(f"{'PASS' if ok else 'FAIL'}  {name}{' - ' + detail if detail else ''}")


async def wait_for_state(conn, state, timeout):
    """Return seconds taken to reach `state`, or None on timeout."""
    start = time.monotonic()
    while time.monotonic() - start < timeout:
        if conn.state is state:
            return time.monotonic() - start
        await asyncio.sleep(0.1)
    return None


def drop_socket(conn):
    """Kill the TCP connection under the manager, as if the hub hung up."""
    conn._writer.transport.abort()


async def check_basic(conn):
    say("1. Connect, set, query")
    ok = await conn.connect()
    record("connect() returns True", ok, f"state={conn.state.name}")
    if not ok:
        return None

    original = await conn.query_light_level(ZONE)
    say(f"   zone {ZONE} starts at {original}%")

    await conn.set_light_level(ZONE, 50, 2)
    await asyncio.sleep(3)
    level = await conn.query_light_level(ZONE)
    record("set 50% then query", abs(level - 50) < 1, f"got {level}%")
    return original


async def check_hub_drop(conn):
    say("2. Socket dropped -> manager reconnects on its own")
    drop_socket(conn)
    lost = await wait_for_state(conn, ConnState.CONNECTING, 5)
    record("loss detected", lost is not None, f"after {lost:.1f}s" if lost is not None else "still CONNECTED")
    back = await wait_for_state(conn, ConnState.CONNECTED, 30)
    record("reconnected", back is not None, f"after {back:.1f}s" if back is not None else "timed out")


async def check_command_during_drop(conn):
    say("3. Command sent right as the socket drops -> resent after reconnect")
    drop_socket(conn)
    start = time.monotonic()
    try:
        await conn.set_light_level(ZONE, 80, 0)
        took = time.monotonic() - start
        level = await conn.query_light_level(ZONE)
        record("command survived the drop", abs(level - 80) < 1, f"took {took:.1f}s, lamp at {level}%")
    except Exception as e:
        record("command survived the drop", False, f"{type(e).__name__}: {e}")


async def check_wifi(conn):
    say("4. Silent dead link (Wi-Fi off) -> keep-alive notices, backoff recovers")
    await asyncio.to_thread(input, "   Turn Wi-Fi OFF now, then press Enter... ")
    say(f"   waiting for the keep-alive (every {lutron_telnet.PING_INTERVAL}s) to notice")
    lost = await wait_for_state(conn, ConnState.CONNECTING, lutron_telnet.PING_INTERVAL + 15)
    record("silent loss detected", lost is not None, f"after {lost:.1f}s" if lost is not None else "never noticed")
    await asyncio.to_thread(input, "   Turn Wi-Fi back ON, then press Enter... ")
    say("   waiting for the next backoff attempt (can take a few minutes)")
    back = await wait_for_state(conn, ConnState.CONNECTED, lutron_telnet.RECONNECT_MAX_DELAY + 30)
    record("recovered after Wi-Fi back", back is not None, f"after {back:.1f}s" if back is not None else "timed out")


async def check_hub_error(conn):
    say(f"5. Query zone {MISSING_ZONE} -> HubError, no retry, link stays up")
    start = time.monotonic()
    try:
        await conn.query_light_level(MISSING_ZONE)
        record("HubError raised", False, "query succeeded?")
    except HubError as e:
        took = time.monotonic() - start
        record("HubError raised", e.code == 2 and took < 2, f"code={e.code}, took {took:.2f}s")
    except Exception as e:
        record("HubError raised", False, f"{type(e).__name__}: {e}")
    record("still connected", conn.state is ConnState.CONNECTED, conn.state.name)


async def check_bad_password():
    say("6. Bad password -> connect() False, keeps retrying, disconnect() stops it")
    bad = LutronTelnetConnection(HOST, password="definitely-wrong")
    ok = await bad.connect()
    record("connect() returns False", ok is False, f"state={bad.state.name}")
    record("still retrying", bad.state is ConnState.CONNECTING)
    await bad.disconnect()
    record("disconnect() stops it", bad.state is ConnState.DISCONNECTED)


def parse_args():
    parser = argparse.ArgumentParser(description="Live check of the Lutron connection state machine.")
    parser.add_argument("--host", default=HOST, help=f"hub address (default {HOST})")
    parser.add_argument("--zone", type=int, default=ZONE, help=f"zone safe to change (default {ZONE})")
    parser.add_argument("--wifi", action="store_true", help="also run the interactive Wi-Fi-off check")
    return parser.parse_args()


async def main():
    global HOST, ZONE
    args = parse_args()
    HOST, ZONE = args.host, args.zone
    setup_logging()
    wifi = args.wifi
    conn = LutronTelnetConnection(HOST)
    conn.add_availability_callback(lambda up: say(f"   availability -> {'UP' if up else 'DOWN'}"))

    original = None
    try:
        original = await check_basic(conn)
        if original is None:
            return
        await check_hub_drop(conn)
        await check_command_during_drop(conn)
        if wifi:
            await check_wifi(conn)
        await check_hub_error(conn)
    finally:
        if original is not None and conn.is_connected:
            say(f"Restoring zone {ZONE} to {original}%")
            await conn.set_light_level(ZONE, round(original), 2)
        await conn.disconnect()

    await check_bad_password()

    print()
    passed = sum(ok for _, ok, _ in results)
    print(f"{passed}/{len(results)} checks passed")
    for name, ok, detail in results:
        if not ok:
            print(f"  FAIL  {name}: {detail}")


if __name__ == "__main__":
    asyncio.run(main())
