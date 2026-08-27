# GameTime-Sync

GameTime-Sync is the persistent, authoritative data bridge for the 2026 GameTime fantasy-football league.

## Source of truth

- Sleeper league: `1396356110482407424`
- Sleeper 2026 draft: `1396356110490824704`
- Sleeper 2025 draft used for keeper round costs: `1268005654409269248`
- Format: 10-team PPR, one keeper maximum

Keeper confirmations use Sleeper roster keeper flags and reserved picks while the draft is `pre_draft`. Player discussions and roster membership alone do not confirm keepers.

## Data files

- `data/gametime-master.json` is the project-facing master record.
- `data/raw/` preserves the exact Sleeper responses used to build it.
- `data/history/previous.json` preserves the prior master snapshot whenever source data changes.
- Git history provides the complete long-term change record.

The master record contains the team/manager mapping, confirmed keepers, original 2025 draft-round costs, league settings, draft settings, draft order, and current picks. Missing values remain `null`; the sync never invents data.

## Refresh behavior

The regular GitHub Action is scheduled every five minutes and can also be started manually. GitHub may delay scheduled runs. It validates the authoritative IDs and expected league shape before writing. If the Sleeper payload is unchanged, the generated files remain byte-for-byte unchanged and no commit is created.

Run locally with Python 3.11 or newer:

```bash
python -m unittest discover -s tests -v
python scripts/sync_gametime.py
```

## Live draft mode

After merging, open **Actions → Live GameTime Draft → Run workflow**, select
`main`, leave `minutes` at `240` (or enter 1–240), and press **Run workflow**.
Start it shortly before drafting. It is manual, not automatically scheduled.
The runner stops when Sleeper reports `complete`, the duration expires, or
five consecutive source requests fail. Use **Cancel workflow** to stop early.
The run consumes up to four hours of Actions runner time; account limits apply.

The worker requests league state, draft state, and picks concurrently on a
15-second target cadence. It publishes changes immediately after each successful
poll, and publishes a heartbeat at least once per 60 seconds while healthy.
HTTP latency, commit/push latency, runner queues, and GitHub caching add delay;
15 seconds is a polling target, not a guaranteed end-to-end update time.
Errors back off up to 60 seconds and retain the last successful snapshot.

**During the draft, read `data/gametime-live-draft.json` for picks, draft order,
and source-provided league metadata.** Check `sync.last_success_at` and
`sync.status`; a last success older than 90 seconds is stale even if status
still says running. On hard cancellation, publishing failure, or runner failure,
the final status may not be published. Never report a stale feed as live.

The dedicated feed preserves exact live picks, including corrections and undo.
Preloaded keeper selections can occur in late rounds: do not infer progress or
the next pick from the highest pick number. `keeper_baseline` and `teams` are
context from the master at startup, not refreshed roster/keeper confirmations.
Use raw live picks for player unavailability; source metadata for the current
pick may be absent or briefly inconsistent because the endpoints are not atomic.

Live and regular syncs share a concurrency lock. The normal master snapshot
does not refresh while live mode holds that lock; scheduled sync resumes after
release. The worker only commits its dedicated feed, never force-pushes, and
fails visibly on a publishing error. No new secrets or third-party service are
required; existing repository Actions write permissions must be enabled.

This does not automatically refresh a previously saved HTML strategy board or
send chat/push notifications. Ask for a draft update to have the live feed read
and recommendations adjusted. It cannot make Sleeper draft selections.

Local preview (writes the dedicated feed without pushing):

```bash
python scripts/live_draft.py --interval 15 --minutes 1
```

References: [Sleeper API](https://docs.sleeper.com/),
[GitHub workflow syntax](https://docs.github.com/en/actions/reference/workflows-and-actions/workflow-syntax).
