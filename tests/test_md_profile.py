from pathlib import Path
import sys
import unittest
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "middleware"))
import functions
import game_state
from main import load_config
from profiles import MD_OEM
from chatgpt_client import ChatGPTPlanClient
from test_chatgpt import FakeResponse


class MillenniumDawnProfileTests(unittest.TestCase):
    def setUp(self):
        functions.configure_profile(MD_OEM)

    def tearDown(self):
        functions.configure_profile("vanilla")

    def test_live_ideology_labels_are_profile_specific(self):
        for code, expected in (("1", "Salafist Outlook"), ("2", "Emerging Outlook"),
                               ("3", "Western Outlook"), ("4", "Non-Aligned Outlook"),
                               ("5", "Nationalist Outlook")):
            state = game_state.parse_state_line(f"AIDIPLO_STATE|profile={MD_OEM}|tag=SOV|ideo={code}")
            nation = game_state.parse_nation(f"AIDIPLO_NATION|profile={MD_OEM}|tag=SOV|ideo={code}")
            self.assertEqual(state["ideology_name"], expected)
            self.assertEqual(nation["ideology_name"], expected)
        self.assertEqual(game_state.parse_state_line("AIDIPLO_STATE|tag=SOV|ideo=2")["ideology_name"], "Communism")

    def test_unsupported_politics_and_raw_actions_have_no_commands(self):
        for name in ("regime_change", "set_leader", "start_civil_war", "set_law",
                     "raw_console", "raw_effect", "mobilize_army", "add_popularity"):
            result = functions.build_action(name, {"ideology": "communism", "command": "pp 1"})
            self.assertEqual(result["commands"], [])
            self.assertIsNone(result["special"])

    def test_rifles_use_md_definition_and_unknown_equipment_is_rejected(self):
        result = functions.build_action("send_equipment", {"equipment_type": "rifles", "quantity": 100})
        self.assertIn("type = infantry_weapons_type", result["commands"][0])
        for name in ("AA_Equipment", "L_AT_Equipment", "H_AT_Equipment"):
            result = functions.build_action("send_equipment", {"equipment_type": name, "quantity": 100})
            self.assertIn("type = " + name, result["commands"][0])
        self.assertEqual(functions.build_action("send_equipment", {"equipment_type": "fighters"})["commands"], [])
        self.assertEqual(functions.infer_equipment_type("send anti-air equipment"), "AA_Equipment")
        self.assertIsNone(functions.infer_equipment_type("send fighters"))

    def test_plan_effects_cannot_include_vanilla_laws_or_embedded_politics(self):
        self.assertEqual(functions._sanitize_effect("add_political_power = 5"), "add_political_power = 5")
        self.assertEqual(functions._sanitize_effect("add_ideas = war_economy"), "")
        self.assertEqual(functions._sanitize_effect("add_stability = 0.1 set_politics = { ruling_party = communism }"), "")
        self.assertEqual(functions._sanitize_effect("add_timed_idea = { idea = ai_diplo_fortified days = 30 }"),
                         "add_timed_idea = { idea = ai_diplo_fortified days = 30 }")

    def test_profiles_keep_their_own_localisation_and_memory(self):
        root = Path(__file__).resolve().parents[1]
        vanilla = load_config()
        md = load_config(root / "middleware/config-md-oem.json")
        self.assertNotEqual(md["paths"]["mod_localisation"], vanilla["paths"]["mod_localisation"])
        self.assertNotEqual(md["paths"]["memory_db"], vanilla["paths"]["memory_db"])
        self.assertTrue(md["injection"]["dry_run"])
        self.assertFalse(vanilla["injection"]["dry_run"])

    def test_provider_receives_md_context_and_actual_dry_run_mode(self):
        client = ChatGPTPlanClient({"game_profile": MD_OEM,
                                  "chatgpt_plan": {"model": "test-model"},
                                  "injection": {"dry_run": True}})
        client.auth = type("Auth", (), {"get_access_token": lambda self: "fake-token"})()
        lines = ['data: {"type":"response.completed","response":{"output":[]}}']
        with patch("chatgpt_client.requests.post", return_value=FakeResponse(lines=lines)) as request:
            client.chat([{"role": "user", "content": "Test"}])
        instructions = request.call_args.kwargs["json"]["instructions"]
        self.assertIn("Millennium Dawn OEM", instructions)
        self.assertIn("communism=Emerging Outlook", instructions)
        self.assertIn("EXECUTION MODE: DRY RUN", instructions)


if __name__ == "__main__":
    unittest.main()
