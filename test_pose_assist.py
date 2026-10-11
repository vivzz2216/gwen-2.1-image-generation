import io
import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

import numpy as np
from fastapi.testclient import TestClient
from PIL import Image
import dolphin_prompting as dolphin
import pose_assist
import studio_service as service


def png(color="blue", mask=False):
    image = Image.new("RGB", (128, 128), color)
    if mask:
        image.paste("white", (40, 40, 90, 90))
    data = io.BytesIO()
    image.save(data, "PNG")
    return data.getvalue()


class PoseAssistTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        root = Path(self.temp.name)
        self.patches = [patch.object(service, "OUTPUT_DIR", str(root)), patch.object(service, "STATE_DIR", root),
                        patch.object(service, "DB_PATH", root / "jobs.sqlite3"), patch.object(service, "COMFY_INPUT_DIR", root / "input"),
                        patch.object(service.EXECUTOR, "submit")]
        for p in self.patches:
            p.start()
        service.COMFY_INPUT_DIR.mkdir()
        self.client = TestClient(service.app)

    def tearDown(self):
        for p in reversed(self.patches):
            p.stop()
        self.temp.cleanup()

    def submit_pose(self, **kwargs):
        data = {"prompt": "Two players push at chest level", "pose_enabled": "true", "pose_reviewed": "true", "lora_enabled": "false", **kwargs}
        files = {"pose_reference": ("pose.png", png(), "image/png")}
        if data.get("task") == "edit":
            files["image"] = ("source.png", png("green"), "image/png")
        return self.client.post("/api/jobs", data=data, files=files)

    def test_pose_requires_explicit_review(self):
        self.assertEqual(self.submit_pose(pose_reviewed="false").status_code, 422)

    def test_generate_uses_blank_canvas_and_pose_slot_two(self):
        response = self.submit_pose(aspect_ratio="16:9", mode="fast")
        self.assertEqual(response.status_code, 202, response.text)
        req = service.get_job(response.json()["job_id"])["request"]
        self.assertEqual(req["task"], "generate")
        self.assertFalse(req["preserve"])
        self.assertEqual(len(req["reference_paths"]), 1)
        with Image.open(req["source_path"]) as im:
            self.assertEqual(im.size, service.engine.generation_dimensions("fast", "16:9"))
            self.assertTrue(np.all(np.asarray(im) == 255))

    def test_edit_pose_removes_old_pose_locks_and_uses_global(self):
        response = self.submit_pose(task="edit", region="auto", preserve="true")
        self.assertEqual(response.status_code, 202, response.text)
        req = service.get_job(response.json()["job_id"])["request"]
        self.assertEqual(req["region"], "global")
        compiled = service.compile_prompt("Match the reference pose", pose_enabled=True, reference=2)
        self.assertIn("<image2> supplies only", compiled)
        self.assertIn("<image3>", compiled)
        self.assertNotIn("preserve pose and body proportions", compiled)
        self.assertNotIn("preserve hand positions", compiled)

    def test_pose_mask_must_be_reviewed(self):
        response = self.client.post("/api/jobs", data={"task": "edit", "prompt": "Change pose", "pose_enabled": "true", "pose_reviewed": "true", "region": "manual"},
            files={"image": ("source.png", png(), "image/png"), "pose_reference": ("pose.png", png(), "image/png"), "mask": ("mask.png", png("black", True), "image/png")})
        self.assertEqual(response.status_code, 422)

    def test_negative_hands_not_converted_to_two_visible_hands(self):
        extracted = pose_assist.interpretation("Two people, no extra hands and no text")
        self.assertIn("no extra hands", " ".join(extracted["explicit_exclusions"]))
        self.assertNotIn("exactly two visible hands", json.dumps(extracted))

    def test_explicit_contact_roles_are_extracted_without_guessing_a_side(self):
        extracted = pose_assist.interpretation("The blue player pushes the red player's chest")
        relationship = extracted["interaction_graph"][0]
        self.assertEqual(relationship["actor"], "The blue player")
        self.assertEqual(relationship["recipient"], "the red player")
        self.assertEqual(extracted["directions"], [])
        uncertain = pose_assist.interpretation("A guy kicked on the chest")
        self.assertIsNone(uncertain["interaction_graph"][0]["actor"])

    def test_preservation_is_measured_not_assumed(self):
        source = Image.new("RGB", (128, 128), "blue")
        mask = Image.open(io.BytesIO(png("black", True))).convert("L")
        generated = service.regions.composite_preserving(source, Image.new("RGB", (128, 128), "red"), mask)
        checked = pose_assist.preservation_check(source, generated, mask)
        self.assertEqual(checked["changed_outside_pixels"], 0)
        generated.putpixel((0, 0), (255, 0, 0))
        self.assertEqual(pose_assist.preservation_check(source, generated, mask)["changed_outside_pixels"], 1)

    def test_controlnet_is_explicitly_not_enabled(self):
        self.assertFalse(pose_assist.capabilities()["controlnet"]["enabled"])

    def test_descriptive_rewrite_cannot_choose_colors_or_light(self):
        import prompt_polish
        self.assertTrue(prompt_polish.description_errors("an apple falling from sky", "A red apple falls beneath sunlight in a blue sky."))
        self.assertFalse(prompt_polish.description_errors("a red apple falling from a blue sky", "A red apple falls through the blue sky."))
        self.assertFalse(prompt_polish.rewrite_errors("Two foxes under an umbrella", "Two foxes stand out as the subjects beneath the umbrella."))
        self.assertEqual(prompt_polish.neutralize_default_stance("Two foxes beneath an umbrella", "Two foxes are standing beneath an umbrella."), "Two foxes are positioned beneath an umbrella.")
        self.assertEqual(prompt_polish.neutralize_default_stance("Two foxes standing beneath an umbrella", "Two foxes are standing beneath an umbrella."), "Two foxes are standing beneath an umbrella.")
        self.assertEqual(prompt_polish.neutralize_default_stance('A sign reading "standing"', 'A sign reads "standing".'), 'A sign reads "standing".')

    def test_repair_requires_source_and_approved_mask(self):
        response = self.client.post("/api/jobs", data={"task": "edit", "prompt": "Fix the hand", "repair_kind": "hand"}, files={"image": ("source.png", png(), "image/png")})
        self.assertEqual(response.status_code, 422)

    def test_repair_removes_conflicting_hand_position_lock(self):
        text = service.compile_prompt("Fix my hand", repair_kind="hand")
        self.assertNotIn("hand positions", text)
        self.assertIn("identity", text)

    def test_repair_accepts_only_selected_source_and_one_job(self):
        parent = self.client.post("/api/jobs", data={"prompt": "Two people", "lora_enabled": "false"}).json()["job_id"]
        service.update_job(parent, status="succeeded")
        Image.new("RGB", (128, 128), "blue").save(Path(service.OUTPUT_DIR) / f"qwen_{parent}.png")
        data = {"task": "edit", "prompt": "Repair the hand", "repair_kind": "hand", "repair_parent_id": parent,
                "region": "manual", "preserve": "true", "mask_reviewed": "true", "lora_enabled": "false"}
        def submit(color="blue"):
            return self.client.post("/api/jobs", data=data, files={"image": ("source.png", png(color), "image/png"),
                "mask": ("mask.png", png("black", True), "image/png")})
        self.assertEqual(submit("red").status_code, 422)
        response = submit()
        self.assertEqual(response.status_code, 202, response.text)
        self.assertEqual(submit().status_code, 409)
        child = service.get_job(response.json()["job_id"])
        self.assertEqual(child["request"]["repair_parent_id"], parent)
        self.assertEqual(child["request"]["prompt"], "[Protected]")

    @patch.object(dolphin, "chat", return_value='{"rewritten_prompt":"An apple falls from the sky, suspended in the air during its downward movement. Its rounded fruit shape remains distinct against the sky as the scene captures the falling motion."}')
    def test_enhance_defaults_to_real_description_without_a_style(self, chat):
        result = dolphin.enhance("an apple falling from sky")
        self.assertGreater(len(result["enhanced"].split()), 20)
        self.assertEqual(result["detail"]["level"], "grounded_description")
        brief = json.loads(chat.call_args.args[0]["messages"][-1]["content"])
        self.assertIn("detail_budget", brief)
        self.assertNotIn("watercolor", result["enhanced"])


if __name__ == "__main__":
    unittest.main()
