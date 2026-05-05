# Lutron Fader Dev

Development workspace for the Lutron Fader project — a Home Assistant custom integration that unlocks hardware-native fade capabilities on Lutron systems via the **Lutron Integration Protocol (LIP)**.

## What This Project Does

Home Assistant's built-in Lutron Caseta integration is limited to what the official cloud API exposes. The Lutron Integration Protocol — accessible over a direct Telnet connection to the hub — provides far richer control: extended fade times, zone queries, real-time push events, and more features that the cloud API never surfaces.

This project is a starting point for building a full-featured HA integration on top of the Lutron Integration Protocol. The first capability implemented is long fade times (minutes to hours), which is not possible through the standard integration. The architecture is designed to grow — each additional protocol feature can be added without restructuring the core connection layer.

## How It Works

```
Home Assistant
    └── LutronFaderLight (light.py)
            └── LutronTelnetConnection (lutron_telnet.py)
                    └── Telnet → Lutron Hub (port 23)
                            └── Lutron Integration Protocol
```

When a fade command is issued, `LutronTelnetConnection` sends a command to the hub over Telnet and receives an acknowledgment. The hub reports the target level immediately rather than streaming intermediate values during the fade, so `LutronFaderLight` runs a client-side interpolation timer that pushes live brightness updates to HA every second for the duration of the fade.

## The Lutron Integration Protocol

The Lutron Integration Protocol is a line-based text protocol spoken over a Telnet connection to the hub (port 23). After logging in, you send commands and receive responses in real time.

**Login sequence:**
```
login: lutron
password: integration
GNET>
```

**Command format:**
```
#OUTPUT,<zone_id>,<action>,<level>,<fade_time>
```

**Query format:**
```
?OUTPUT,<zone_id>,<action>
```

**Push response format:**
```
~OUTPUT,<zone_id>,<action>,<level>
```

Where `action 1` = zone level, `level` is 0–100 (percentage).

**Key gotcha — fade time format:**  
Raw integer fade times only work for values 0–59 (treated as seconds). For 60 seconds or longer, the hub silently ignores a raw integer and snaps immediately. Fade times must be formatted as `HH:MM:SS`:

```
# Works — snaps instantly despite the value:
#OUTPUT,5,1,50,60

# Works correctly — fades over 1 minute:
#OUTPUT,5,1,50,00:01:00
```

**Sample session** (via `nc <hub_ip> 23`):
```
login: lutron
password: integration
GNET> ?OUTPUT,5,1
~OUTPUT,5,1,41.18
GNET> #OUTPUT,5,1,0,00:30:00
~OUTPUT,5,1,0.00
GNET>
```

The hub acknowledges a fade command immediately with the target level. The physical light then fades over the specified time.

**Caseta Pro supported OUTPUT actions:**  
The ICD documents many OUTPUT action numbers, but Caseta Pro (L-BDGPRO2-WH) only supports four — confirmed through testing:

| Action | Command | Description |
|--------|---------|-------------|
| 1 | `#OUTPUT,<id>,1,<level>,<fade>` | Set level (with optional fade time) |
| 2 | `#OUTPUT,<id>,2` | Start Raising |
| 3 | `#OUTPUT,<id>,3` | Start Lowering |
| 4 | `#OUTPUT,<id>,4` | Stop |

Action 1 is the one this integration uses. The fade time argument is what makes it useful — see the `HH:MM:SS` format note above.

**Protocol specification:**  
The full Lutron Integration Protocol specification (P/N 040249) is publicly hosted by Lutron: [Lutron Integration Protocol PDF](https://assets.lutron.com/a/documents/040249.pdf). If you want a local copy for reference, dropping it in this directory is safe — `*.pdf` is gitignored.

## Repo Structure

```
lutron-fader-dev/
├── lutron-fader/        # Submodule — HA integration (custom_components)
├── lutron-fader-card/   # Submodule — Lovelace card
├── tests/               # Unit tests (no hub required)
├── scripts/             # Integration/diagnostic scripts (real hub required)
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

Tests are unit tests and require no Lutron hub:

```bash
pip install pytest pytest-asyncio
python3 -m pytest tests/ -v
```

## Integration Scripts

Scripts in `scripts/` require a real Lutron hub. Update the host IP in each script before running:

| Script | Purpose |
|--------|---------|
| `monitor_zones.py` | Watch live zone updates — useful for discovering zone IDs |
| `debug_query.py` | Test query commands against the hub |
| `test_lutron_telnet.py` | Full manual test suite with interactive mode |
| `test_connection_longevity.py` | Test how long the hub keeps a connection alive |
| `test_lip_queries.py` | Test various Lutron Integration Protocol commands |

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
