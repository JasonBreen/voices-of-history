"""Offline regression coverage for opt-in boundaries and old save/memory formats."""
from pathlib import Path
import hashlib
import json
import queue
import sqlite3
import sys
import tempfile
import unittest
from unittest.mock import Mock, patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / 'middleware'))
import functions
import prompts
from injector import Injector
from main import App, load_config
from memory import Memory
from providers import configured_client, make_client
from safe_actions import ActionJournal, _integer
from unified_mod import render, configure_bases, BASES, DEPENDENCIES


def normalized(data):
    return data.replace(b'\r\n', b'\n')


class UpstreamCompatibilityTests(unittest.TestCase):
    def setUp(self):
        functions.configure_profile('vanilla')
        prompts.configure_features({})
        prompts.set_difficulty('realistic')
        prompts.set_language('')

    def tearDown(self):
        functions.configure_profile('vanilla')
        prompts.configure_features({})

    def test_original_configuration_native_assets_and_personalities_are_preserved(self):
        manifest = json.loads((ROOT / 'tests/baseline.json').read_text())
        self.assertEqual(hashlib.sha256(normalized((ROOT / 'middleware/config.json').read_bytes())).hexdigest(), manifest['config'])
        digest = hashlib.sha256()
        base = ROOT / 'mod'
        for path in sorted(base.rglob('*')):
            if path.is_file():
                digest.update(path.relative_to(base).as_posix().encode())
                digest.update(normalized(path.read_bytes()))
        self.assertEqual(digest.hexdigest(), manifest['native'])
        for name, args in manifest['prompt_arguments'].items():
            prompt = getattr(prompts, name)(*args)
            self.assertEqual(hashlib.sha256(prompt.encode()).hexdigest(), manifest['prompts'][name], name)
        before = prompts.build_leader_system('SOV', 'Soviet Union', 'Leader', 'USA')
        prompts.configure_features({'behavior': {'enhanced_personalities': True}})
        self.assertIn('NATURAL LEADER DIALOGUE', prompts.build_leader_system('SOV', 'Soviet Union', 'Leader', 'USA'))
        prompts.configure_features({})
        self.assertEqual(prompts.build_leader_system('SOV', 'Soviet Union', 'Leader', 'USA'), before)

    def test_baseline_never_creates_cloud_client_or_budget(self):
        cfg = json.loads((ROOT / 'middleware/config.json').read_text())
        with patch('providers.LLMClient') as provider, patch('usage_budget.BudgetClient') as budget:
            self.assertIs(configured_client(cfg), provider.return_value)
            budget.assert_not_called()
        with self.assertRaisesRegex(ValueError, 'Unknown provider'):
            make_client({'provider': 'claude'})

    def test_budget_is_explicit_and_unified_always_meters_requests(self):
        for cfg in ({'usage': {'enabled': True}}, {'unified': True}):
            with patch('providers.LLMClient'), patch('usage_budget.BudgetClient') as budget:
                self.assertIs(configured_client(cfg), budget.return_value)
                budget.assert_called_once()

    def test_explicit_user_directory_and_profile_localization_never_redirect(self):
        with tempfile.TemporaryDirectory() as temp:
            cfg = json.loads((ROOT / 'middleware/config-unified-cwic-dev.json').read_text())
            cfg['paths']['auto_detect'] = False
            cfg['paths']['game_log'] = str(Path(temp) / 'logs/game.log')
            cfg['paths']['mod_localisation'] = str(Path(temp) / 'missing-profile/localisation.yml')
            path = Path(temp) / 'config.json'
            path.write_text(json.dumps(cfg))
            with patch('main._autodetect') as detect:
                loaded = load_config(path)
            detect.assert_not_called()
            self.assertEqual(loaded['paths']['mod_localisation'], cfg['paths']['mod_localisation'])
            injector = Injector(loaded, logger=Mock())
            self.assertEqual(Path(injector.run_file).parent, Path(temp))

    def test_old_database_migrates_without_replaying_unidentified_history(self):
        with tempfile.TemporaryDirectory() as temp:
            path = Path(temp) / 'old.sqlite3'
            with sqlite3.connect(path) as db:
                db.execute('CREATE TABLE messages(id INTEGER PRIMARY KEY,channel TEXT,role TEXT,content TEXT,ts REAL)')
                db.execute("INSERT INTO messages VALUES(1,'leader:SOV','user','unidentified legacy row',1)")
            db.close()
            memory = Memory(path)
            memory.set_context(17, 20)
            self.assertEqual(memory.get_history('leader:SOV'), [])
            memory.add_turn('leader:SOV', 'Current question', 'Current answer', 17, 20)
            memory.set_context(17, 19)
            self.assertEqual(memory.get_history('leader:SOV'), [])
            memory.set_context(18, 20)
            self.assertEqual(memory.get_history('leader:SOV'), [])
            memory.conn.close()

    def test_legacy_plan_requires_matching_campaign_even_when_player_tag_is_same(self):
        app = App.__new__(App)
        app.gid, app._ptag = 18, 'USA'
        self.assertFalse(app._plan_is_current({'gid': 17, 'tag': 'USA'}))
        self.assertTrue(app._plan_is_current({'gid': 18, 'tag': 'USA'}))
        self.assertFalse(app._plan_is_current({'gid': 18, 'tag': 'GER'}))

    def test_future_action_results_stay_out_of_reloaded_save_context(self):
        with tempfile.TemporaryDirectory() as temp:
            journal = ActionJournal(Path(temp) / 'actions.sqlite3')
            action = {'name': 'add_political_power', 'actor': 'USA', 'amount': 1}
            journal.create(action, {'gid': 17, 'day': 20}, 'vanilla', True)
            self.assertEqual(journal.recent('vanilla', 17, day=19), [])
            self.assertEqual(len(journal.recent('vanilla', 17, day=20)), 1)

    def test_non_finite_and_boolean_amounts_are_rejected(self):
        for value in (float('nan'), float('inf'), float('-inf'), True, '5', 501):
            with self.assertRaises(ValueError):
                _integer(value, 1, 500)

    def test_disabled_pilot_never_schedules_provider_work(self):
        app = App.__new__(App)
        app.strategist = None
        app._strategy_review = Mock()
        app._strategy_tick({'live': True})
        app._strategy_review.assert_not_called()

    def test_generated_native_scripts_have_balanced_blocks_and_no_foreign_assets(self):
        import re
        for profile in ('vanilla', 'cwic_dev', 'millennium_dawn_oem', 'the_fire_rises'):
            for legacy in (False, True) if profile != 'vanilla' else (False,):
                files = render(profile, unified=not legacy)
                self.assertNotIn(b'path=', files['descriptor.mod'])
                for name, data in files.items():
                    if name.endswith(('.txt', '.gui', '.gfx', '.mod')):
                        text = data.decode('utf-8-sig')
                        tokens = re.findall(r'"(?:\\.|[^"\\])*"|#[^\n]*|[{}]', text)
                        depth = 0
                        for token in tokens:
                            if token == '{': depth += 1
                            elif token == '}': depth -= 1
                            self.assertGreaterEqual(depth, 0, (profile, legacy, name))
                        self.assertEqual(depth, 0, (profile, legacy, name))
                    if name.endswith('.tga'):
                        self.assertEqual(data, (ROOT / 'mod/HOI4_AI_Diplomacy' / name).read_bytes())

    def test_descriptor_aliases_are_explicit_and_dependency_uses_actual_name(self):
        old_bases, old_dependencies = dict(BASES), dict(DEPENDENCIES)
        self.addCleanup(lambda: (BASES.clear(), BASES.update(old_bases), DEPENDENCIES.clear(), DEPENDENCIES.update(old_dependencies)))
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            (root / 'mod').mkdir()
            (root / 'mod/custom-cwic.mod').write_text('name="My CWIC Installation"\npath="custom"\n')
            (root / 'voices-profiles.json').write_text(json.dumps({'mod/custom-cwic.mod': 'cwic_dev'}))
            configure_bases(root)
            self.assertEqual(BASES['mod/custom-cwic.mod'], 'cwic_dev')
            self.assertIn(b'My CWIC Installation', render('cwic_dev')['descriptor.mod'])

    def test_baseline_import_does_not_require_optional_chatgpt_dependencies(self):
        import subprocess
        script = """import builtins
original = builtins.__import__
def guarded(name, *args, **kwargs):
    if name in ('jwt', 'chatgpt_auth', 'chatgpt_client'):
        raise AssertionError('Optional provider imported: ' + name)
    return original(name, *args, **kwargs)
builtins.__import__ = guarded
import main
from providers import configured_client
import json
cfg=json.load(open('config.json', encoding='utf8'))
assert configured_client(cfg).__class__.__name__ == 'LLMClient'
"""
        result = subprocess.run([sys.executable, '-c', script], cwd=ROOT / 'middleware',
                                capture_output=True, text=True)
        self.assertEqual(result.returncode, 0, result.stderr)

    def test_provider_response_text_variants_and_missing_text(self):
        from llm_client import LLMClient
        self.assertEqual(LLMClient._extract_text({'choices': [{'message': {'content': 'reply'}}]}), 'reply')
        self.assertEqual(LLMClient._extract_text({'choices': [{'message': {'content': [
            {'type': 'text', 'text': 'first '}, {'type': 'text', 'text': 'second'}]}}]}), 'first second')
        self.assertEqual(LLMClient._extract_text({'response': 'Player2 reply'}), 'Player2 reply')
        for data in ({'error': 'no content'}, {'choices': []}, [], None):
            with self.assertRaises(ValueError):
                LLMClient._extract_text(data)

    def test_userdir_cli_pins_both_log_and_injection_paths(self):
        import main
        cfg = json.loads((ROOT / 'middleware/config-unified-vanilla.json').read_text())
        with tempfile.TemporaryDirectory() as temp:
            with patch.object(main, 'load_config', return_value=cfg), patch.object(main, 'App') as app, \
                 patch.object(sys, 'argv', ['main.py', '--userdir', temp]), patch('builtins.print'):
                main.main()
            actual = app.call_args.args[0]
            self.assertEqual(Path(actual['paths']['game_log']), Path(temp) / 'logs/game.log')
            self.assertEqual(actual['paths']['_hoi4_userdirs'], [str(Path(temp))])
            self.assertEqual(Path(actual['paths']['mod_localisation']),
                             Path(temp) / 'mod/HOI4_AI_Voices_Universal/localisation/english/ai_diplo_l_english.yml')

    def test_compatible_provider_sends_only_explicit_environment_credential(self):
        cfg = {'provider': 'openai_compatible', 'player2': {'base_url': 'https://provider.invalid',
               'chat_path': '/v1/chat/completions', 'health_path': '/v1/models', 'api_key_env': 'VOICES_TEST_KEY'}}
        client = make_client(cfg)
        with patch.dict('os.environ', {'VOICES_TEST_KEY': 'synthetic-test-credential'}):
            self.assertEqual(client._headers()['Authorization'], 'Bearer synthetic-test-credential')
            self.assertNotIn('player2-game-key', client._headers())


if __name__ == '__main__':
    unittest.main()
