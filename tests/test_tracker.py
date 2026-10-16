"""Tests for the fade-tracking state machine (no Home Assistant needed)."""
from datetime import datetime, timedelta, timezone

import sys
import os
sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..', 'lutron-fader'))

from custom_components.lutron_fader.tracker import FadeTracker, Fading, Idle, Pending

T0 = datetime(2026, 10, 15, 12, 0, 0, tzinfo=timezone.utc)


def at(seconds: float) -> datetime:
    return T0 + timedelta(seconds=seconds)


def fading_tracker(start=0.0, target=255.0, duration=100.0) -> FadeTracker:
    """A tracker that confirmed a fade starting at T0."""
    t = FadeTracker()
    t.on_call(start, duration, "ctx", T0)
    t.on_light_state(target, at(0.1))
    assert isinstance(t.state, Fading)
    return t


# ---------------------------------------------------------------------------
# Starting a fade
# ---------------------------------------------------------------------------

class TestStart:
    def test_starts_idle(self):
        t = FadeTracker()
        assert t.state == Idle()
        assert t.state_name() == "idle"
        assert t.attributes(T0) == {}

    def test_call_with_transition_goes_pending(self):
        t = FadeTracker()
        assert t.on_call(50.0, 30, "ctx", T0) is True
        assert t.state == Pending(50.0, T0, 30.0, "ctx")
        assert t.state_name() == "idle"  # not shown as fading until confirmed

    def test_light_reporting_target_starts_the_fade(self):
        t = FadeTracker()
        t.on_call(0.0, 30, "ctx", T0)

        assert t.on_light_state(255.0, at(0.2)) is True

        assert t.state == Fading(0.0, 255.0, T0, at(30))
        assert t.state_name() == "fading"

    def test_fade_off_starts_on_off_report(self):
        t = FadeTracker()
        t.on_call(200.0, 60, "ctx", T0)
        t.on_light_state(0.0, at(0.1))  # light reports off
        assert t.state == Fading(200.0, 0.0, T0, at(60))

    def test_zero_transition_does_not_track(self):
        t = FadeTracker()
        t.on_call(0.0, 0, "ctx", T0)
        assert t.state == Idle()

    def test_already_at_target_does_not_fade(self):
        t = FadeTracker()
        t.on_call(128.0, 30, "ctx", T0)
        t.on_light_state(129.0, at(0.1))  # within tolerance of the start
        assert t.state == Idle()

    def test_pending_times_out_without_a_report(self):
        t = FadeTracker()
        t.on_call(0.0, 30, "ctx", T0)
        assert t.on_tick(at(4)) is False
        assert t.on_tick(at(5)) is True
        assert t.state == Idle()

    def test_unavailable_while_pending_goes_idle(self):
        t = FadeTracker()
        t.on_call(0.0, 30, "ctx", T0)
        t.on_light_state(None, at(0.1))
        assert t.state == Idle()

    def test_late_confirmation_after_fade_would_end_goes_idle(self):
        t = FadeTracker()
        t.on_call(0.0, 2, "ctx", T0)
        t.on_light_state(255.0, at(3))
        assert t.state == Idle()


# ---------------------------------------------------------------------------
# During a fade
# ---------------------------------------------------------------------------

class TestFading:
    def test_level_is_interpolated(self):
        t = fading_tracker(start=0.0, target=200.0, duration=100)
        assert t.state.level_at(at(0)) == 0.0
        assert t.state.level_at(at(25)) == 50.0
        assert t.state.level_at(at(100)) == 200.0
        assert t.state.level_at(at(500)) == 200.0  # clamped

    def test_fade_down_is_interpolated(self):
        t = fading_tracker(start=255.0, target=0.0, duration=10)
        assert t.state.level_at(at(5)) == 127.5

    def test_attributes_mid_fade(self):
        t = fading_tracker(start=0.0, target=255.0, duration=100)
        assert t.attributes(at(50)) == {
            "brightness": 128,  # round(127.5)
            "brightness_pct": 50,
            "start_pct": 0,
            "target_pct": 100,
            "started_at": T0.isoformat(),
            "ends_at": at(100).isoformat(),
            "progress": 50,
        }

    def test_target_echo_is_ignored(self):
        t = fading_tracker(target=255.0)
        assert t.on_light_state(254.0, at(10)) is False
        assert isinstance(t.state, Fading)

    def test_override_ends_the_fade(self):
        t = fading_tracker(target=255.0)
        assert t.on_light_state(76.0, at(10)) is True  # Pico set 30%
        assert t.state == Idle()

    def test_turned_off_mid_fade_ends_it(self):
        t = fading_tracker(target=255.0)
        t.on_light_state(0.0, at(10))
        assert t.state == Idle()

    def test_unavailable_mid_fade_ends_it(self):
        t = fading_tracker()
        t.on_light_state(None, at(10))
        assert t.state == Idle()

    def test_fade_ends_when_time_is_up(self):
        t = fading_tracker(duration=100)
        assert t.on_tick(at(99)) is False
        assert t.on_tick(at(100)) is True
        assert t.state == Idle()

    def test_zero_transition_mid_fade_stops_tracking(self):
        t = fading_tracker()
        t.on_call(255.0, 0, "ctx2", at(10))
        assert t.state == Idle()


# ---------------------------------------------------------------------------
# A new fade during a fade
# ---------------------------------------------------------------------------

class TestRetarget:
    def test_new_fade_starts_from_calculated_level(self):
        t = fading_tracker(start=0.0, target=200.0, duration=100)
        # HA still shows 200 (the old target); the real level is 100 at t=50
        t.on_call(200.0, 20, "ctx2", at(50))

        assert t.state == Pending(100.0, at(50), 20.0, "ctx2")

        t.on_light_state(0.0, at(50.1))
        assert t.state == Fading(100.0, 0.0, at(50), at(70))


# ---------------------------------------------------------------------------
# Restoring after a restart
# ---------------------------------------------------------------------------

class TestRestore:
    def test_resumes_when_light_still_at_target(self):
        t = FadeTracker()
        assert t.restore(0.0, 255.0, T0, at(100), 255.0, at(40)) is True
        assert t.state == Fading(0.0, 255.0, T0, at(100))
        assert t.attributes(at(40))["progress"] == 40

    def test_does_not_resume_after_end(self):
        t = FadeTracker()
        t.restore(0.0, 255.0, T0, at(100), 255.0, at(101))
        assert t.state == Idle()

    def test_does_not_resume_if_light_moved(self):
        t = FadeTracker()
        t.restore(0.0, 255.0, T0, at(100), 76.0, at(40))
        assert t.state == Idle()

    def test_does_not_resume_if_light_unavailable(self):
        t = FadeTracker()
        t.restore(0.0, 255.0, T0, at(100), None, at(40))
        assert t.state == Idle()
