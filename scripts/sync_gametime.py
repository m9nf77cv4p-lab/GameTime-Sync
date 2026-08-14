#!/usr/bin/env python3
"""Build GameTime's authoritative snapshot from Sleeper's read-only API."""

from __future__ import annotations

import argparse
import hashlib
import json
import sys
import urllib.error
import urllib.request
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any
from zoneinfo import ZoneInfo

API_ROOT = "https://api.sleeper.app/v1"
ROOT = Path(__file__).resolve().parents[1]
DEFAULT_CONFIG = ROOT / "config" / "gametime.json"
DEFAULT_OUTPUT = ROOT / "data" / "gametime-master.json"
RAW_DIR = ROOT / "data" / "raw"
PREVIOUS_SNAPSHOT = ROOT / "data" / "history" / "previous.json"
LOCAL_TIMEZONE = ZoneInfo("America/New_York")


class SyncError(RuntimeError):
    pass


def fetch_json(path: str) -> Any:
    request = urllib.request.Request(
        f"{API_ROOT}{path}", headers={"User-Agent": "GameTime-Sync/1.0"}
    )
    try:
        with urllib.request.urlopen(request, timeout=30) as response:
            if response.status != 200:
                raise SyncError(f"Sleeper returned HTTP {response.status} for {path}")
            return json.load(response)
    except (urllib.error.URLError, TimeoutError, json.JSONDecodeError) as exc:
        raise SyncError(f"Unable to fetch valid Sleeper data from {path}: {exc}") from exc


def cleaned_name(value: Any) -> str | None:
    if not isinstance(value, str):
        return None
    value = value.strip()
    return value or None


def stable_hash(value: Any) -> str:
    payload = json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=False)
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


def iso_utc(value: datetime) -> str:
    return value.astimezone(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")


def iso_local(value: datetime) -> str:
    return value.astimezone(LOCAL_TIMEZONE).replace(microsecond=0).isoformat()


def draft_schedule(league: dict[str, Any], draft: dict[str, Any]) -> dict[str, Any]:
    start_time_ms = draft.get("start_time")
    start_at = None
    if isinstance(start_time_ms, (int, float)) and start_time_ms > 0:
        start_at = datetime.fromtimestamp(start_time_ms / 1000, tz=timezone.utc)

    keeper_deadline_raw = (league.get("metadata") or {}).get("keeper_deadline")
    keeper_deadline_days = None
    try:
        if keeper_deadline_raw not in (None, ""):
            keeper_deadline_days = int(keeper_deadline_raw)
    except (TypeError, ValueError):
        keeper_deadline_days = None

    keeper_deadline_at = None
    if start_at is not None and keeper_deadline_days is not None:
        keeper_deadline_at = start_at - timedelta(days=keeper_deadline_days)

    return {
        "start_time_ms": int(start_time_ms) if isinstance(start_time_ms, (int, float)) else None,
        "start_time_utc": iso_utc(start_at) if start_at else None,
        "start_time_et": iso_local(start_at) if start_at else None,
        "keeper_deadline_days_before_draft": keeper_deadline_days,
        "keeper_deadline_utc": iso_utc(keeper_deadline_at) if keeper_deadline_at else None,
        "keeper_deadline_et": iso_local(keeper_deadline_at) if keeper_deadline_at else None,
        "keeper_deadline_source": "league.metadata.keeper_deadline",
    }


def keeper_player(picks_by_player: dict[str, dict[str, Any]], player_id: str) -> dict[str, Any]:
    pick = picks_by_player.get(player_id, {})
    metadata = pick.get("metadata") or {}
    first = cleaned_name(metadata.get("first_name"))
    last = cleaned_name(metadata.get("last_name"))
    full_name = " ".join(part for part in (first, last) if part) or None
    return {
        "player_id": player_id,
        "player_name": full_name,
        "position": metadata.get("position"),
        "nfl_team": metadata.get("team"),
    }


def build_snapshot(config: dict[str, Any], raw: dict[str, Any]) -> dict[str, Any]:
    league = raw["league"]
    users = raw["users"]
    rosters = raw["rosters"]
    draft = raw["draft"]
    current_picks = raw["current_draft_picks"]
    previous_picks = raw["previous_draft_picks"]

    league_id = str(config["league_id"])
    draft_id = str(config["draft_id"])
    if str(league.get("league_id")) != league_id:
        raise SyncError("Sleeper response did not match the authoritative GameTime league ID")
    if str(league.get("draft_id")) != draft_id or str(draft.get("draft_id")) != draft_id:
        raise SyncError("Sleeper response did not match the authoritative GameTime draft ID")
    if str(league.get("season")) != str(config["season"]):
        raise SyncError("Sleeper response did not match the configured GameTime season")
    if len(rosters) != int(config["expected_team_count"]):
        raise SyncError(f"Expected {config['expected_team_count']} rosters; received {len(rosters)}")

    users_by_id = {str(user["user_id"]): user for user in users}
    previous_by_player = {str(pick["player_id"]): pick for pick in previous_picks}
    teams: list[dict[str, Any]] = []
    keepers: list[dict[str, Any]] = []

    for roster in sorted(rosters, key=lambda item: int(item["roster_id"])):
        roster_id = int(roster["roster_id"])
        owner_id = str(roster.get("owner_id") or "")
        user = users_by_id.get(owner_id)
        if not user:
            raise SyncError(f"Roster {roster_id} has no matching league user")
        manager = cleaned_name(user.get("display_name")) or cleaned_name(user.get("username"))
        team_name = cleaned_name((user.get("metadata") or {}).get("team_name")) or manager
        keeper_ids = [str(value) for value in (roster.get("keepers") or [])]
        if len(keeper_ids) > int(config["max_keepers_per_team"]):
            raise SyncError(f"Roster {roster_id} exceeds the configured keeper limit")
        roster_players = [str(value) for value in (roster.get("players") or [])]
        if any(player_id not in roster_players for player_id in keeper_ids):
            raise SyncError(f"Roster {roster_id} contains a keeper absent from its player list")

        teams.append({
            "roster_id": roster_id,
            "owner_user_id": owner_id,
            "manager": manager,
            "team_name": team_name,
            "player_ids": roster_players,
            "confirmed_keeper_ids": keeper_ids,
        })
        for player_id in keeper_ids:
            prior = previous_by_player.get(player_id)
            keepers.append({
                "roster_id": roster_id,
                "team_name": team_name,
                "manager": manager,
                **keeper_player(previous_by_player, player_id),
                "confirmed": True,
                "confirmation_source": "league_rosters.keepers",
                "keeper_cost": {
                    "rule": "original_2025_draft_round",
                    "round": prior.get("round") if prior else None,
                    "pick_no": prior.get("pick_no") if prior else None,
                    "source_draft_id": str(config["previous_draft_id"]),
                    "resolved": prior is not None,
                },
            })

    keepers.sort(key=lambda item: item["roster_id"])
    return {
        "schema_version": 1,
        "source": {
            "provider": "Sleeper",
            "authoritative": True,
            "league_id": league_id,
            "draft_id": draft_id,
            "previous_draft_id": str(config["previous_draft_id"]),
        },
        "league": {
            "name": league.get("name"),
            "season": league.get("season"),
            "status": league.get("status"),
            "settings": league.get("settings"),
            "metadata": league.get("metadata"),
            "scoring_settings": league.get("scoring_settings"),
            "roster_positions": league.get("roster_positions"),
        },
        "draft": {
            "status": draft.get("status"),
            "type": draft.get("type"),
            "settings": draft.get("settings"),
            "metadata": draft.get("metadata"),
            "draft_order": draft.get("draft_order"),
            "slot_to_roster_id": draft.get("slot_to_roster_id"),
            "order_is_set": bool(draft.get("draft_order")),
            "schedule": draft_schedule(league, draft),
            "picks": current_picks,
        },
        "teams": teams,
        "confirmed_keepers": keepers,
        "confirmed_keeper_count": len(keepers),
    }


def read_json(path: Path) -> Any | None:
    if not path.exists():
        return None
    with path.open(encoding="utf-8") as handle:
        return json.load(handle)


def write_json(path: Path, value: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(value, indent=2, sort_keys=True, ensure_ascii=False) + "\n",
        encoding="utf-8",
    )


def sync(config_path: Path, output_path: Path, check_only: bool = False) -> dict[str, Any]:
    config = read_json(config_path)
    if not isinstance(config, dict):
        raise SyncError(f"Invalid configuration: {config_path}")

    league_id = str(config["league_id"])
    draft_id = str(config["draft_id"])
    previous_draft_id = str(config["previous_draft_id"])
    endpoints = {
        "league": f"/league/{league_id}",
        "users": f"/league/{league_id}/users",
        "rosters": f"/league/{league_id}/rosters",
        "draft": f"/draft/{draft_id}",
        "current_draft_picks": f"/draft/{draft_id}/picks",
        "previous_draft_picks": f"/draft/{previous_draft_id}/picks",
    }
    with ThreadPoolExecutor(max_workers=len(endpoints)) as pool:
        futures = {name: pool.submit(fetch_json, path) for name, path in endpoints.items()}
        raw = {name: future.result() for name, future in futures.items()}
    snapshot = build_snapshot(config, raw)
    fingerprint = stable_hash(snapshot)
    old = read_json(output_path)
    old_fingerprint = ((old or {}).get("sync") or {}).get("source_fingerprint")
    if old_fingerprint == fingerprint:
        print(f"GameTime data unchanged ({fingerprint[:12]})")
        return old

    now = datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")
    snapshot["sync"] = {"last_changed_at": now, "source_fingerprint": fingerprint}
    if check_only:
        print(f"GameTime data valid; changes detected ({fingerprint[:12]})")
        return snapshot

    if old:
        write_json(PREVIOUS_SNAPSHOT, old)
    write_json(output_path, snapshot)
    for name, payload in raw.items():
        write_json(RAW_DIR / f"{name}.json", payload)
    print(
        f"Wrote {output_path}: {len(snapshot['teams'])} teams, "
        f"{snapshot['confirmed_keeper_count']} confirmed keepers ({fingerprint[:12]})"
    )
    return snapshot


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--config", type=Path, default=DEFAULT_CONFIG)
    parser.add_argument("--output", type=Path, default=DEFAULT_OUTPUT)
    parser.add_argument("--check", action="store_true", help="validate without writing files")
    args = parser.parse_args()
    try:
        sync(args.config, args.output, args.check)
    except (SyncError, KeyError, TypeError, ValueError) as exc:
        print(f"GameTime sync failed: {exc}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
