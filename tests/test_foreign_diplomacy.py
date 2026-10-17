from pathlib import Path
import queue
import sys
import tempfile
import unittest
from unittest.mock import Mock

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'middleware'))
import game_state as gs
import prompts
from main import App
from memory import Memory
from overlay import filter_countries


class ForeignDiplomacyTests(unittest.TestCase):
    def setUp(self):
        self.state = {'tag': 'USA', 'name': 'United States', 'leader': 'Player President',
                      'profile': 'cwic_dev', 'date': '1949.5.25', 'gid': '17', 'day': '3'}
        self.info = {'target': 'SOV', 'player': 'USA', 'tname': 'Soviet Union',
                     'tleader': 'Old Leader'}
        self.nation = gs.parse_nation('AIDIPLO_NATION|profile=cwic_dev|tag=SOV|name=Soviet Union'
                                     '|leader=Current Leader|date=1949.5.25|ideo=2|waryou=0'
                                     '|facyou=0|subj=0|subjyou=0|nb=0|cap=0|civ=40|mil=20')

    def test_non_vanilla_political_groups_never_hide_minor_countries(self):
        world = {}
        for profile, code, tag in [('cwic_dev', '8', 'ABC'), ('cwic_dev', '19', 'DEF'),
                                   ('millennium_dawn', '3', 'GHI'),
                                   ('millennium_dawn', '5', 'JKL')]:
            world[tag] = gs.parse_nation(f'AIDIPLO_NATION|profile={profile}|tag={tag}|name={tag}|ideo={code}')
        block = gs.world_snapshot_block(world)
        for tag, nation in world.items():
            self.assertIn(tag, block)
            self.assertIn(nation['ideology_name'], block)

    def test_minor_counterpart_always_has_its_own_full_briefing(self):
        block = gs.diplomatic_context(self.state, {'SOV': self.nation}, self.info)
        self.assertIn('Your leader/representative: Current Leader', block)
        self.assertIn('Orthodox Marxist-Leninism', block)
        self.assertIn('At war with the player: No', block)
        self.assertIn('Your exported national situation:', block)
        self.assertIn('Counterpart snapshot date: 1949.5.25', block)
        self.assertIn('The human player represents: United States (USA)', block)

    def test_wrong_player_own_country_and_invalid_tags_are_rejected(self):
        for changes in ({'player': 'FRA'}, {'target': 'USA'}, {'target': 'ROOT'},
                        {'target': 'SOV\nother'}, {'player': ''}):
            with self.subTest(changes=changes), self.assertRaises(ValueError):
                gs.diplomatic_contact(dict(self.info, **changes), self.state)
        # HOI4 creates dynamic countries such as D01 during civil wars.
        self.assertEqual(gs.diplomatic_contact(dict(self.info, target='D01'), self.state)['target'], 'D01')

    def test_missing_nation_is_unknown_not_player_statistics(self):
        block = gs.diplomatic_context(self.state, {}, self.info)
        self.assertIn('At war with the player: Unknown', block)
        self.assertIn('Your ruling ideology/outlook: Unknown', block)
        self.assertIn('National situation unavailable', block)

    def test_switching_countries_keeps_history_separate(self):
        with tempfile.TemporaryDirectory() as folder:
            app = App.__new__(App)
            app.latest_state, app.world = self.state, {'SOV': self.nation}
            app.diplomatic_contacts = {}
            app.ui = Mock()
            app._park_active_event = Mock()
            app._raise = Mock()
            app._log = Mock()
            app.gid, app.max_history = 17, 20
            app.memory = Memory(str(Path(folder) / 'memory.sqlite3'))
            app.memory.set_context(17, 3)
            app.memory.add_message('leader:SOV', 'user', 'Soviet private negotiation')
            app.memory.add_message('leader:FRA', 'user', 'French private negotiation')
            app._on_open(self.info)
            app.ui.append_player.assert_called_once_with('Soviet private negotiation')
            app.ui.reset_mock()
            app._on_open({'target': 'FRA', 'player': 'USA', 'tname': 'France', 'tleader': 'French Leader'})
            app.ui.append_player.assert_called_once_with('French private negotiation')
            self.assertEqual(app.channel, 'leader:FRA')
            app.ui.reset_mock()
            app._on_open(dict(self.info, player='GER'))
            self.assertEqual(app.channel, 'leader:FRA')
            app.ui.set_header.assert_not_called()
            app.memory.conn.close()

    def test_real_turn_uses_updated_leader_and_counterpart_context(self):
        app = App.__new__(App)
        app.latest_state, app.world, app.states = self.state, {'SOV': self.nation}, {}
        app.diplomatic_contacts = {'SOV': self.info}
        app.memory = Mock()
        app.memory.get_history.return_value = []
        app.memory.facts_block.return_value = ''
        app.max_history, app.auto_exec = 20, False
        app.q = queue.Queue()
        app._lang_directive = Mock(return_value='Reply in English.')
        app.llm = Mock()
        app.llm.chat.return_value = '{"message":"We are prepared to discuss your proposal.","actions":[]}'
        app._do_llm('Greetings. Take no actions.', 'leader:SOV', 'Old Leader', 'old prompt')
        messages = app.llm.chat.call_args.args[0]
        self.assertIn('You are Current Leader', messages[0]['content'])
        self.assertIn('=== DIPLOMATIC COUNTERPART ===', messages[-1]['content'])
        self.assertIn('Orthodox Marxist-Leninism', messages[-1]['content'])
        self.assertEqual(app.memory.add_turn.call_args.args[0], 'leader:SOV')
        results = list(app.q.queue)
        self.assertFalse(any(kind == 'sysnote' for kind, _ in results), results)
        self.assertTrue(any(kind == 'assistant' and item['name'] == 'Current Leader'
                            for kind, item in results))

    def test_foreign_prompt_allows_refusal_and_respects_dry_run(self):
        prompts.configure_features({'behavior': {'enhanced_personalities': True}})
        self.addCleanup(prompts.configure_features, {})
        prompt = prompts.build_leader_system('SOV', 'Soviet Union', 'Leader', 'United States', 'USA')
        self.assertIn('You may negotiate, request concessions or decline', prompt)
        self.assertIn('In DRY RUN they are previews', prompt)
        self.assertIn('unfinished negotiations must', prompt)
        self.assertIn('ROOT refers to that human', prompt)

    def test_country_picker_filters_real_foreign_roster(self):
        world = {'SOV': self.nation, 'USA': self.state, 'ROOT': {'name': 'Invalid country'}}
        roster = gs.diplomatic_roster(self.state, world)
        self.assertEqual([item['target'] for item in roster], ['SOV'])
        self.assertEqual(filter_countries(roster, 'sov current'), roster)
        self.assertEqual(filter_countries(roster, 'Orthodox'), roster)
        self.assertEqual(filter_countries(roster, 'France'), [])

    def test_country_picker_routes_to_same_foreign_channel_and_rejects_campaign_change(self):
        app = App.__new__(App)
        app.gid, app.latest_state, app.world = 17, self.state, {'SOV': self.nation}
        app.ui, app._on_open = Mock(), Mock()
        app.diplomacy_from_ui()
        roster, selected = app.ui.choose_country.call_args.args
        selected(roster[0])
        self.assertEqual(app._on_open.call_args.args[0]['target'], 'SOV')
        self.assertEqual(app._on_open.call_args.args[0]['player'], 'USA')
        self.assertEqual(app._on_open.call_args.args[0]['source'], 'country_picker')
        app._on_open.reset_mock()
        app.gid = 18
        selected(roster[0])
        app._on_open.assert_not_called()
        self.assertIn('Campaign changed', app.ui.append_system.call_args.args[0])


if __name__ == '__main__':
    unittest.main()
