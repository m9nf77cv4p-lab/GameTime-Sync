import importlib.util
import unittest
from pathlib import Path


MODULE_PATH = Path(__file__).resolve().parents[1] / "scripts" / "sync_gametime.py"
SPEC = importlib.util.spec_from_file_location("sync_gametime", MODULE_PATH)
sync_gametime = importlib.util.module_from_spec(SPEC)
assert SPEC.loader
SPEC.loader.exec_module(sync_gametime)


class BuildSnapshotTests(unittest.TestCase):
    def test_confirmed_keeper_uses_roster_flag_and_previous_round(self):
        config = {
            "league_id": "league",
            "draft_id": "draft",
            "previous_draft_id": "previous",
            "season": "2026",
            "expected_team_count": 1,
            "max_keepers_per_team": 1,
        }
        raw = {
            "league": {
                "league_id": "league",
                "draft_id": "draft",
                "season": "2026",
                "metadata": {"keeper_deadline": "7"},
            },
            "users": [{"user_id": "user", "display_name": "Manager", "metadata": {"team_name": "Team"}}],
            "rosters": [{"roster_id": 1, "owner_id": "user", "players": ["10"], "keepers": ["10"]}],
            "draft": {
                "draft_id": "draft",
                "draft_order": None,
                "start_time": 1787869831000,
            },
            "current_draft_picks": [],
            "previous_draft_picks": [{
                "player_id": "10",
                "round": 3,
                "pick_no": 30,
                "metadata": {
                    "first_name": "Omarion",
                    "last_name": "Hampton",
                    "position": "RB",
                    "team": "LAC",
                },
            }],
        }
        snapshot = sync_gametime.build_snapshot(config, raw)
        keeper = snapshot["confirmed_keepers"][0]
        self.assertTrue(keeper["confirmed"])
        self.assertEqual(keeper["player_name"], "Omarion Hampton")
        self.assertEqual(keeper["keeper_cost"]["round"], 3)
        self.assertFalse(snapshot["draft"]["order_is_set"])
        self.assertEqual(snapshot["draft"]["schedule"]["start_time_utc"], "2026-08-27T22:30:31Z")
        self.assertEqual(snapshot["draft"]["schedule"]["start_time_et"], "2026-08-27T18:30:31-04:00")
        self.assertEqual(snapshot["draft"]["schedule"]["keeper_deadline_days_before_draft"], 7)
        self.assertEqual(snapshot["draft"]["schedule"]["keeper_deadline_utc"], "2026-08-20T22:30:31Z")
        self.assertEqual(snapshot["draft"]["schedule"]["keeper_deadline_et"], "2026-08-20T18:30:31-04:00")

    def test_missing_keeper_deadline_does_not_invent_one(self):
        schedule = sync_gametime.draft_schedule({}, {"start_time": 1787869831000})
        self.assertIsNone(schedule["keeper_deadline_days_before_draft"])
        self.assertIsNone(schedule["keeper_deadline_utc"])
        self.assertIsNone(schedule["keeper_deadline_et"])

    def test_pre_draft_pick_is_confirmed_keeper_after_league_rollover(self):
        config = {
            "league_id": "league",
            "draft_id": "draft",
            "previous_draft_id": "previous",
            "season": "2026",
            "expected_team_count": 1,
            "max_keepers_per_team": 1,
        }
        prior_pick = {
            "player_id": "10",
            "round": 3,
            "pick_no": 30,
            "metadata": {"first_name": "Omarion", "last_name": "Hampton"},
        }
        raw = {
            "league": {"league_id": "league", "draft_id": "draft", "season": "2026"},
            "users": [{"user_id": "user", "display_name": "Manager", "metadata": {}}],
            "rosters": [{"roster_id": 1, "owner_id": "user", "players": ["10"], "keepers": []}],
            "draft": {"draft_id": "draft", "status": "pre_draft"},
            "current_draft_picks": [{"roster_id": 1, "player_id": "10"}],
            "previous_draft_picks": [prior_pick],
        }
        snapshot = sync_gametime.build_snapshot(config, raw)
        self.assertEqual(snapshot["confirmed_keeper_count"], 1)
        self.assertEqual(snapshot["teams"][0]["confirmed_keeper_ids"], ["10"])
        self.assertEqual(
            snapshot["confirmed_keepers"][0]["confirmation_source"],
            "draft.pre_draft_picks",
        )

    def test_in_progress_draft_pick_is_not_a_keeper(self):
        config = {
            "league_id": "league",
            "draft_id": "draft",
            "previous_draft_id": "previous",
            "season": "2026",
            "expected_team_count": 1,
            "max_keepers_per_team": 1,
        }
        raw = {
            "league": {"league_id": "league", "draft_id": "draft", "season": "2026"},
            "users": [{"user_id": "user", "display_name": "Manager", "metadata": {}}],
            "rosters": [{"roster_id": 1, "owner_id": "user", "players": ["10"], "keepers": []}],
            "draft": {"draft_id": "draft", "status": "drafting"},
            "current_draft_picks": [{"roster_id": 1, "player_id": "10"}],
            "previous_draft_picks": [],
        }
        snapshot = sync_gametime.build_snapshot(config, raw)
        self.assertEqual(snapshot["confirmed_keeper_count"], 0)

    def test_rejects_wrong_league(self):
        config = {
            "league_id": "right",
            "draft_id": "draft",
            "previous_draft_id": "old",
            "season": "2026",
            "expected_team_count": 0,
            "max_keepers_per_team": 1,
        }
        raw = {
            "league": {"league_id": "wrong", "draft_id": "draft", "season": "2026"},
            "users": [],
            "rosters": [],
            "draft": {"draft_id": "draft"},
            "current_draft_picks": [],
            "previous_draft_picks": [],
        }
        with self.assertRaises(sync_gametime.SyncError):
            sync_gametime.build_snapshot(config, raw)


if __name__ == "__main__":
    unittest.main()
