import json
import unittest
import urllib.error
from unittest.mock import MagicMock, patch

from fastapi.testclient import TestClient
import dolphin_prompting as dolphin
import studio_service as service


def reply(prompt="A watercolor illustration of two foxes.", ratio="3:2", task="generate", **extra):
    value = {"rewritten_prompt": prompt, "wh_ratio": ratio}
    if task == "edit":
        value.update(wh_ratio="", ratio_follow="<image1>")
    return {"done": True, "done_reason": "stop", "message": {"content": json.dumps(value)}, **extra}


class DolphinPromptTests(unittest.TestCase):
    def setUp(self):
        model_patch = patch.object(dolphin, "MODEL", "qwen21-dolphin")
        model_patch.start()
        self.addCleanup(model_patch.stop)

    def response(self, value):
        context = MagicMock()
        context.__enter__.return_value.read.return_value = json.dumps(value).encode()
        return context

    @patch("dolphin_prompting.urllib.request.urlopen")
    def test_polishes_without_inventing_a_style(self, urlopen):
        urlopen.return_value = self.response(reply("Two foxes beneath an umbrella.", "1:1"))
        result = dolphin.enhance("Two foxes beneath an umbrella", enhancement_style="polish")
        self.assertEqual(result["attempts"], 1)
        self.assertEqual(result["enhanced"], "Two foxes beneath an umbrella.")
        self.assertEqual(result["profile"], "qwen21-actions-v2")
        self.assertNotIn("fidelity", result)
        payload = json.loads(urlopen.call_args.args[0].data)
        self.assertEqual(payload["options"]["temperature"], .15)
        brief = json.loads(payload["messages"][1]["content"])
        self.assertNotIn("fixed_requirements", brief)
        self.assertNotIn("fidelity_protocol", brief)
        self.assertEqual(brief["rewrite_mode"], "polish_only")

    def test_unconventional_dark_scene_is_not_keyword_blocked(self):
        prompt = "A surreal horror castle floating upside down beneath a black sun."
        out = dolphin.validate_output(reply(prompt, "1:1")["message"]["content"], prompt, "generate", "1:1")
        self.assertEqual(out["rewritten_prompt"], prompt)

    @patch("dolphin_prompting.urllib.request.urlopen")
    def test_explicit_ratio_and_message_roles_and_memory_release(self, urlopen):
        urlopen.return_value = self.response(reply())
        result = dolphin.enhance("Two foxes in watercolor, 3:2", aspect_ratio="1:1")
        self.assertEqual(result["wh_ratio"], "3:2")
        request = urlopen.call_args.args[0]
        payload = json.loads(request.data)
        self.assertEqual(request.full_url, dolphin.OLLAMA_URL + "/api/chat")
        self.assertEqual([m["role"] for m in payload["messages"]], ["system", "user", "assistant", "user"])
        self.assertIn("Two foxes in watercolor", payload["messages"][-1]["content"])
        self.assertEqual(payload["keep_alive"], 0)
        self.assertFalse(result["image_understanding"])

    def test_literal_text_and_significant_spacing_survive(self):
        prompt = 'A sign reading "HELLO  WORLD!"'
        value = json.dumps({"rewritten_prompt": 'A painted sign reads "HELLO  WORLD!".\nA cream background surrounds it.', "wh_ratio": "1:1"})
        out = dolphin.validate_output(value, prompt, "generate", "1:1")
        self.assertIn("HELLO  WORLD!", out["rewritten_prompt"])
        self.assertNotIn("\n", out["rewritten_prompt"])

    def test_changed_literal_text_and_ratio_are_rejected(self):
        with self.assertRaises(dolphin.EnhancementError):
            dolphin.validate_output(json.dumps({"rewritten_prompt": 'A sign reads "HELLO".', "wh_ratio": "1:1"}), 'A sign reads "HELLO WORLD!"', "generate", "1:1")
        with self.assertRaises(dolphin.EnhancementError):
            dolphin.validate_output(json.dumps({"rewritten_prompt": "Two cats.", "wh_ratio": "16:9"}), "Two cats.", "generate", "1:1")

    def test_invalid_commentary_oversized_and_incomplete_output_rejected(self):
        for value in ("not json", "{}", json.dumps({"rewritten_prompt": "", "wh_ratio": "1:1"}),
                      json.dumps({"rewritten_prompt": "Here is your image prompt.", "wh_ratio": "1:1"}),
                      json.dumps({"rewritten_prompt": "x" * 4001, "wh_ratio": "1:1"})):
            with self.subTest(value=value[:50]), self.assertRaises(dolphin.EnhancementError):
                dolphin.validate_output(value, "Two cats.", "generate", "1:1")

    @patch("dolphin_prompting.urllib.request.urlopen", side_effect=urllib.error.URLError("offline"))
    def test_offline_has_no_fake_template_fallback(self, _urlopen):
        with self.assertRaises(dolphin.EnhancementError) as caught:
            dolphin.enhance("A blue bird.")
        self.assertEqual(caught.exception.status, 503)

    @patch("dolphin_prompting.urllib.request.urlopen")
    def test_cut_short_output_is_not_used(self, urlopen):
        urlopen.return_value = self.response(reply(done_reason="length"))
        with self.assertRaises(dolphin.EnhancementError):
            dolphin.enhance("Two foxes.", aspect_ratio="3:2")

    def test_edit_canvas_settings_cannot_drift(self):
        value = reply("Raise the person's left arm.", task="edit")["message"]["content"]
        self.assertEqual(dolphin.validate_output(value, "Raise my left arm", "edit", "")["ratio_follow"], "<image1>")
        with self.assertRaises(dolphin.EnhancementError):
            dolphin.validate_output(reply()["message"]["content"], "Raise my left arm", "edit", "")

    def test_size_metadata_in_visual_prose_is_rejected_but_literal_is_kept(self):
        with self.assertRaises(dolphin.EnhancementError):
            dolphin.validate_output(reply("A poster on a 3:2 canvas.")["message"]["content"], "A poster", "generate", "3:2")
        prompt = 'A label reading "16:9"'
        self.assertEqual(dolphin.selected_ratio(prompt, "1:1"), "1:1")
        dolphin.validate_output(reply(prompt, "1:1")["message"]["content"], prompt, "generate", "1:1")

    def test_no_text_exclusion_cannot_be_dropped(self):
        result = dolphin.validate_output(reply("Two foxes.")["message"]["content"], "Two foxes, no text", "generate", "3:2")
        self.assertIn("no readable text", result["rewritten_prompt"])
        dolphin.validate_output(reply("Two foxes, without any text.")["message"]["content"], "Two foxes, no text", "generate", "3:2")

    def test_non_english_output_is_rejected_and_translated_input_is_allowed(self):
        non_english = chr(0x0928)
        with self.assertRaises(dolphin.EnhancementError):
            dolphin.validate_output(reply("A sign reads " + non_english)["message"]["content"], "A sign", "generate", "3:2")
        translated = 'A sign reads "Hello!".'
        dolphin.validate_output(reply(translated)["message"]["content"], 'A sign reads "' + non_english + '"', "generate", "3:2")

    def test_numbered_reference_roles_cannot_be_replaced_with_vague_words(self):
        with self.assertRaises(dolphin.EnhancementError):
            dolphin.validate_output(reply("Replace the clothes using the reference image.", task="edit")["message"]["content"], "Replace my clothes", "edit", "", reference_count=1)
        dolphin.validate_output(reply("Replace the clothes in <image1> using the suit from <image2>.", task="edit")["message"]["content"], "Replace my clothes", "edit", "", reference_count=1)

    @patch("dolphin_prompting.urllib.request.urlopen")
    def test_missing_smile_is_repaired_once(self, urlopen):
        urlopen.side_effect = [self.response(reply("Raise the left arm while maintaining the current expression.", task="edit")),
                               self.response(reply("Raise the person's left arm and give them a clear smile. Keep unrelated details unchanged.", task="edit"))]
        result = dolphin.enhance("Raise my left arm and make me smile", task="edit")
        self.assertEqual(result["attempts"], 2)
        self.assertIn("smile", result["enhanced"])
        self.assertEqual(urlopen.call_count, 2)

    @patch("dolphin_prompting.urllib.request.urlopen")
    def test_repeat_failed_repair_returns_error(self, urlopen):
        urlopen.return_value = self.response(reply("Raise the left arm while maintaining the current expression.", task="edit"))
        with self.assertRaises(dolphin.EnhancementError):
            dolphin.enhance("Raise my left arm and make me smile", task="edit")
        self.assertEqual(urlopen.call_count, 2)


class EnhanceAPITests(unittest.TestCase):
    def setUp(self):
        self.client = TestClient(service.app)

    def test_generation_compilation_does_not_add_strict_surface_rules(self):
        self.assertEqual(service.compile_prompt("A surreal castle, no text", preserve=False), "A surreal castle, no text")
        self.assertEqual(service.compile_prompt("A surreal castle", preserve=False), "A surreal castle")

    def test_invalid_request_and_empty_prompt_rejected(self):
        for data in ({"prompt": ""}, {"prompt": "   "}, {"prompt": "x" * 4001},
                     {"prompt": "cat", "task": "invalid"}, {"prompt": "cat", "reference_count": 3}):
            with self.subTest(data=str(data)[:50]):
                self.assertEqual(self.client.post("/api/enhance", json=data).status_code, 422)

    @patch.object(service, "release_qwen_for_enhancer")
    @patch.object(service.CLIENT, "request", return_value={"queue_running": [], "queue_pending": []})
    @patch.object(dolphin, "enhance", return_value={"enhanced": "Change only the hair to auburn."})
    def test_edit_context_forwarded_and_resource_lock_released(self, enhance, _queue, release):
        response = self.client.post("/api/enhance", json={"prompt": "Make my hair auburn", "task": "edit", "region": "hair", "reference_count": 1})
        self.assertEqual(response.status_code, 200)
        self.assertEqual(enhance.call_args.kwargs["task"], "edit")
        self.assertEqual(enhance.call_args.kwargs["reference_count"], 1)
        release.assert_called_once()
        self.assertFalse(service.RESOURCE_LOCK.locked())

    @patch.object(service.CLIENT, "request", return_value={"queue_running": [[1]], "queue_pending": []})
    @patch.object(dolphin, "enhance")
    def test_busy_external_comfy_job_is_not_interrupted(self, enhance, _queue):
        self.assertEqual(self.client.post("/api/enhance", json={"prompt": "cat"}).status_code, 409)
        enhance.assert_not_called()
        self.assertFalse(service.RESOURCE_LOCK.locked())

    def test_busy_studio_does_not_load_dolphin(self):
        service.RESOURCE_LOCK.acquire()
        try:
            self.assertEqual(self.client.post("/api/enhance", json={"prompt": "cat"}).status_code, 409)
        finally:
            service.RESOURCE_LOCK.release()

    @patch.object(service.CLIENT, "request", side_effect=service.ImageGenerationError("offline"))
    @patch.object(dolphin, "enhance", side_effect=dolphin.EnhancementError("offline", 503))
    def test_failure_returns_an_error_and_releases_lock(self, _enhance, _queue):
        self.assertEqual(self.client.post("/api/enhance", json={"prompt": "cat"}).status_code, 503)
        self.assertFalse(service.RESOURCE_LOCK.locked())

    def test_compiled_edit_does_not_forbid_requested_pose_change(self):
        prompt = service.compile_prompt("Raise my left arm", "manual")
        self.assertIn("Except for attributes explicitly changed", prompt)
        self.assertNotIn("Do not beautify the face, reposition", prompt)
        multi = service.compile_prompt("Transfer the clothes", "outfit", reference=2)
        self.assertIn("<image1>", multi)
        self.assertIn("<image2>", multi)
        self.assertIn("<image3>", multi)

    @patch.object(service, "generate_live_ai_image", return_value="/outputs/qwen_test.png")
    @patch.object(service, "enhance_for_studio", return_value={"enhanced": "A watercolor boat on a quiet lake.", "wh_ratio": "2:3"})
    def test_legacy_generate_enhance_flag_uses_rewritten_text_and_size(self, enhance, generate):
        result = self.client.post("/api/generate", json={"prompt": "boat", "enhance_prompt": True})
        self.assertEqual(result.status_code, 200)
        enhance.assert_called_once()
        self.assertEqual(generate.call_args.args[:2], ("A watercolor boat on a quiet lake.", "2:3"))
        self.assertEqual(result.json()["aspect_ratio"], "2:3")


if __name__ == "__main__":
    unittest.main()
