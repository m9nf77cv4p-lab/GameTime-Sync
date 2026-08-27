#!/usr/bin/env python3
"""Bounded, read-only Sleeper polling; optionally publish a dedicated live feed."""
import argparse
import copy
import os
import signal
import subprocess
import time
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timezone

from sync_gametime import (
    ROOT, DEFAULT_CONFIG, DEFAULT_OUTPUT, SyncError, fetch_json, iso_utc,
    read_json, stable_hash, write_json,
)

OUTPUT = ROOT / 'data' / 'gametime-live-draft.json'


def validate_source(config, league, draft, picks):
    if not isinstance(league, dict) or not isinstance(draft, dict) or not isinstance(picks, list):
        raise SyncError('Invalid live draft response shape')
    if (str(league.get('league_id')) != str(config['league_id'])
            or str(league.get('draft_id')) != str(config['draft_id'])
            or str(draft.get('draft_id')) != str(config['draft_id'])
            or str(league.get('season')) != str(config['season'])):
        raise SyncError('Live response does not match configured GameTime source')
    seen = set()
    for pick in picks:
        if (not isinstance(pick, dict)
                or str(pick.get('draft_id')) != str(config['draft_id'])
                or not isinstance(pick.get('pick_no'), int)
                or pick['pick_no'] < 1 or not pick.get('player_id')
                or pick['pick_no'] in seen):
            raise SyncError('Invalid, duplicate, or wrong-draft pick')
        seen.add(pick['pick_no'])


def fetch_state(config):
    paths = [f"/league/{config['league_id']}", f"/draft/{config['draft_id']}",
             f"/draft/{config['draft_id']}/picks"]
    with ThreadPoolExecutor(max_workers=3) as pool:
        league, draft, picks = list(pool.map(fetch_json, paths))
    validate_source(config, league, draft, picks)
    # No highest-pick inference: preloaded keepers can occupy late rounds.
    return {'league': league, 'draft': draft,
            'picks': sorted(picks, key=lambda p: p['pick_no'])}


def git(*args):
    return subprocess.run(['git', *args], cwd=ROOT, check=True,
                          capture_output=True, text=True, timeout=45).stdout.strip()


def publish(payload):
    # Write atomically so local readers cannot see half a snapshot.
    temporary = OUTPUT.with_suffix('.tmp')
    write_json(temporary, payload)
    os.replace(temporary, OUTPUT)


def publish_git(payload):
    publish(payload)
    git('add', 'data/gametime-live-draft.json')
    if not git('diff', '--cached', '--name-only'):
        return
    git('commit', '-m', 'Refresh GameTime live draft')
    # The workflow's shared concurrency group serializes scheduled syncs.
    # Rebase also handles an unrelated main-branch edit; never force-push.
    git('pull', '--rebase', 'origin', 'main')
    git('push', 'origin', 'HEAD:main')


def run(config, baseline, *, interval=15, minutes=240, emit=publish,
        fetch=fetch_state, clock=time.monotonic, sleep=time.sleep,
        now=lambda: iso_utc(datetime.now(timezone.utc)), stopped=lambda: False):
    if not 10 <= interval <= 60 or not 1 <= minutes <= 240:
        raise ValueError('Interval must be 10–60 seconds; duration 1–240 minutes')
    source = (baseline or {}).get('source', {})
    if any(str(source.get(k)) != str(config[k]) for k in ('league_id', 'draft_id')):
        raise SyncError('Master snapshot must match the configured GameTime league/draft')
    deadline = clock() + minutes * 60
    last_published = float('-inf')
    fingerprint = None
    errors = 0
    payload = {'schema_version': 1, 'source': source,
               'teams': baseline.get('teams', []),
               'keeper_baseline': baseline.get('confirmed_keepers', []),
               'keeper_baseline_note': 'Context only; raw live picks are authoritative. '
                                       'Do not treat preloaded keepers as live selections.',
               'sync': {'last_success_at': None, 'poll_interval_seconds': interval,
                        'started_at': now(), 'max_duration_minutes': minutes}}
    reason = 'duration_limit'
    while clock() < deadline and not stopped():
        started = clock()
        try:
            state = fetch(config)
        except (SyncError, KeyError, TypeError, ValueError) as exc:
            errors += 1
            payload['sync'].update(status='retrying', error=str(exc),
                                   consecutive_errors=errors)
            emit(copy.deepcopy(payload))
            if errors >= 5:
                reason = 'source_error'
                break
            sleep(max(0, min(interval * 2 ** (errors - 1), 60, deadline - clock())))
            continue
        errors = 0
        payload.update(state)
        payload['sync'].update(status='running', last_success_at=now(),
                               consecutive_errors=0, error=None)
        current = stable_hash(state)
        if state['draft'].get('status') == 'complete':
            reason = 'draft_complete'
            break
        if current != fingerprint or clock() - last_published >= 60:
            emit(copy.deepcopy(payload))
            fingerprint, last_published = current, clock()
        sleep(max(0, min(interval - (clock() - started), deadline - clock())))
    if stopped():
        reason = 'cancelled'
    payload['sync'].update(status='stopped', stop_reason=reason, stopped_at=now())
    emit(copy.deepcopy(payload))
    return 1 if reason == 'source_error' else 0


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--interval', type=int, default=15)
    parser.add_argument('--minutes', type=int, default=240)
    parser.add_argument('--publish', action='store_true', help='Commit and push live feed to main')
    args = parser.parse_args()
    stop = []
    for sig in (signal.SIGTERM, signal.SIGINT):
        signal.signal(sig, lambda *_: stop.append(True))
    try:
        return run(read_json(DEFAULT_CONFIG), read_json(DEFAULT_OUTPUT),
                   interval=args.interval, minutes=args.minutes,
                   emit=publish_git if args.publish else publish,
                   stopped=lambda: bool(stop))
    except (SyncError, ValueError, TypeError, KeyError, OSError,
            subprocess.SubprocessError) as exc:
        # Publishing failures are fatal: do not pretend the remote feed is fresh.
        print(f'Live draft failed: {exc}', flush=True)
        return 1


if __name__ == '__main__':
    raise SystemExit(main())
