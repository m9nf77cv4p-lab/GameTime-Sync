import copy
import sys
import subprocess
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'scripts'))
import live_draft as live


class LiveDraftTests(unittest.TestCase):
    def setUp(self):
        self.config = {'league_id': 'league', 'draft_id': 'draft', 'season': '2026'}
        self.baseline = {'source': dict(self.config), 'confirmed_keepers': [{'player_id': 'k'}]}
        self.state = {'league': dict(self.config), 'draft': {'draft_id': 'draft', 'status': 'pre_draft'},
                      'picks': [{'draft_id': 'draft', 'pick_no': 153, 'player_id': 'k'}]}
        self.t = 0
        self.outputs = []

    def sleep(self, seconds):
        self.t += seconds

    def run_live(self, fetch=None, **kwargs):
        return live.run(self.config, self.baseline, minutes=kwargs.pop('minutes', 1),
                        fetch=fetch or (lambda _: copy.deepcopy(self.state)),
                        emit=self.outputs.append, clock=lambda: self.t,
                        sleep=self.sleep, now=lambda: str(self.t), **kwargs)

    def test_preloaded_late_keeper_does_not_end_draft(self):
        self.run_live()
        self.assertEqual(self.t, 60)
        self.assertEqual(self.outputs[-1]['sync']['stop_reason'], 'duration_limit')
        self.assertEqual(self.outputs[-1]['picks'][0]['pick_no'], 153)
        self.assertEqual(len(self.outputs), 2)  # initial state and final stop

    def test_complete_is_published_without_waiting(self):
        self.state['draft']['status'] = 'complete'
        self.run_live()
        self.assertEqual(self.t, 0)
        self.assertEqual(self.outputs[-1]['sync']['stop_reason'], 'draft_complete')

    def test_changes_and_undo_are_published(self):
        def fetch(_):
            state = copy.deepcopy(self.state)
            if self.t == 15:
                state['picks'].append({'draft_id': 'draft', 'pick_no': 1, 'player_id': 'new'})
            return state
        self.run_live(fetch)
        self.assertEqual([len(x['picks']) for x in self.outputs], [1, 2, 1, 1])

    def test_unchanged_state_has_heartbeat(self):
        self.run_live(minutes=2)
        self.assertEqual([x['sync']['last_success_at'] for x in self.outputs], ['0', '60', '105'])

    def test_retries_preserve_last_success_and_stop(self):
        def fetch(_):
            if self.t == 0:
                return copy.deepcopy(self.state)
            raise live.SyncError('network unavailable')
        result = self.run_live(fetch, minutes=5)
        self.assertEqual(result, 1)
        self.assertEqual(self.outputs[-1]['sync']['stop_reason'], 'source_error')
        self.assertEqual(self.outputs[-1]['sync']['last_success_at'], '0')

    def test_invalid_sources_and_duplicate_picks_rejected(self):
        live.validate_source(self.config, self.state['league'], self.state['draft'], self.state['picks'])
        for picks in [self.state['picks'] * 2, [{'draft_id': 'wrong', 'pick_no': 1, 'player_id': 'p'}], None]:
            with self.assertRaises(live.SyncError):
                live.validate_source(self.config, self.state['league'], self.state['draft'], picks)
        with self.assertRaises(live.SyncError):
            live.validate_source(self.config, {'league_id': 'wrong'}, self.state['draft'], [])

    def test_wrong_baseline_and_invalid_limits_rejected(self):
        with self.assertRaises(ValueError):
            self.run_live(interval=1)
        with self.assertRaises(ValueError):
            self.run_live(minutes=241)
        self.baseline['source']['draft_id'] = 'wrong'
        with self.assertRaises(live.SyncError):
            self.run_live()

    def test_cancellation_and_publishing_failure(self):
        self.run_live(stopped=lambda: self.t >= 15)
        self.assertEqual(self.outputs[-1]['sync']['stop_reason'], 'cancelled')
        with self.assertRaises(OSError):
            live.run(self.config, self.baseline, fetch=lambda _: self.state,
                     emit=lambda _: (_ for _ in ()).throw(OSError('push failed')))

    def test_git_publisher_preserves_unrelated_remote_change(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            remote, working, other = root / 'remote.git', root / 'working', root / 'other'
            def git(cwd, *args):
                return subprocess.run(['git', *args], cwd=cwd, check=True,
                                      capture_output=True, text=True).stdout.strip()
            git(root, 'init', '--bare', '--initial-branch=main', str(remote))
            git(root, 'clone', str(remote), str(working))
            for repo in [working]:
                git(repo, 'config', 'user.name', 'Test')
                git(repo, 'config', 'user.email', 'test@example.com')
            (working / 'README.md').write_text('initial')
            git(working, 'add', 'README.md')
            git(working, 'commit', '-m', 'initial')
            git(working, 'push', 'origin', 'main')
            git(root, 'clone', str(remote), str(other))
            git(other, 'config', 'user.name', 'Test')
            git(other, 'config', 'user.email', 'test@example.com')
            (other / 'README.md').write_text('unrelated edit')
            git(other, 'add', 'README.md')
            git(other, 'commit', '-m', 'concurrent edit')
            git(other, 'push', 'origin', 'main')
            with patch.object(live, 'ROOT', working), patch.object(
                    live, 'OUTPUT', working / 'data' / 'gametime-live-draft.json'):
                live.publish_git({'draft': {'status': 'drafting'}})
                count = git(working, 'rev-list', '--count', 'HEAD')
                live.publish_git({'draft': {'status': 'drafting'}})
                self.assertEqual(git(working, 'rev-list', '--count', 'HEAD'), count)
            self.assertEqual((working / 'README.md').read_text(), 'unrelated edit')
            self.assertEqual(git(working, 'rev-parse', 'HEAD'),
                             git(remote, 'rev-parse', 'main'))


if __name__ == '__main__':
    unittest.main()
