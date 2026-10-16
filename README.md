# Lutron Fader Dev

Development workspace for the Lutron Fader project: a Home Assistant custom integration that shows the **progress of native Lutron fades**, plus its companion Lovelace card.

## What This Project Does

Lutron dimmers fade in hardware, and Home Assistant's official Lutron Caseta integration can already start those fades: `light.turn_on` with a `transition` sends a native fade over Lutron's local LEAP API, with no cap on its length. But the hub only ever reports a fade's **target**. Home Assistant shows a light at its end brightness the moment a fade starts, with no way to see where it is or when it ends.

Lutron Fader fills that gap. It never talks to the hub. It watches Home Assistant for fades sent to Lutron lights, calculates each fade's progress, and publishes it as a `sensor.<light>_fade` per light. The card and your automations keep using the official lights.

> **History:** versions up to 1.x drove the lights themselves over a Telnet connection to a Smart Bridge Pro. Once it turned out LEAP already exposes native fades, 2.0 became a tracker. The Telnet code is in git history (`lutron-fader` commit `3e9540e`).

## How It Works

```
 card / automation / script ──light.turn_on{transition}──▶ lutron_caseta ──LEAP──▶ hub
            │ call_service event                │ state_changed (light jumps to target)
            ▼                                   ▼
   FadeRouter (__init__.py) ──routes──▶ FadeSensor (sensor.py, one per light)
     resolves entity / area /                └── FadeTracker (tracker.py)
     device / floor targets                        Idle / Pending / Fading
                                                   │
                                                   ▼
                       sensor.living_room_floor_lamp_fade   state: idle | fading
                         attrs: light_entity_id, brightness, brightness_pct, start_pct,
                                target_pct, started_at, ends_at, progress
```

- **`FadeRouter`** finds every dimmable, fade-capable light from `lutron_caseta`, `lutron_caseta_pro` or `lutron` in the entity registry. It reloads when such lights are added, removed, renamed, or gain the needed capabilities.
- **It listens to `call_service` events** for `light.turn_on` / `light.turn_off` that carry a `transition`. HA fires that event *before* the light acts, so the light's current state is the fade's start. Targets given by area, device, floor or label are resolved to lights.
- **Each `FadeSensor`** watches its light's `state_changed` events and runs a 1-second refresh only while a fade is pending or running. It sits on the light's device and saves the running fade so it survives a restart. Its per-second attributes are kept out of the recorder.

## The Fade State Machine (`tracker.py`)

```
    Idle ──call with transition──▶ Pending(start, started_at, duration)
     ▲                               │ light reports a new level → that's the target
     │ already at target, or         ▼
     │ no report within 5s       Fading(start, target, started_at, ends_at)
     │◀──────────────────────────── │ tick: publish the calculated level
     │◀── now ≥ ends_at ────────────┤
     │◀── light changes to ≠ target, off, or unavailable (override)
         new call while Fading ──▶ Pending(start = current calculated level)
```

- **One `_state` field per tracker,** holding one of three frozen dataclasses (`Idle`, `Pending`, `Fading`). It changes only in `_transition()`, and everything shown is derived from it.
- **The target comes from the light, not the call.** Whatever the light reports right after the command is the target. That handles `brightness`, `brightness_pct`, a bare `turn_on` and `turn_off` without parsing them.
- **A new fade mid-fade starts from our calculated level,** because HA is showing the old target by then.
- **Times are wall-clock UTC,** so `restore()` can resume a fade after a restart, if it hasn't ended and the light still reports its target.
- **Echo tolerance:** ±3 on the 0–255 scale.
- **No HA imports,** so it's unit-tested on its own.

**Limits:** fades started outside HA (the Lutron app, a Pico, hub scenes) only ever show up as their target, so they aren't tracked. Progress is calculated as an even fade; no Lutron API reports the in-between level.

## Repo Structure

```
lutron-fader-dev/
├── lutron-fader/        # Submodule: HA integration (custom_components)
├── lutron-fader-card/   # Submodule: Lovelace card
├── tests/               # Unit + Home Assistant integration tests (no hub required)
├── scripts/             # Diagnostic scripts (real hub required)
└── examples/            # Dashboard configuration examples
```

## Related Repos

| Repo | Purpose | HACS |
|------|---------|------|
| [lutron-fader](https://github.com/rblancarte/lutron-fader) | HA integration | Integration |
| [lutron-fader-card](https://github.com/rblancarte/lutron-fader-card) | Lovelace card | Frontend |
| [lutron-fader-dev](https://github.com/rblancarte/lutron-fader-dev) | This repo | — |

## Getting Started

Clone with submodules:

```bash
git clone --recurse-submodules https://github.com/rblancarte/lutron-fader-dev
```

If you already cloned without submodules:

```bash
git submodule update --init --recursive
```

## Running Tests

No Lutron hub is needed. The integration tests run inside a real Home Assistant using `pytest-homeassistant-custom-component`, which installs Home Assistant itself. Use Python 3.14 (what Home Assistant 2026.10 runs on):

```bash
python3.14 -m venv .venv
.venv/bin/pip install pytest-homeassistant-custom-component
.venv/bin/python -m pytest          # settings come from pytest.ini
```

- `tests/test_tracker.py`: the state machine on its own
- `tests/test_integration.py`: end to end with fake Lutron lights:
  - setup and which lights are tracked
  - fades and fading off
  - area targets
  - overrides
  - restarts
  - migration from 1.x
  - new lights
  - the sensor entity ID

## Diagnostic Scripts

| Script | Purpose |
|--------|---------|
| `monitor_zones.py` | Watch a Smart Bridge Pro's Telnet push stream: zone levels and Pico presses (hardcodes the hub IP; edit before running) |

### Background: the Lutron Integration Protocol (Telnet)
`monitor_zones.py` talks the Lutron Integration Protocol: a line-based text protocol over Telnet (port 23) on Smart Bridge Pro, RadioRA 2 and HomeWorks systems. Log in with `lutron` / `integration`. Then:
- **Set a level:** `#OUTPUT,<zone>,1,<level>,<HH:MM:SS fade>`
- **Query a level:** `?OUTPUT,<zone>,1`
- **Push:** the hub sends `~OUTPUT,<zone>,1,<level>` when a level changes, and `~DEVICE,<id>,<button>,<3=press|4=release>` for Pico buttons.

Like LEAP, it reports only a fade's target. Fade times of 60s or more must use `HH:MM:SS`. The full specification is Lutron P/N 040249: [Lutron Integration Protocol PDF](https://assets.lutron.com/a/documents/040249.pdf). `*.pdf` is gitignored, so a local copy is safe here.

## Submodule Workflow

After making changes in a submodule and pushing them:

```bash
# Update the submodule pointer in this repo
git add lutron-fader   # or lutron-fader-card
git commit -m "Update submodule to latest"
git push
```

To pull latest changes in all submodules:

```bash
git submodule update --remote
```
