import io
import tempfile
import threading
import time
import unittest
from pathlib import Path
from unittest.mock import patch

import numpy as np
from fastapi.testclient import TestClient
from PIL import Image
import studio_service as service


def bitmap(color="blue", mask=False):
    image = Image.new("RGB", (128, 128), color)
    if mask:
        image.paste("white", (40, 40, 90, 90))
    data = io.BytesIO()
    image.save(data, "PNG")
    return data.getvalue()


class JobTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        root = Path(self.temp.name)
        self.patches = [patch.object(service, "OUTPUT_DIR", str(root)),
                        patch.object(service, "STATE_DIR", root),
                        patch.object(service, "DB_PATH", root / "jobs.sqlite3"),
                        patch.object(service, "COMFY_INPUT_DIR", root / "input")]
        for p in self.patches:
            p.start()
        service.COMFY_INPUT_DIR.mkdir()
        self.client = TestClient(service.app)

    def tearDown(self):
        for p in reversed(self.patches):
            p.stop()
        self.temp.cleanup()

    @patch.object(service.EXECUTOR, "submit")
    @patch.object(service.regions, "propose_mask")
    def test_protected_edit_auto_masks_when_mask_missing(self, propose_mask, submit):
        from editing_regions import MaskProposal
        propose_mask.return_value = MaskProposal(
            mask=Image.new("L", (128, 128), 255),
            target="upper_clothes",
            confidence=0.95,
            method="segformer"
        )
        response = self.client.post("/api/jobs", data={"task": "edit", "prompt": "change shirt", "preserve": "true"},
                                    files={"image": ("source.png", bitmap(), "image/png")})
        self.assertEqual(response.status_code, 202)
        submit.assert_called_once()
        job_id = response.json()["job_id"]
        job = service.get_job(job_id)
        self.assertIsNotNone(job["request"]["mask_path"])
        service.CANCEL_EVENTS.pop(job_id, None)

    @patch.object(service.EXECUTOR, "submit")
    def test_queue_cancel_and_persistent_history(self, submit):
        response = self.client.post("/api/jobs", data={"prompt": "a green forest", "mode": "fast"})
        self.assertEqual(response.status_code, 202)
        job_id = response.json()["job_id"]
        self.assertEqual(self.client.post(f"/api/jobs/{job_id}/cancel").json()["status"], "cancelled")
        self.assertEqual(self.client.get("/api/history").json()["items"][0]["job_id"], job_id)
        service.CANCEL_EVENTS.pop(job_id)

    @patch.object(service.EXECUTOR, "submit")
    def test_masked_job_saves_exact_original_exterior_and_workflow(self, submit):
        response = self.client.post("/api/jobs", data={"task": "edit", "prompt": "change shirt to red", "mode": "ultra",
                 "preserve": "true", "region": "manual", "num_steps": "2"}, files={
                 "image": ("source.png", bitmap(), "image/png"),
                 "mask": ("mask.png", bitmap("black", mask=True), "image/png")})
        self.assertEqual(response.status_code, 202)
        job_id = response.json()["job_id"]
        generated = Path(self.temp.name) / "generated.png"
        Image.new("RGB", (256, 256), "red").save(generated)
        with patch.object(service.CLIENT, "run", return_value=generated):
            service.process_job(job_id, service.CANCEL_EVENTS[job_id])
        job = self.client.get(f"/api/jobs/{job_id}").json()
        self.assertEqual(job["status"], "succeeded", job["error"])
        self.assertEqual(job["prompt"], "[Protected]")
        self.assertEqual(self.client.get(f"/api/jobs/{job_id}/workflow").status_code, 403)
        output = Path(self.temp.name) / Path(job["image_url"]).name
        with Image.open(output) as im:
            pixels = np.array(im)
            self.assertNotIn("prompt", im.info)
        self.assertTrue(np.all(pixels[:40, :] == [0, 0, 255]))
        self.assertGreater(int(pixels[60, 60, 0]), 250)
        self.assertEqual(job["metadata"]["dimensions"], [128, 128])

    def test_prompt_preservation_excludes_requested_hair_or_background(self):
        hair = service.compile_prompt("change hairstyle", target="hair")
        background = service.compile_prompt("replace background", target="background")
        self.assertNotIn(", hairstyle", hair)
        self.assertNotIn("background and surroundings", background)

    @patch.object(service.EXECUTOR, "submit")
    @patch.object(service.regions, "propose_mask")
    def test_auto_pose_edit_does_not_use_an_outfit_mask(self, propose, submit):
        response = self.client.post("/api/jobs", data={"task": "edit", "prompt": "Raise my left arm while wearing a suit", "region": "auto"},
                                    files={"image": ("source.png", bitmap(), "image/png")})
        self.assertEqual(response.status_code, 202)
        job_id = response.json()["job_id"]
        job = service.get_job(job_id)
        self.assertEqual(job["request"]["region"], "global")
        self.assertTrue(job["request"]["preserve"])
        self.assertIsNone(job["request"]["mask_path"])
        propose.assert_not_called()
        service.CANCEL_EVENTS.pop(job_id, None)
        service.ACTIVE_PROMPTS.pop(job_id, None)

    @patch.object(service.EXECUTOR, "submit")
    def test_explicit_clothing_region_cannot_silently_clip_a_kick(self, submit):
        response = self.client.post("/api/jobs", data={"task": "edit", "prompt": "Make me kick the other player's chest", "region": "outfit"},
                                    files={"image": ("source.png", bitmap(), "image/png")})
        self.assertEqual(response.status_code, 422)
        self.assertIn("Global edit", response.json()["detail"])
        submit.assert_not_called()

    @patch.object(service.EXECUTOR, "submit")
    def test_batch_submission_and_status(self, submit):
        import json
        prompts = ["a majestic lion", "a playful otter", "a soaring eagle"]
        response = self.client.post("/api/jobs/batch", data={"prompts": json.dumps(prompts), "mode": "fast"})
        self.assertEqual(response.status_code, 202)
        data = response.json()
        self.assertEqual(data["total"], 3)
        self.assertEqual(len(data["job_ids"]), 3)
        self.assertEqual(submit.call_count, 3)

        batch_id = data["batch_id"]
        status_resp = self.client.get(f"/api/batches/{batch_id}")
        self.assertEqual(status_resp.status_code, 200)
        batch_data = status_resp.json()
        self.assertEqual(batch_data["total"], 3)
        self.assertEqual(len(batch_data["jobs"]), 3)
        for job in batch_data["jobs"]:
            self.assertEqual(job["prompt"], "[Protected]")
            self.assertEqual(job["status"], "queued")
            service.CANCEL_EVENTS.pop(job["job_id"], None)

    @patch.object(service.EXECUTOR, "submit")
    def test_batch_validation_limits(self, submit):
        import json
        # Over 5 prompts rejected
        too_many = json.dumps([f"prompt {i}" for i in range(6)])
        resp = self.client.post("/api/jobs/batch", data={"prompts": too_many})
        self.assertEqual(resp.status_code, 422)
        submit.assert_not_called()

        # Zero prompts rejected
        empty = json.dumps([])
        resp = self.client.post("/api/jobs/batch", data={"prompts": empty})
        self.assertEqual(resp.status_code, 422)

    @patch.object(service.EXECUTOR, "submit")
    def test_batch_cancel(self, submit):
        import json
        prompts = ["scene 1", "scene 2"]
        response = self.client.post("/api/jobs/batch", data={"prompts": json.dumps(prompts), "mode": "balanced"})
        self.assertEqual(response.status_code, 202)
        batch_id = response.json()["batch_id"]

        cancel_resp = self.client.post(f"/api/batches/{batch_id}/cancel")
        self.assertEqual(cancel_resp.status_code, 200)
        batch_data = cancel_resp.json()
        self.assertEqual(batch_data["overall_status"], "cancelled")
        for job in batch_data["jobs"]:
            self.assertEqual(job["status"], "cancelled")
            service.CANCEL_EVENTS.pop(job["job_id"], None)


    @patch.object(service.EXECUTOR, "submit")
    def test_batch_edit_mode(self, submit):
        import json
        prompts = ["change shirt to red", "change shirt to green"]
        response = self.client.post("/api/jobs/batch", data={
            "task": "edit", "prompts": json.dumps(prompts), "mode": "ultra",
            "preserve": "true", "region": "manual", "num_steps": "2"
        }, files={
            "image": ("source.png", bitmap(), "image/png"),
            "mask": ("mask.png", bitmap("black", mask=True), "image/png")
        })
        self.assertEqual(response.status_code, 202)
        data = response.json()
        self.assertEqual(data["total"], 2)
        self.assertEqual(submit.call_count, 2)
        for jid in data["job_ids"]:
            service.CANCEL_EVENTS.pop(jid, None)

    @patch.object(service.EXECUTOR, "submit")
    @patch.object(service.identity_assist, "face_boxes")
    @patch.object(service.identity_assist, "similarity", return_value={"status": "measured", "faces": [{"cosine_similarity": 0.85}]})
    def test_face_detail_runs_for_small_face(self, similarity, face_boxes, submit):
        import json
        calls = []
        def mock_run(workflow, **kwargs):
            p = Path(self.temp.name) / f"mock_{len(calls)}.png"
            Image.new("RGB", (896, 672), (120, 140, 160)).save(p)
            calls.append(workflow)
            return p

        with patch.object(service.CLIENT, "run", side_effect=mock_run):
            face_boxes.return_value = [{'box': (400, 60, 50, 60), 'width': 50, 'height': 60}]
            response = self.client.post("/api/jobs", data={
                "task": "generate", "prompt": "Riding a bicycle", "identity_enabled": "true",
                "identity_reviewed": "true", "identity_box": "[0.2, 0.1, 0.5, 0.5]",
                "face_detail": "true", "mode": "fast", "lora_enabled": "false"
            }, files={"identity_reference": ("identity.png", bitmap(), "image/png")})
            self.assertEqual(response.status_code, 202)
            job_id = response.json()["job_id"]
            # Process job synchronously with event
            event = service.CANCEL_EVENTS[job_id]
            service.process_job(job_id, event)

            job = service.get_job(job_id)
            self.assertEqual(job["status"], "succeeded")
            # Should have called CLIENT.run twice: 1 initial generation, 1 face detail
            self.assertEqual(len(calls), 2)
            metadata = job["result"]["metadata"]
            self.assertIn("face_detail", metadata)
            self.assertTrue(metadata["face_detail"]["applied"])
            base_file = Path(service.OUTPUT_DIR) / f"qwen_{job_id}_base.png"
            final_file = Path(service.OUTPUT_DIR) / f"qwen_{job_id}.png"
            self.assertTrue(base_file.exists())
            self.assertTrue(final_file.exists())
            service.CANCEL_EVENTS.pop(job_id, None)

    @patch.object(service.EXECUTOR, "submit")
    @patch.object(service.identity_assist, "face_boxes")
    @patch.object(service.identity_assist, "similarity", return_value={"status": "measured", "faces": [{"cosine_similarity": 0.85}]})
    def test_face_detail_skips_when_face_is_large(self, similarity, face_boxes, submit):
        calls = []
        def mock_run(workflow, **kwargs):
            p = Path(self.temp.name) / f"mock_{len(calls)}.png"
            Image.new("RGB", (896, 672), (120, 140, 160)).save(p)
            calls.append(workflow)
            return p

        with patch.object(service.CLIENT, "run", side_effect=mock_run):
            # Face width/height >= 128
            face_boxes.return_value = [{'box': (300, 100, 180, 220), 'width': 180, 'height': 220}]
            response = self.client.post("/api/jobs", data={
                "task": "generate", "prompt": "Portrait laughing", "identity_enabled": "true",
                "identity_reviewed": "true", "identity_box": "[0.2, 0.1, 0.5, 0.5]",
                "face_detail": "true", "mode": "fast", "lora_enabled": "false"
            }, files={"identity_reference": ("identity.png", bitmap(), "image/png")})
            self.assertEqual(response.status_code, 202)
            job_id = response.json()["job_id"]
            event = service.CANCEL_EVENTS[job_id]
            service.process_job(job_id, event)

            job = service.get_job(job_id)
            self.assertEqual(job["status"], "succeeded")
            # Should have called CLIENT.run only ONCE (skipped face detail)
            self.assertEqual(len(calls), 1)
            metadata = job["result"]["metadata"]
            self.assertIn("face_detail", metadata)
            self.assertFalse(metadata["face_detail"]["applied"])
            service.CANCEL_EVENTS.pop(job_id, None)

    @patch.object(service.EXECUTOR, "submit")
    @patch.object(service.identity_assist, "face_boxes")
    @patch.object(service.identity_assist, "similarity", return_value={"status": "measured", "faces": [{"cosine_similarity": 0.85}]})
    def test_face_detail_error_does_not_fail_job(self, similarity, face_boxes, submit):
        calls = []
        def mock_run(workflow, **kwargs):
            if len(calls) == 0:
                p = Path(self.temp.name) / "mock_0.png"
                Image.new("RGB", (896, 672), (120, 140, 160)).save(p)
                calls.append(workflow)
                return p
            raise RuntimeError("Temporary CUDA failure during sharpening")

        with patch.object(service.CLIENT, "run", side_effect=mock_run):
            face_boxes.return_value = [{'box': (400, 60, 50, 60), 'width': 50, 'height': 60}]
            response = self.client.post("/api/jobs", data={
                "task": "generate", "prompt": "Riding", "identity_enabled": "true",
                "identity_reviewed": "true", "identity_box": "[0.2, 0.1, 0.5, 0.5]",
                "face_detail": "true", "mode": "fast", "lora_enabled": "false"
            }, files={"identity_reference": ("identity.png", bitmap(), "image/png")})
            job_id = response.json()["job_id"]
            event = service.CANCEL_EVENTS[job_id]
            service.process_job(job_id, event)

            job = service.get_job(job_id)
            # Job still succeeds with base output
            self.assertEqual(job["status"], "succeeded")
            metadata = job["result"]["metadata"]
            self.assertFalse(metadata["face_detail"]["applied"])
            self.assertTrue(any("Face detail enhancement" in w for w in metadata["warnings"]))
            service.CANCEL_EVENTS.pop(job_id, None)


if __name__ == "__main__":
    unittest.main()
