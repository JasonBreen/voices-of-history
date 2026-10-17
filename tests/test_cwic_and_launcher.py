from pathlib import Path
import importlib.util
import json
import sys
import tempfile
import unittest
from unittest.mock import Mock, patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "middleware"))
import functions
import game_state
from profiles import CWIC_DEV, CWIC_GROUPS, CWIC_IDEOLOGY, model_instructions
from main import load_config

spec = importlib.util.spec_from_file_location("voices_launcher_bridge", ROOT / "Launcher-Voices.py")
bridge = importlib.util.module_from_spec(spec)
spec.loader.exec_module(bridge)


class CWICAndLauncherTests(unittest.TestCase):
    def tearDown(self):
        functions.configure_profile("vanilla")

    def test_all_cwic_political_groups_are_preserved(self):
        self.assertEqual(len(CWIC_GROUPS), 19)
        for code, label in CWIC_IDEOLOGY.items():
            state = game_state.parse_state_line(f"AIDIPLO_STATE|profile={CWIC_DEV}|tag=USA|ideo={code}")
            nation = game_state.parse_nation(f"AIDIPLO_NATION|profile={CWIC_DEV}|tag=USA|ideo={code}")
            self.assertEqual(state["ideology_name"], label)
            self.assertEqual(nation["ideology_name"], label)
        self.assertEqual(CWIC_IDEOLOGY["6"], "Progressivism")
        self.assertEqual(CWIC_IDEOLOGY["8"], "Liberalism")

    def test_cwic_equipment_mapping_does_not_use_md_names(self):
        functions.configure_profile(CWIC_DEV)
        for alias, expected in (("rifles", "infantry_equipment"),
                                ("anti_air", "auto_cannon_equipment"),
                                ("anti_tank", "direct_fire_gun_equipment"),
                                ("support", "support_equipment")):
            result = functions.build_action("send_equipment", {"equipment_type": alias, "quantity": 10})
            self.assertIn("type = " + expected, result["commands"][0])
        self.assertEqual(functions.build_action("send_equipment", {"equipment_type": "AA_Equipment"})["commands"], [])

    def test_unsupported_cwic_systems_cannot_generate_commands(self):
        functions.configure_profile(CWIC_DEV)
        for name in ("set_leader", "set_law", "regime_change", "raw_console", "raw_effect", "start_civil_war"):
            result = functions.build_action(name, {"command": "pp 1"})
            self.assertEqual(result["commands"], [])
            self.assertIsNone(result["special"])
        self.assertEqual(functions._sanitize_effect("add_ideas = war_economy"), "")

    def test_cwic_configuration_is_separate_and_dry(self):
        cwic = load_config(ROOT / "middleware/config-cwic-dev.json")
        md = load_config(ROOT / "middleware/config-md.json")
        self.assertNotEqual(cwic["paths"]["memory_db"], md["paths"]["memory_db"])
        self.assertNotEqual(cwic["paths"]["mod_localisation"], md["paths"]["mod_localisation"])
        self.assertTrue(cwic["injection"]["dry_run"])
        instructions = model_instructions(cwic)
        self.assertIn("Cold War Iron Curtain Dev Build", instructions)
        self.assertIn("EXECUTION MODE: DRY RUN", instructions)

    def test_launcher_selects_only_matching_playsets(self):
        self.assertEqual(bridge.select_profile(["mod/Cold_War_Iron_Curtain.mod", "mod/HOI4_AI_Voices_CWIC_Dev.mod"]),
                         "config-cwic-dev.json")
        self.assertEqual(bridge.select_profile(["mod/HOI4_AI_Voices_MD.mod", "mod/MD_LOCAL_STANDARD.mod"]),
                         "config-md.json")
        for mods in (["mod/Cold_War_Iron_Curtain.mod"],
                     ["mod/Cold_War_Iron_Curtain.mod", "mod/HOI4_AI_Voices_MD.mod"],
                     ["mod/MD_LOCAL_STANDARD.mod", "mod/HOI4_AI_Voices_MD.mod", "mod/Cold_War_Iron_Curtain.mod"]):
            with self.assertRaises(RuntimeError):
                bridge.select_profile(mods)

    def test_launcher_starts_visible_debug_game_and_closes_its_middleware(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            userdir = root / "userdir"
            userdir.mkdir()
            middleware_dir = root / "middleware"
            middleware_dir.mkdir()
            game_dir = root / "game"
            game_dir.mkdir()
            (game_dir / "hoi4.exe").write_bytes(b"test fixture only")
            (userdir / "dlc_load.json").write_text(json.dumps({"enabled_mods": [
                "mod/Cold_War_Iron_Curtain.mod", "mod/HOI4_AI_Voices_CWIC_Dev.mod"]}))
            (middleware_dir / "config-cwic-dev.json").write_text(json.dumps({
                "game_profile": CWIC_DEV, "injection": {"dry_run": True}}))
            (root / "launcher-bridge.json").write_text(json.dumps({
                "game_directory": str(game_dir), "exePath": "hoi4.exe", "exeArgs": ["-gdpr-compliant"]}))
            middleware = Mock(pid=1001)
            middleware.poll.return_value = None
            game = Mock(pid=1002)
            game.wait.return_value = 0
            forwarded = ["--pdx-launcher-session-token", "fake private value with spaces"]
            with patch.object(bridge, "ROOT", root), patch.object(bridge, "USERDIR", userdir), \
                 patch.object(bridge, "DATA", root / "data"), patch.object(bridge, "process_ids", return_value=[]), \
                 patch.object(bridge.time, "sleep"), patch.object(bridge, "log") as logger, \
                 patch.object(bridge.subprocess, "Popen", side_effect=[middleware, game]) as launch, \
                 patch.object(bridge.subprocess, "run") as cleanup:
                self.assertEqual(bridge.main(forwarded), 0)
            args, kwargs = launch.call_args_list[1]
            self.assertIn("-debug", args[0])
            self.assertIn("-userdir=" + str(userdir), args[0])
            self.assertIn("--userdir", launch.call_args_list[0].args[0])
            self.assertEqual(args[0][-2:], forwarded)
            self.assertEqual(kwargs["startupinfo"].wShowWindow, 1)
            self.assertEqual(kwargs["cwd"], game_dir)
            cleanup.assert_called_once()
            self.assertEqual(cleanup.call_args.args[0], ["taskkill.exe", "/PID", "1001", "/T", "/F"])
            self.assertNotIn(forwarded[1], str(logger.call_args_list))


if __name__ == "__main__":
    unittest.main()
