import json
import unittest
from unittest.mock import MagicMock, patch

import dolphin_prompting as dolphin
import pose_prompting as pose
import studio_service as service
from fastapi.testclient import TestClient


class PoseTests(unittest.TestCase):
    def test_interacting_people_get_limb_ownership_on_clean_and_normal_paths(self):
        prompt = "Two adult ice hockey players. The blue player pushes the red player's chest with his right hand."
        for clean in (True, False):
            result = service.compile_prompt(prompt, preserve=False, clean_prompt=clean)
            self.assertTrue(result.startswith(prompt))
            self.assertIn(pose.MARKER, result)
            self.assertIn("acting limb attached to its owner", result)
            self.assertIn("one instant", result)

    def test_static_multi_person_scene_is_supported(self):
        self.assertIn(pose.MARKER, pose.apply("Two women beside a man in a park."))
        self.assertIn(pose.MARKER, pose.apply("Three dancers holding hands in a circle."))

    def test_single_complex_pose_supported(self):
        self.assertIn(pose.MARKER, pose.apply("A man crouching with his hands on his knees."))
        self.assertIn(pose.MARKER, pose.apply("Raise my left arm"))

    def test_clothing_edits_and_unrelated_objects_unchanged(self):
        for prompt in ("Change my shirt to blue", "Add a necklace", "A boat on a lake", "A clock with three hands", 'A poster reading "Two people shaking hands"'):
            with self.subTest(prompt=prompt):
                self.assertEqual(pose.apply(prompt), prompt)
        self.assertNotIn(pose.MARKER, service.compile_prompt("Change the shirts of two men to blue", preserve=True))

    def test_explicit_unusual_anatomy_not_normalized(self):
        for prompt in ("A man with four arms", "A multi-armed dancer", "An alien with three legs", "A woman in an impossible surreal pose"):
            with self.subTest(prompt=prompt):
                self.assertEqual(pose.apply(prompt), prompt)

    def test_sequence_not_forced_into_single_instant(self):
        prompt = "Three storyboard panels show a man raising his arm then lowering it."
        self.assertNotIn("one instant", pose.apply(prompt))
        self.assertTrue(pose.apply(prompt).startswith(prompt))

    def test_occlusion_and_disability_not_overridden(self):
        prompt = "Two men shaking hands, one has a prosthetic leg; their lower legs are cropped out."
        result = pose.apply(prompt)
        self.assertIn("Respect explicitly requested missing limbs, prostheses, cropping and occlusion", result)
        self.assertNotIn("two arms", result)

    def test_idempotent_and_switchable(self):
        prompt = "Two players shaking hands"
        once = pose.apply(prompt)
        self.assertEqual(pose.apply(once), once)
        with patch.dict("os.environ", {"QWEN_POSE_GUIDANCE": "0"}):
            self.assertEqual(pose.apply(prompt), prompt)

    @patch.object(service.CLIENT, "request", side_effect=service.engine.ImageGenerationError("offline"))
    @patch("studio_service.urllib.request.urlopen", side_effect=OSError("offline"))
    def test_capabilities_reports_soft_guidance(self, _urlopen, _client):
        result = TestClient(service.app).get("/api/capabilities")
        self.assertEqual(result.status_code, 200)
        self.assertTrue(result.json()["pose_guidance"]["enabled"])
        self.assertIn("not verified", result.json()["pose_guidance"]["scope"])

    @patch("studio_service._run_comfy_workflow", return_value="/outputs/test.png")
    def test_legacy_api_passes_guidance_to_actual_encoder_prompt(self, run):
        response = TestClient(service.app).post("/api/generate", json={"prompt": "Two men shaking hands", "lora_enabled": False})
        self.assertEqual(response.status_code, 200)
        workflow = run.call_args.args[0]
        self.assertIn(pose.MARKER, workflow["5"]["inputs"]["prompt"])
        self.assertEqual(workflow["7"]["inputs"]["cfg"], 1.0)

    @patch("dolphin_prompting.urllib.request.urlopen")
    def test_optional_enhancer_does_not_add_pose_notes(self, urlopen):
        prompt = "Two men shaking hands"
        response = MagicMock()
        response.__enter__.return_value.read.return_value = json.dumps({"done": True, "message": {"content": json.dumps({"rewritten_prompt": prompt})}}).encode()
        urlopen.return_value = response
        result = dolphin.enhance(prompt)
        payload = json.loads(urlopen.call_args.args[0].data)
        brief = json.loads(payload["messages"][1]["content"])
        self.assertNotIn("pose_guidance", brief)
        self.assertNotIn(pose.MARKER, result["enhanced"])
        self.assertEqual(result["enhanced"], prompt)
        self.assertEqual(result["attempts"], 1)


if __name__ == "__main__":
    unittest.main()
