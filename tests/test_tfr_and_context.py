from pathlib import Path
import importlib.util
import json
import queue
import re
import sys
import unittest
from unittest.mock import Mock, patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "middleware"))
import functions
import game_state
from chatgpt_client import ChatGPTPlanClient
from main import App, load_config
from memory import Memory
from unified_mod import render
from profiles import TFR, TFR_GROUPS, TFR_IDEOLOGY, model_instructions
from test_chatgpt import FakeResponse

spec = importlib.util.spec_from_file_location("voices_tfr_launcher", ROOT / "Launcher-Voices.py")
bridge = importlib.util.module_from_spec(spec)
spec.loader.exec_module(bridge)


class TFRTests(unittest.TestCase):
    def setUp(self):
        functions.configure_profile(TFR)

    def tearDown(self):
        functions.configure_profile("vanilla")

    def test_all_tfr_groups_survive_export_and_parse(self):
        export = render(TFR, unified=False)["common/scripted_effects/ai_diplo_export.txt"].decode("utf-8-sig")
        self.assertEqual(len(TFR_GROUPS), 11)
        for variable in ("aid_ideo", "n_ideo"):
            exported = re.findall(r"has_government = (\w+) \} set_temp_variable = \{ " + variable + r" = (\d+)", export)
            self.assertEqual(exported, [(name, str(i)) for i, (name, _) in enumerate(TFR_GROUPS, 1)])
        for code, label in TFR_IDEOLOGY.items():
            state = game_state.parse_state_line(f"AIDIPLO_STATE|profile={TFR}|tag=USA|ideo={code}")
            nation = game_state.parse_nation(f"AIDIPLO_NATION|profile={TFR}|tag=D01|name=Test successor|ideo={code}")
            self.assertEqual(state["ideology_name"], label)
            self.assertEqual(nation["ideology_name"], label)
            self.assertIn("Test successor (D01)", game_state.world_snapshot_block({"D01": nation}))
        self.assertEqual(TFR_IDEOLOGY["9"], "Despotic")
        self.assertNotIn("profile=cwic_dev", export)

    def test_tfr_equipment_does_not_fall_through_to_cwic_or_md(self):
        for alias, expected in (("rifles", "infantry_equipment"), ("support", "support_equipment"),
                                ("anti_air", "anti_air_equipment"), ("anti_tank", "anti_tank_equipment"),
                                ("artillery", "artillery_equipment"), ("trucks", "motorized_equipment"),
                                ("convoys", "convoy")):
            result = functions.build_action("send_equipment", {"equipment_type": alias, "quantity": 10})
            self.assertIn("type = " + expected, result["commands"][0])
        for unsupported in ("AA_Equipment", "auto_cannon_equipment", "direct_fire_gun_equipment", "nuclear_missile"):
            self.assertEqual(functions.build_action("send_equipment", {"equipment_type": unsupported})["commands"], [])

    def test_tfr_custom_systems_and_escapes_are_rejected(self):
        for name in ("set_leader", "set_law", "regime_change", "raw_console", "raw_effect",
                     "start_civil_war", "change_budget", "trigger_election", "advance_gpu_race"):
            result = functions.build_action(name, {"command": "pp 1"})
            self.assertEqual(result["commands"], [])
            self.assertIsNone(result["special"])
            self.assertIn("The Fire Rises", result["desc"])
        for effect in ("set_variable = { TFR_budget = 500 }", "country_event = { id = TFR.1 }",
                       "set_politics = { ruling_party = fascist }", "add_ideas = war_economy"):
            self.assertEqual(functions._sanitize_effect(effect), "")
        self.assertEqual(functions._sanitize_effect("add_political_power = 1"), "add_political_power = 1")

    def test_tfr_launcher_and_memory_are_separate_and_dry(self):
        cfg = load_config(ROOT / "middleware/config-tfr.json")
        self.assertEqual(cfg["game_profile"], TFR)
        self.assertTrue(cfg["injection"]["dry_run"])
        self.assertEqual(cfg["provider"], "player2")
        self.assertIn("THE FIRE RISES ACTION CATALOG", functions.catalog_text())
        self.assertIn("EXECUTION MODE: DRY RUN", model_instructions(cfg))
        for other in ("config.json", "config-md-oem.json", "config-cwic-dev.json"):
            other_cfg = load_config(ROOT / "middleware" / other)
            self.assertNotEqual(cfg["paths"]["memory_db"], other_cfg["paths"]["memory_db"])
            self.assertNotEqual(cfg["paths"]["mod_localisation"], other_cfg["paths"]["mod_localisation"])
        pair = ["mod/ugc_3350890356.mod", "mod/HOI4_AI_Voices_TFR.mod"]
        self.assertEqual(bridge.select_profile(pair), "config-tfr.json")
        self.assertEqual(bridge.select_profile(pair[::-1]), "config-tfr.json")
        for invalid in (pair[:1], pair + ["mod/extra.mod"], pair + pair, [pair[0], "mod/HOI4_AI_Voices_MD_OEM.mod"]):
            with self.assertRaises(RuntimeError):
                bridge.select_profile(invalid)

    def test_tfr_rival_event_preserves_save_identity_without_vanilla_bonuses(self):
        addon = render(TFR, unified=False)
        events = addon["events/ai_diplo_events.txt"].decode("utf-8-sig").replace("\r\n", "\n")
        event = re.search(r"country_event = \{\n\tid = aidiplo\.50\n[\s\S]*?\n\}", events).group()
        self.assertIn("immediate = { }", event)
        self.assertNotIn("add_political_power", event)
        self.assertNotIn("has_government", event)
        actions = addon["common/on_actions/zzz_ai_diplo_on_actions.txt"].decode("utf-8-sig")
        self.assertNotIn("id = aidiplo.50", actions)


class ContextIsolationTests(unittest.TestCase):
    def setUp(self):
        self.memory = Memory(":memory:")

    def tearDown(self):
        self.memory.conn.close()

    def test_history_is_limited_to_campaign_channel_and_current_day(self):
        m = self.memory
        m.set_context(100, 2)
        for i in range(55):
            m.add_message("leader:CAN", "user", f"canada-message-{i}")
        m.add_message("leader:SOV", "user", "unrelated-country-message")
        m.set_context(101, 2)
        m.add_message("leader:CAN", "user", "other-campaign-message")
        m.set_context(100, 3)
        m.add_message("leader:CAN", "user", "future-save-message")
        m.set_context(100, 2)
        self.assertEqual([row["content"] for row in m.get_history("leader:CAN")],
                         [f"canada-message-{i}" for i in range(15, 55)])

    def test_facts_are_limited_to_25_for_this_campaign_and_day(self):
        m = self.memory
        m.set_context(100, 2)
        for i in range(30):
            m.remember_fact("world", f"campaign-fact-{i:02}")
        m.set_context(101, 2)
        m.remember_fact("world", "another-campaign-fact")
        m.set_context(100, 3)
        m.remember_fact("world", "future-fact")
        m.set_context(100, 2)
        facts = m.facts_block()
        self.assertEqual(re.findall(r"campaign-fact-\d+", facts), [f"campaign-fact-{i:02}" for i in range(5, 30)])
        self.assertNotIn("another-campaign-fact", facts)
        self.assertNotIn("future-fact", facts)

    def test_request_contains_selected_history_and_game_context_without_chatgpt_history_link(self):
        m = self.memory
        m.set_context(100, 2)
        m.add_message("staff", "user", "selected-voices-history")
        m.add_message("leader:CAN", "user", "other-channel-history")
        m.remember_fact("world", "saved-campaign-fact")
        app = App.__new__(App)
        app.memory = m
        app.max_history = 40
        app.latest_state = game_state.parse_state_line(
            f"AIDIPLO_STATE|profile={TFR}|tag=USA|name=United States|leader=Campaign Representative|date=2020.1.3|ideo=7")
        app.world = {}
        app.states = {}
        app.q = queue.Queue()
        app.auto_exec = False
        app._lang_directive = Mock(return_value="Reply in English.")
        cfg = {"game_profile": TFR, "chatgpt_plan": {"model": "gpt-test"}, "injection": {"dry_run": True}}
        app.llm = ChatGPTPlanClient(cfg)
        app.llm.auth = Mock()
        app.llm.auth.get_access_token.return_value = "fake-token"
        raw = json.dumps({"message": "Report only.", "actions": []})
        lines = ["data: " + json.dumps({"type": "response.output_text.delta", "delta": raw}),
                 'data: {"type":"response.completed","response":{}}']
        with patch("chatgpt_client.requests.post", return_value=FakeResponse(lines=lines)) as request:
            app._do_llm("Current test message; take no actions.", "staff", "Staff", "Voices roleplay instructions")
        request.assert_called_once()
        body = request.call_args.kwargs["json"]
        self.assertEqual(body["input"][0], {"role": "user", "content": "selected-voices-history"})
        prompt = body["input"][-1]["content"]
        for expected in ("Current test message", "saved-campaign-fact", "Campaign Representative", "Conservative", "2020.1.3"):
            self.assertIn(expected, prompt)
        self.assertNotIn("other-channel-history", json.dumps(body))
        self.assertIn("EXECUTION MODE: DRY RUN", body["instructions"])
        self.assertEqual(set(body), {"model", "input", "instructions", "store", "stream"})
        self.assertFalse(body["store"])
        self.assertFalse(any(event[0] == "sysnote" for event in list(app.q.queue)))


if __name__ == "__main__":
    unittest.main()
