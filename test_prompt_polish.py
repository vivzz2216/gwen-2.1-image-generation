import json
import unittest
from unittest.mock import patch

import dolphin_prompting as dolphin
import prompt_polish as polish


class PolishTests(unittest.TestCase):
    def test_unspecified_styles_are_rejected(self):
        for style in ("watercolor", "watercolour", "photorealistic", "anime", "cartoon",
                      "digital illustration", "3D render", "oil painting", "pencil sketch",
                      "pixel art", "vector illustration", "cinematic", "gouache"):
            with self.subTest(style=style):
                self.assertTrue(polish.rewrite_errors("Two foxes under an umbrella",
                                                     f"Two foxes under an umbrella, {style}."))

    def test_explicit_style_is_kept(self):
        for style in ("watercolor", "watercolour", "anime", "photorealistic"):
            prompt = f"Two foxes under an umbrella in {style}"
            self.assertFalse(polish.rewrite_errors(prompt, prompt + "."))
            self.assertTrue(polish.rewrite_errors(prompt, "Two foxes under an umbrella."))

    def test_excluded_style_is_not_permission_to_apply_it(self):
        prompt = "Two foxes, no watercolor"
        self.assertTrue(polish.rewrite_errors(prompt, "A watercolor picture of two foxes."))
        self.assertFalse(polish.rewrite_errors(prompt, "Two foxes, without watercolor."))
        self.assertTrue(polish.rewrite_errors(prompt, "Two foxes."))

    def test_quoted_lettering_does_not_authorize_a_style(self):
        prompt = 'A sign reading "WATERCOLOR"'
        self.assertTrue(polish.rewrite_errors(prompt, 'A watercolor sign reading "WATERCOLOR".'))
        self.assertFalse(polish.rewrite_errors(prompt, prompt + "."))

    def test_neutral_grammar_does_not_choose_a_new_pose(self):
        self.assertFalse(polish.rewrite_errors("two foxes beneath an umbrella", "Two foxes are beneath an umbrella."))
        self.assertTrue(polish.rewrite_errors("two foxes beneath an umbrella", "Two foxes stand beneath an umbrella."))
        self.assertFalse(polish.rewrite_errors("two foxes standing beneath an umbrella", "Two foxes stand beneath an umbrella."))

    @patch.object(dolphin, "chat")
    def test_unrequested_watercolor_is_repaired_once(self, chat):
        chat.side_effect = [json.dumps({"rewritten_prompt": "A watercolor picture of two foxes."}),
                            json.dumps({"rewritten_prompt": "Two foxes under an umbrella."})]
        result = dolphin.enhance("Two foxes under an umbrella")
        self.assertEqual(result["enhanced"], "Two foxes under an umbrella.")
        self.assertEqual(result["model_calls"], 2)
        self.assertFalse(result["detail"]["expansion_attempted"])
        repair = json.loads(chat.call_args.args[0]["messages"][-1]["content"])
        self.assertIn("Do not introduce watercolor", repair["validation_feedback"])

    @patch.object(dolphin, "chat", return_value='{"rewritten_prompt":"A watercolor picture of two foxes."}')
    def test_repeated_style_invention_fails_without_replacing_input(self, chat):
        with self.assertRaises(dolphin.EnhancementError):
            dolphin.enhance("Two foxes")
        self.assertEqual(chat.call_count, 2)

    @patch.object(dolphin, "chat")
    def test_generate_and_edit_receive_only_polishing_instructions(self, chat):
        for task, prompt in (("generate", "A player pushes another player's chest during ice hockey"),
                             ("edit", "Change my shirt to black. Keep my face and background unchanged.")):
            with self.subTest(task=task):
                chat.return_value = json.dumps({"rewritten_prompt": prompt})
                result = dolphin.enhance(prompt, task=task, enhancement_style="polish")
                self.assertEqual(result["enhanced"], prompt)
                payload = chat.call_args.args[0]
                brief = json.loads(payload["messages"][1]["content"])
                self.assertEqual(brief["rewrite_mode"], "polish_only")
                self.assertNotIn("detail_plan", brief)
                self.assertNotIn("pose_guidance", brief)
                self.assertEqual(payload["format"]["required"], ["rewritten_prompt"])
                self.assertIn("not a", payload["messages"][0]["content"])
                self.assertEqual(result["detail"]["profile"], "qwen21-polish-v2")

    @patch.object(dolphin, "chat", return_value='{"rewritten_prompt":"An apple is falling from the sky."}')
    def test_user_apple_example_is_rewritten_without_new_content(self, chat):
        result = dolphin.enhance("an apple falling from sky", enhancement_style="polish")
        self.assertEqual(result["enhanced"], "An apple is falling from the sky.")
        brief = json.loads(chat.call_args.args[0]["messages"][1]["content"])
        self.assertIn("Actively clarify language", brief["priority"])

    @patch.object(dolphin, "chat")
    def test_action_wording_can_improve_without_adding_facts(self, chat):
        original = "a guy pushing the chest of other player while playing ice hockey"
        rewritten = "A guy pushes another player's chest while playing ice hockey."
        chat.return_value = json.dumps({"rewritten_prompt": rewritten})
        result = dolphin.enhance(original, enhancement_style="polish")
        self.assertEqual(result["enhanced"], rewritten)
        system = chat.call_args.args[0]["messages"][0]["content"]
        self.assertIn("not the original wording or errors", system)
        self.assertIn("Merely copying", system)
        self.assertNotIn("close to the original length", system)

    @patch.object(dolphin, "chat")
    def test_edit_can_be_rephrased_without_expanding_the_operations(self, chat):
        original = "chnage my shirt black dont change face or background"
        rewritten = "Change my shirt to black. Do not change my face or background."
        chat.return_value = json.dumps({"rewritten_prompt": rewritten})
        result = dolphin.enhance(original, task="edit", enhancement_style="polish")
        self.assertEqual(result["enhanced"], rewritten)
        system = chat.call_args.args[0]["messages"][0]["content"]
        self.assertIn("clear imperative sentences", system)
        self.assertIn("not the original wording or errors", system)


if __name__ == "__main__":
    unittest.main()
