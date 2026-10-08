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
    def test_protected_edit_rejects_missing_reviewed_mask(self, submit):
        response = self.client.post("/api/jobs", data={"task": "edit", "prompt": "change shirt", "preserve": "true"},
                                    files={"image": ("source.png", bitmap(), "image/png")})
        self.assertEqual(response.status_code, 422)
        submit.assert_not_called()

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


if __name__ == "__main__":
    unittest.main()
