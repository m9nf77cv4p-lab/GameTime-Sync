# GameTime-Sync

GameTime-Sync is the persistent, authoritative data bridge for the 2026 GameTime fantasy-football league.

## Source of truth

- Sleeper league: `1385505283328966656`
- Sleeper 2026 draft: `1385505283337367552`
- Sleeper 2025 draft used for keeper round costs: `1268005654409269248`
- Format: 10-team PPR, one keeper maximum

Only Sleeper's `rosters[].keepers` field confirms a keeper. Player discussions, roster membership, and prior baselines are not treated as confirmation.

## Data files

- `data/gametime-master.json` is the project-facing master record.
- `data/raw/` preserves the exact Sleeper responses used to build it.
- `data/history/previous.json` preserves the prior master snapshot whenever source data changes.
- Git history provides the complete long-term change record.

The master record contains the team/manager mapping, confirmed keepers, original 2025 draft-round costs, league settings, draft settings, draft order, and current picks. Missing values remain `null`; the sync never invents data.

## Refresh behavior

The GitHub Action runs every 15 minutes and can also be started manually. It validates the authoritative IDs and expected league shape before writing. If the Sleeper payload is unchanged, the generated files remain byte-for-byte unchanged and no commit is created.

Run locally with Python 3.11 or newer:

```bash
python -m unittest discover -s tests -v
python scripts/sync_gametime.py
```
