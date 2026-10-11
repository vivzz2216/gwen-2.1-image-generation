import json
import unittest
from unittest.mock import MagicMock, patch

import dolphin_prompting as dolphin
import prompt_intent as intent
import studio_service as service


class IntentTests(unittest.TestCase):
    def test_hockey_contact(self):
        prompt = "a guy pushing the chest of other player while playing ice hockey!"
        parsed = intent.analyze(prompt)
        self.assertEqual(parsed["body_parts"], ["chest"])
        self.assertEqual(parsed["action_clauses"][0]["actions"], ["push"])
        self.assertTrue(parsed["changes_pose"])
        self.assertIn("ice hockey", parsed["action_clauses"][0]["instruction"])

    def test_invented_behind_approach_for_chest_is_rejected(self):
        prompt = "The blue player pushes the red player's chest"
        self.assertTrue(intent.rewrite_errors(prompt, "The blue player skates up behind the player in red and pushes his chest."))
        self.assertEqual(intent.rewrite_errors(prompt + " from behind", "The blue player pushes the red player's chest from behind."), [])

    def test_kick_contact_and_resting_hand_have_action_anchors(self):
        for prompt in ("Two adult martial artists practice a kick. One man's right foot contacts the other man's chest.",
                       "A man rests his right hand on his stomach"):
            self.assertTrue(intent.analyze(prompt)["action_clauses"])
            self.assertIn("Required action", intent.anchor_rewrite(prompt, prompt))

    def test_body_regions_are_distinct(self):
        for requested, wrong in (("chest", "stomach"), ("neck", "chest"), ("stomach", "chest")):
            with self.subTest(requested=requested):
                self.assertTrue(intent.rewrite_errors(f"A man touches another man's {requested}", f"A man touches another man's {wrong}"))

    def test_abdominal_synonyms_are_allowed(self):
        self.assertEqual(intent.rewrite_errors("A man holds his stomach", "A man holds his abdomen"), [])

    def test_kick_cannot_become_push(self):
        self.assertTrue(intent.rewrite_errors("A guy kicked on the chest", "A guy is pushed on the chest"))

    def test_kick_inflections_and_passive_are_allowed(self):
        self.assertEqual(intent.rewrite_errors("A guy kicked on the chest", "A guy is being kicked in the chest"), [])

    def test_left_right_checked_in_generation_and_editing(self):
        prompt = "A man touches another player's left shoulder with his right hand"
        self.assertTrue(intent.rewrite_errors(prompt, "A man touches another player's right shoulder with his left hand"))
        self.assertEqual(intent.rewrite_errors(prompt, prompt), [])

    def test_directions_not_inferred_for_unspecified_limbs(self):
        self.assertEqual(intent.analyze("A man kicks another man's chest")["directions"], [])

    def test_descriptive_modifiers_do_not_erase_the_requested_side(self):
        prompt = "A man touches his chest with his right hand"
        self.assertEqual(intent.rewrite_errors(prompt, "A man touches his chest with his right gloved hand"), [])
        self.assertTrue(intent.rewrite_errors(prompt, "A man touches his chest with his left gloved hand"))
        self.assertIn({"side": "right", "part": "hand"}, intent.analyze("His right gloved hand touches his chest")["directions"])

    def test_literal_text_is_not_an_action(self):
        prompt = 'A poster reading "kick his chest"'
        self.assertEqual(intent.analyze(prompt)["action_clauses"], [])
        self.assertEqual(intent.analyze(prompt)["body_parts"], [])

    def test_object_senses_and_hyphenated_necklines_are_not_anatomy(self):
        for prompt in ("A treasure chest", "A chest of drawers", "Change the neck of the bottle", "A V-neck shirt", "Back to the beach", "The back of a book"):
            with self.subTest(prompt=prompt):
                self.assertEqual(intent.analyze(prompt)["body_parts"], [])

    def test_multiple_actions_are_retained(self):
        self.assertTrue(intent.rewrite_errors("Raise my left arm and bend my right knee", "Raise my left arm with my right knee visible"))

    def test_negative_action_cannot_turn_positive(self):
        self.assertTrue(intent.rewrite_errors("Do not kick the other player", "Kick the other player"))
        self.assertEqual(intent.rewrite_errors("Do not kick the other player", "Do not kick the other player"), [])
        self.assertNotIn("moment of contact", intent.guidance("Do not kick the other player"))

    def test_contact_with_an_object_does_not_invent_a_human_recipient(self):
        notes = intent.guidance("A man kicks a football")
        self.assertNotIn("person's specified body region", notes)
        self.assertIn("who or what", notes)

    def test_negative_target_mention_is_not_positive_fidelity(self):
        self.assertTrue(intent.rewrite_errors("Kick his chest", "Kick his stomach, not his chest"))

    def test_preserving_a_body_part_does_not_exclude_it(self):
        for original in ("Keep my face unchanged", "dont change my face", "don't change my face"):
            with self.subTest(original=original):
                self.assertEqual(intent.rewrite_errors(original, "Do not change my face."), [])
        self.assertTrue(intent.rewrite_errors("Show my face", "Without my face."))

    def test_apostrophe_correction_keeps_action_negative(self):
        self.assertEqual(intent.rewrite_errors("dont kick the other player", "Do not kick the other player."), [])
        self.assertTrue(intent.rewrite_errors("dont kick the other player", "Kick the other player."))

    def test_preservation_and_clothing_are_not_pose_requests(self):
        for prompt in ("Keep the man standing", "Change my lower clothes", "Turn my hair blue", "Change my necklace"):
            with self.subTest(prompt=prompt):
                self.assertFalse(intent.analyze(prompt)["changes_pose"])

    def test_preservation_then_requested_motion(self):
        self.assertTrue(intent.analyze("Keep my shirt and raise my left arm")["changes_pose"])

    def test_actor_relationship_wording_is_anchored(self):
        prompt = "The red player pushes the blue player's chest"
        out = intent.anchor_rewrite(prompt, "Two hockey players interact.")
        self.assertIn(prompt, out)
        self.assertIn("Do not exchange", out)

    def test_compile_pose_change_has_no_pose_or_hand_lock(self):
        out = service.compile_prompt("Raise my left arm", target="global")
        self.assertNotIn("pose and body proportions", out)
        self.assertNotIn("hand positions", out)
        self.assertIn("identity", out)

    def test_compile_without_enhancer_adds_anatomy_guidance(self):
        out = service.compile_prompt("A guy kicks another guy's chest", preserve=False)
        self.assertIn("front upper torso", out)
        self.assertIn("moment of contact", out)

    def test_ordinary_prompts_are_unchanged(self):
        self.assertEqual(service.compile_prompt("A watercolor boat", preserve=False), "A watercolor boat")
        self.assertEqual(intent.anchor_rewrite("Change my shirt", "Replace the shirt."), "Replace the shirt.")

    @patch("dolphin_prompting.urllib.request.urlopen")
    def test_wrong_body_part_is_repaired_without_appending_action_notes(self, urlopen):
        def response(text):
            mock = MagicMock()
            mock.__enter__.return_value.read.return_value = json.dumps({"done": True, "done_reason": "stop", "message": {"content": json.dumps({"rewritten_prompt": text})}}).encode()
            return mock
        urlopen.side_effect = [response("A player pushes the other player's stomach on the ice."),
                               response("A player pushes the other player's chest while playing ice hockey.")]
        prompt = "A guy pushes the other player's chest while playing ice hockey"
        out = dolphin.enhance(prompt)
        self.assertEqual(out["attempts"], 2)
        self.assertEqual(out["original"], prompt)
        self.assertEqual(out["enhanced"], "A player pushes the other player's chest while playing ice hockey.")
        self.assertFalse(out["image_understanding"])
        self.assertIn("image not checked", out["intent"]["validation_scope"])
        payload = json.loads(urlopen.call_args_list[0].args[0].data)
        brief = json.loads(payload["messages"][-1]["content"])
        self.assertIn("required_intent", brief)

    @patch("dolphin_prompting.urllib.request.urlopen")
    def test_repeat_failed_body_part_repair_does_not_succeed(self, urlopen):
        mock = MagicMock()
        mock.__enter__.return_value.read.return_value = json.dumps({"done": True, "message": {"content": '{"rewritten_prompt":"A man kicks another man in the stomach."}'}}).encode()
        urlopen.return_value = mock
        with self.assertRaises(dolphin.EnhancementError):
            dolphin.enhance("A man kicks another man's chest")
        self.assertEqual(urlopen.call_count, 2)


if __name__ == "__main__":
    unittest.main()
