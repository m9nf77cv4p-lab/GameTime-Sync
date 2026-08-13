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
            "league": {"league_id": "league", "draft_id": "draft", "season": "2026"},
            "users": [{"user_id": "user", "display_name": "Manager", "metadata": {"team_name": "Team"}}],
            "rosters": [{"roster_id": 1, "owner_id": "user", "players": ["10"], "keepers": ["10"]}],
            "draft": {"draft_id": "draft", "draft_order": None},
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
