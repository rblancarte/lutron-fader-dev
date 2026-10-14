"""Tests for LutronFaderLight entity."""
import asyncio
import time
import pytest
from unittest.mock import AsyncMock, MagicMock, patch

import sys
import os
sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..', 'lutron-fader'))

from custom_components.lutron_fader.light import (
    Fading,
    LutronFaderLight,
    Sending,
    Steady,
    Unavailable,
)
from custom_components.lutron_fader.lutron_telnet import (
    SOURCE_INTERNAL,
    SOURCE_EXTERNAL,
    HubError,
    NoReply,
    NotConnected,
)
from homeassistant.exceptions import HomeAssistantError


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def make_light(zone_id=25, name="Test Light", state=None):
    hass = MagicMock()
    connection = MagicMock()
    connection.available = True
    connection.set_light_level = AsyncMock(return_value=None)
    connection.query_light_level = AsyncMock(return_value=0.0)
    light = LutronFaderLight(
        hass=hass,
        connection=connection,
        name=name,
        zone_id=zone_id,
        unique_id=f"lutron_fader_{zone_id}",
    )
    light._state = Steady(0.0) if state is None else state
    light.async_write_ha_state = MagicMock()
    return light


def fading(start=0.0, target=100.0, elapsed=0.0, duration=10.0) -> Fading:
    return Fading(start=start, target=target, t0=time.monotonic() - elapsed, duration=duration)


@pytest.fixture(autouse=True)
def track():
    """Fake the 1s fade timer; each start returns a fresh unsubscribe mock."""
    with patch(
        "custom_components.lutron_fader.light.async_track_time_interval",
        side_effect=lambda *args: MagicMock(),
    ) as mock:
        yield mock


# ---------------------------------------------------------------------------
# Brightness conversion
# ---------------------------------------------------------------------------

class TestBrightnessConversion:
    @pytest.mark.asyncio
    async def test_turn_on_255_sends_100_pct(self):
        light = make_light()
        await light.async_turn_on(brightness=255)
        light._connection.set_light_level.assert_called_once_with(25, 100, 0)

    @pytest.mark.asyncio
    async def test_turn_on_converts_128_to_50pct(self):
        light = make_light()
        await light.async_turn_on(brightness=128)
        assert light._connection.set_light_level.call_args[0][1] == 50

    @pytest.mark.asyncio
    async def test_turn_on_low_brightness_clamps_to_1pct(self):
        light = make_light()
        await light.async_turn_on(brightness=1)
        assert light._connection.set_light_level.call_args[0][1] == 1

    def test_lutron_100_is_brightness_255(self):
        light = make_light(state=Steady(100.0))
        assert light.brightness == 255
        assert light.is_on is True

    def test_lutron_0_is_off(self):
        light = make_light(state=Steady(0.0))
        assert light.brightness == 0
        assert light.is_on is False


# ---------------------------------------------------------------------------
# async_update (reading the level)
# ---------------------------------------------------------------------------

class TestUpdate:
    @pytest.mark.asyncio
    async def test_read_makes_unavailable_light_steady(self):
        light = make_light(state=Unavailable())
        light._connection.query_light_level = AsyncMock(return_value=75.0)

        await light.async_update()

        assert light._state == Steady(75.0)
        assert light.available is True
        light.async_write_ha_state.assert_not_called()  # HA writes after update

    @pytest.mark.asyncio
    async def test_read_refreshes_steady_level(self):
        light = make_light(state=Steady(20.0))
        light._connection.query_light_level = AsyncMock(return_value=40.0)

        await light.async_update()

        assert light._state == Steady(40.0)

    @pytest.mark.asyncio
    async def test_read_failure_leaves_state_unchanged(self):
        light = make_light(state=Steady(50.0))
        light._connection.query_light_level = AsyncMock(side_effect=HomeAssistantError("no reply"))

        await light.async_update()

        assert light._state == Steady(50.0)

    @pytest.mark.asyncio
    async def test_read_does_not_clobber_a_fade(self):
        state = fading(elapsed=2)
        light = make_light(state=state)
        light._connection.query_light_level = AsyncMock(return_value=100.0)

        await light.async_update()

        assert light._state is state

    @pytest.mark.asyncio
    async def test_read_does_not_clobber_a_command_in_flight(self):
        state = Sending(target=60.0, fade=0.0, previous=Steady(0.0))
        light = make_light(state=state)
        light._connection.query_light_level = AsyncMock(return_value=0.0)

        await light.async_update()

        assert light._state is state


# ---------------------------------------------------------------------------
# Turn on / off
# ---------------------------------------------------------------------------

class TestTurnOnOff:
    @pytest.mark.asyncio
    async def test_turn_on_passes_transition(self):
        light = make_light()
        await light.async_turn_on(brightness=255, transition=1800)
        light._connection.set_light_level.assert_called_once_with(25, 100, 1800)

    @pytest.mark.asyncio
    async def test_turn_on_defaults_transition_to_zero(self):
        light = make_light()
        await light.async_turn_on(brightness=255)
        assert light._connection.set_light_level.call_args[0][2] == 0

    @pytest.mark.asyncio
    async def test_instant_turn_on_goes_steady_at_target(self):
        light = make_light()
        await light.async_turn_on(brightness=255)

        assert light._state == Steady(100.0)
        assert light.is_on is True
        light.async_write_ha_state.assert_called()

    @pytest.mark.asyncio
    async def test_instant_turn_off_goes_steady_at_zero(self):
        light = make_light(state=Steady(100.0))
        await light.async_turn_off()

        light._connection.set_light_level.assert_called_once_with(25, 0, 0)
        assert light._state == Steady(0.0)
        assert light.is_on is False

    @pytest.mark.asyncio
    async def test_turn_off_with_fade_sends_zero_and_fades(self):
        light = make_light(state=Steady(100.0))
        await light.async_turn_off(transition=900)

        light._connection.set_light_level.assert_called_once_with(25, 0, 900)
        assert isinstance(light._state, Fading)
        assert light._state.start == 100.0 and light._state.target == 0.0
        assert light.is_on is True  # still visibly on as the fade begins

    @pytest.mark.asyncio
    async def test_shows_previous_level_while_sending(self):
        light = make_light(state=Steady(30.0))
        seen = []

        async def set_level(zone_id, level, fade):
            seen.append((type(light._state), light.brightness))

        light._connection.set_light_level = AsyncMock(side_effect=set_level)
        await light.async_turn_on(brightness=255)

        assert seen == [(Sending, round(30 / 100 * 255))]

    @pytest.mark.asyncio
    async def test_turn_on_no_zone_id_does_nothing(self):
        light = make_light(zone_id=None)
        await light.async_turn_on(brightness=255)
        light._connection.set_light_level.assert_not_called()

    @pytest.mark.asyncio
    async def test_turn_off_no_zone_id_does_nothing(self):
        light = make_light(zone_id=None)
        await light.async_turn_off()
        light._connection.set_light_level.assert_not_called()

    @pytest.mark.asyncio
    async def test_turn_on_while_unavailable_raises_without_sending(self):
        light = make_light(state=Unavailable())

        with pytest.raises(NotConnected):
            await light.async_turn_on(brightness=255)

        light._connection.set_light_level.assert_not_called()
        assert light._state == Unavailable()


# ---------------------------------------------------------------------------
# Fading
# ---------------------------------------------------------------------------

class TestFading:
    @pytest.mark.asyncio
    async def test_fade_starts_from_current_level(self, track):
        light = make_light(state=Steady(20.0))
        await light.async_turn_on(brightness=255, transition=30)

        assert isinstance(light._state, Fading)
        assert light._state.start == 20.0
        assert light._state.target == 100.0
        assert light._state.duration == 30.0
        track.assert_called_once()

    @pytest.mark.asyncio
    async def test_new_fade_mid_fade_starts_from_interpolated_level(self):
        light = make_light(state=fading(start=0.0, target=100.0, elapsed=5, duration=10))
        await light.async_turn_off(transition=10)

        assert light._state.start == pytest.approx(50.0, abs=1.0)
        assert light._state.target == 0.0

    def test_interpolated_at_midpoint(self):
        light = make_light(state=fading(start=0.0, target=100.0, elapsed=5, duration=10))
        assert light.brightness == pytest.approx(128, abs=3)

    def test_interpolated_at_start(self):
        light = make_light(state=fading(start=0.0, target=100.0, elapsed=0, duration=10))
        assert light.brightness <= 3

    def test_interpolation_clamps_to_target(self):
        light = make_light(state=fading(start=0.0, target=100.0, elapsed=20, duration=10))
        assert light.brightness == 255

    def test_tick_completes_at_target(self):
        light = make_light(state=Steady(0.0))
        light._transition(fading(start=0.0, target=100.0, elapsed=11, duration=10))
        unsub = light._tick_unsub

        light._fade_tick(None)

        assert light._state == Steady(100.0)
        unsub.assert_called_once()
        assert light._tick_unsub is None

    def test_tick_fade_to_zero_turns_off(self):
        light = make_light(state=fading(start=50.0, target=0.0, elapsed=11, duration=10))

        light._fade_tick(None)

        assert light._state == Steady(0.0)
        assert light.is_on is False

    def test_tick_mid_fade_writes_without_changing_state(self):
        state = fading(start=0.0, target=100.0, elapsed=5, duration=10)
        light = make_light(state=state)

        light._fade_tick(None)

        assert light._state is state
        light.async_write_ha_state.assert_called_once()

    def test_tick_outside_a_fade_does_nothing(self):
        light = make_light(state=Steady(40.0))
        light._fade_tick(None)
        light.async_write_ha_state.assert_not_called()

    def test_timer_runs_only_while_fading(self, track):
        light = make_light()
        light._transition(fading())
        assert light._tick_unsub is not None
        unsub = light._tick_unsub

        light._transition(Steady(10.0))

        unsub.assert_called_once()
        assert light._tick_unsub is None


# ---------------------------------------------------------------------------
# Pushes from the hub
# ---------------------------------------------------------------------------

class TestPushes:
    def test_external_push_updates_steady_level(self):
        light = make_light(state=Steady(0.0))
        light._handle_push("~OUTPUT,25,1,60.00", SOURCE_EXTERNAL)
        assert light._state == Steady(60.0)

    def test_different_level_overrides_fade(self):
        light = make_light()
        light._transition(fading(start=0.0, target=100.0))
        unsub = light._tick_unsub

        light._handle_push("~OUTPUT,25,1,30.00", SOURCE_EXTERNAL)

        assert light._state == Steady(30.0)
        unsub.assert_called_once()

    def test_echo_of_fade_target_is_ignored(self):
        state = fading(start=0.0, target=50.0)
        light = make_light(state=state)

        light._handle_push("~OUTPUT,25,1,50.00", SOURCE_EXTERNAL)

        assert light._state is state

    def test_echo_while_sending_is_ignored(self):
        """The hub can broadcast our setpoint before the ack returns."""
        state = Sending(target=50.0, fade=30.0, previous=Steady(0.0))
        light = make_light(state=state)

        light._handle_push("~OUTPUT,25,1,50.00", SOURCE_EXTERNAL)

        assert light._state is state

    def test_different_level_while_sending_overrides(self):
        light = make_light(state=Sending(target=50.0, fade=30.0, previous=Steady(0.0)))
        light._handle_push("~OUTPUT,25,1,80.00", SOURCE_EXTERNAL)
        assert light._state == Steady(80.0)

    def test_internal_push_is_ignored(self):
        light = make_light(state=Steady(0.0))
        light._handle_push("~OUTPUT,25,1,75.00", SOURCE_INTERNAL)
        assert light._state == Steady(0.0)

    def test_other_zone_is_ignored(self):
        light = make_light(zone_id=25, state=Steady(0.0))
        light._handle_push("~OUTPUT,99,1,75.00", SOURCE_EXTERNAL)
        assert light._state == Steady(0.0)

    def test_malformed_lines_are_ignored(self):
        light = make_light(state=Steady(0.0))
        for line in ("~OUTPUT,25", "~OUTPUT,abc,1,75", "~OUTPUT,25,1,abc", "~DEVICE,25,1,75"):
            light._handle_push(line, SOURCE_EXTERNAL)
        assert light._state == Steady(0.0)

    def test_only_action_1_is_a_level(self):
        light = make_light(state=Steady(0.0))
        light._handle_push("~OUTPUT,25,29,6", SOURCE_EXTERNAL)
        light._handle_push("~OUTPUT,25,30,1,75.00", SOURCE_EXTERNAL)
        assert light._state == Steady(0.0)

    def test_push_while_unavailable_but_linked_reads_level(self):
        light = make_light(state=Unavailable())
        light._handle_push("~OUTPUT,25,1,40.00", SOURCE_EXTERNAL)
        assert light._state == Steady(40.0)

    def test_push_while_link_down_is_ignored(self):
        light = make_light(state=Unavailable())
        light._connection.available = False
        light._handle_push("~OUTPUT,25,1,40.00", SOURCE_EXTERNAL)
        assert light._state == Unavailable()


# ---------------------------------------------------------------------------
# Failures
# ---------------------------------------------------------------------------

class TestFailures:
    @pytest.mark.asyncio
    async def test_hub_error_returns_to_previous_steady_and_raises(self):
        light = make_light(state=Steady(30.0))
        light._connection.set_light_level = AsyncMock(side_effect=HubError("#OUTPUT,25,1,100,0", "~ERROR,4"))

        with pytest.raises(HubError):
            await light.async_turn_on(brightness=255)

        assert light._state == Steady(30.0)

    @pytest.mark.asyncio
    async def test_hub_error_mid_fade_keeps_fading(self, track):
        state = fading(start=0.0, target=100.0, elapsed=2, duration=10)
        light = make_light(state=state)
        light._connection.set_light_level = AsyncMock(side_effect=HubError("#OUTPUT,25,1,0,5", "~ERROR,4"))

        with pytest.raises(HubError):
            await light.async_turn_off(transition=5)

        assert light._state is state
        assert light._tick_unsub is not None  # timer restarted

    @pytest.mark.asyncio
    async def test_link_failure_goes_unavailable_and_raises(self):
        light = make_light(state=Steady(30.0))

        async def fail(*args):
            light._connection.available = False
            raise NoReply("no reply")

        light._connection.set_light_level = AsyncMock(side_effect=fail)

        with pytest.raises(NoReply):
            await light.async_turn_on(brightness=255)

        assert light._state == Unavailable()
        assert light.available is False

    @pytest.mark.asyncio
    async def test_failure_after_override_keeps_the_override(self):
        light = make_light(state=Steady(0.0))

        async def fail(*args):
            light._handle_push("~OUTPUT,25,1,80.00", SOURCE_EXTERNAL)
            raise HubError("#OUTPUT,25,1,100,0", "~ERROR,4")

        light._connection.set_light_level = AsyncMock(side_effect=fail)

        with pytest.raises(HubError):
            await light.async_turn_on(brightness=255)

        assert light._state == Steady(80.0)


# ---------------------------------------------------------------------------
# Overrides and overlapping commands
# ---------------------------------------------------------------------------

class TestOverrideDuringAck:
    @pytest.mark.asyncio
    async def test_pico_press_during_ack_wins(self, track):
        light = make_light(state=Steady(0.0))

        async def set_level(zone_id, level, fade):
            # Pico sets 80% while our fade command is in flight
            light._handle_push("~OUTPUT,25,1,80.00", SOURCE_EXTERNAL)

        light._connection.set_light_level = AsyncMock(side_effect=set_level)

        await light.async_turn_on(brightness=128, transition=30)

        track.assert_not_called()
        assert light._state == Steady(80.0)

    @pytest.mark.asyncio
    async def test_late_ack_does_not_overwrite_newer_command(self):
        light = make_light(state=Steady(0.0))
        release_first = asyncio.Event()

        async def set_level(zone_id, level, fade):
            if level == 100:
                await release_first.wait()

        light._connection.set_light_level = AsyncMock(side_effect=set_level)

        first = asyncio.create_task(light.async_turn_on(brightness=255))
        await asyncio.sleep(0)
        await light.async_turn_on(brightness=51)  # 20%, acked immediately
        release_first.set()
        await first

        assert light._state == Steady(20.0)

    @pytest.mark.asyncio
    async def test_second_command_returns_to_settled_state_on_error(self):
        settled = Steady(10.0)
        light = make_light(state=Sending(target=100.0, fade=0.0, previous=settled))
        light._connection.set_light_level = AsyncMock(side_effect=HubError("#OUTPUT,25,1,50,0", "~ERROR,4"))

        with pytest.raises(HubError):
            await light.async_turn_on(brightness=128)

        assert light._state is settled


# ---------------------------------------------------------------------------
# Availability
# ---------------------------------------------------------------------------

class TestAvailability:
    def test_starts_unavailable_until_level_is_read(self):
        light = LutronFaderLight(
            hass=MagicMock(), connection=MagicMock(), name="x", zone_id=25, unique_id="u",
        )
        assert light.available is False

    def test_link_down_from_fade_stops_timer_and_writes(self):
        light = make_light()
        light._transition(fading())
        unsub = light._tick_unsub
        light.async_write_ha_state.reset_mock()

        light._handle_availability(False)

        assert light._state == Unavailable()
        unsub.assert_called_once()
        light.async_write_ha_state.assert_called_once()

    def test_link_up_requests_refresh(self):
        light = make_light(state=Unavailable())
        light.async_schedule_update_ha_state = MagicMock()

        light._handle_availability(True)

        light.async_schedule_update_ha_state.assert_called_once_with(True)

    @pytest.mark.asyncio
    async def test_remove_stops_timer(self):
        light = make_light()
        light._transition(fading())
        unsub = light._tick_unsub

        await light.async_will_remove_from_hass()

        unsub.assert_called_once()


# ---------------------------------------------------------------------------
# Supported features
# ---------------------------------------------------------------------------

class TestSupportedFeatures:
    def test_transition_feature_declared(self):
        light = make_light()
        assert light._attr_supported_features == 32  # LightEntityFeature.TRANSITION stub


# ---------------------------------------------------------------------------
# Fades end on the hub's own setpoint (no follow-up snap command)
# ---------------------------------------------------------------------------

class TestNoSnap:
    @pytest.mark.asyncio
    async def test_fade_off_sends_single_command(self):
        light = make_light(state=Steady(100.0))
        await light.async_turn_off(transition=30)

        light._state = Fading(**{**light._state.__dict__, "t0": light._state.t0 - 31})
        light._fade_tick(None)

        light._connection.set_light_level.assert_called_once_with(25, 0, 30)
        assert light._state == Steady(0.0)
        assert light.is_on is False

    @pytest.mark.asyncio
    async def test_fade_to_full_sends_single_command(self):
        light = make_light()
        await light.async_turn_on(brightness=255, transition=30)

        light._state = Fading(**{**light._state.__dict__, "t0": light._state.t0 - 31})
        light._fade_tick(None)

        light._connection.set_light_level.assert_called_once_with(25, 100, 30)
        assert light._state == Steady(100.0)
        assert light.brightness == 255
