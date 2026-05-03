# Lutron Fader Dev

Development workspace for the Lutron Fader project. Contains tests, integration scripts, and examples. The integration and card live in their own repos and are included here as git submodules.

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
| `test_lip_queries.py` | Test various LIP protocol commands |

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
