import json
import unittest
import urllib.error
from unittest.mock import MagicMock, patch

import dolphin_prompting as dolphin
import enhancement_detail as detail


class DetailTests(unittest.TestCase):
    def setUp(self):
        model_patch = patch.object(dolphin, "MODEL", "qwen21-dolphin")
        model_patch.start()
        self.addCleanup(model_patch.stop)

    def response(self, prompt):
        reply = MagicMock()
        reply.__enter__.return_value.read.return_value = json.dumps({"done": True, "message": {"content": json.dumps({"rewritten_prompt": prompt})}}).encode()
        return reply

    def test_generation_plan_covers_spatial_material_and_lighting_details(self):
        plan = detail.plan("A robot tending orchids", "generate")
        self.assertEqual(plan["target_words"], [230, 360])
        self.assertTrue(any("Light source" in item for item in plan["coverage"]))
        self.assertTrue(any("actor/recipient" in item for item in plan["coverage"]))

    def test_minimal_scenes_remain_simple_and_quoted_words_do_not_change_plan(self):
        self.assertEqual(detail.plan("An isolated red circle on white", "generate")["target_words"], [40, 100])
        self.assertEqual(detail.plan('A detailed city poster reading "simple logo"', "generate")["target_words"], [230, 360])

    def test_edits_do_not_invent_unseen_source_details(self):
        plan = detail.plan("Replace my jacket", "edit")
        self.assertEqual(plan["target_words"], [90, 180])
        self.assertIn("have not seen", plan["priority"])
        self.assertIn("Expand only", plan["priority"])

    def test_sections_join_into_public_prompt_in_order(self):
        parts = {key: "A copper robot." if index == 0 else "Purple orchids fill this scene." for index, key in enumerate(detail.SECTIONS["generate"])}
        result = dolphin.parsed_reply(json.dumps(parts), "A copper robot with purple orchids", "generate", "1:1", 0)
        self.assertTrue(result["rewritten_prompt"].startswith("A copper robot."))
        self.assertEqual(set(result), {"rewritten_prompt", "wh_ratio"})
        self.assertEqual(set(detail.section_schema("generate")["required"]), set(parts))

    def test_nonexistent_edit_reference_is_not_invented(self):
        content = json.dumps({"rewritten_prompt": "Replace the shirt with the black jacket shown in the reference image.", "wh_ratio": "", "ratio_follow": "<image1>"})
        with self.assertRaises(dolphin.EnhancementError):
            dolphin.validate_output(content, "Replace my shirt with a black jacket", "edit", "", 0)

    def test_explicit_source_reference_wording_is_not_blocked(self):
        prompt = "Use the source as the reference image and change my shirt to a black jacket."
        content = json.dumps({"rewritten_prompt": prompt, "wh_ratio": "", "ratio_follow": "<image1>"})
        dolphin.validate_output(content, prompt, "edit", "", 0)

    @patch("dolphin_prompting.urllib.request.urlopen")
    def test_polish_mode_sent_without_an_expansion_plan(self, urlopen):
        reply = MagicMock()
        reply.__enter__.return_value.read.return_value = json.dumps({"done": True, "message": {"content": json.dumps({"rewritten_prompt": "A copper robot tending purple orchids."})}}).encode()
        urlopen.return_value = reply
        out = dolphin.enhance("A copper robot tending purple orchids", enhancement_style="polish")
        payload = json.loads(urlopen.call_args.args[0].data)
        brief = json.loads(payload["messages"][1]["content"])
        self.assertNotIn("detail_plan", brief)
        self.assertNotIn("output_sections", brief)
        self.assertEqual(brief["rewrite_mode"], "polish_only")
        self.assertEqual(payload["options"]["temperature"], .15)
        self.assertEqual(payload["options"]["num_ctx"], 4096)
        self.assertEqual(out["attempts"], 1)
        self.assertEqual(out["detail"]["descriptive_word_count"], 6)
        self.assertEqual(out["detail"]["scope"], "text polish only; image not verified")

    @patch("dolphin_prompting.urllib.request.urlopen")
    def test_valid_polish_is_never_expanded(self, urlopen):
        draft = "A copper robot tending purple orchids."
        expanded = "A copper robot tends purple orchids inside a greenhouse. Soft daylight catches the brushed copper surface while the purple petals stand out against the muted background."
        urlopen.side_effect = [self.response(draft), self.response(expanded)]
        out = dolphin.enhance(draft)
        self.assertEqual(out["enhanced"], draft)
        self.assertFalse(out["detail"]["expansion_applied"])
        self.assertEqual(out["model_calls"], 1)
        self.assertEqual(urlopen.call_count, 1)

    @patch("dolphin_prompting.urllib.request.urlopen")
    def test_no_second_call_after_valid_polish(self, urlopen):
        draft = "A copper robot tending purple orchids."
        urlopen.side_effect = [self.response(draft), urllib.error.URLError("offline")]
        out = dolphin.enhance(draft)
        self.assertEqual(out["enhanced"], draft)
        self.assertFalse(out["detail"]["expansion_applied"])
        self.assertEqual(out["detail"]["warning"], "")
        self.assertEqual(urlopen.call_count, 1)

    @patch("dolphin_prompting.urllib.request.urlopen")
    def test_validation_repair_does_not_trigger_a_third_call(self, urlopen):
        draft = "A copper robot tending purple orchids."
        urlopen.side_effect = [self.response("Here is your prompt."), self.response(draft)]
        out = dolphin.enhance(draft)
        self.assertEqual(out["attempts"], 2)
        self.assertEqual(out["model_calls"], 2)
        self.assertFalse(out["detail"]["expansion_attempted"])


if __name__ == "__main__":
    unittest.main()
