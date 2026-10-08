import io
import json
import sqlite3
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from fastapi.testclient import TestClient
from PIL import Image

import studio_service as service


def make_test_image():
    buf = io.BytesIO()
    Image.new("RGB", (64, 64), "white").save(buf, format="PNG")
    return buf.getvalue()


class SecurityPrivacyTests(unittest.TestCase):
    def setUp(self):
        self.temp_dir = tempfile.TemporaryDirectory(ignore_cleanup_errors=True)
        self.root = Path(self.temp_dir.name)
        self.output_dir = self.root / "outputs"
        self.state_dir = self.output_dir / ".studio"
        self.comfy_out = self.root / "comfy_output"
        self.comfy_in = self.root / "comfy_input"
        self.output_dir.mkdir(parents=True)
        self.state_dir.mkdir(parents=True)
        self.comfy_out.mkdir(parents=True)
        self.comfy_in.mkdir(parents=True)

        self.patches = [
            patch.object(service, "OUTPUT_DIR", str(self.output_dir)),
            patch.object(service, "STATE_DIR", self.state_dir),
            patch.object(service, "DB_PATH", self.state_dir / "jobs.sqlite3"),
            patch.object(service, "COMFY_OUTPUT_DIR", self.comfy_out),
            patch.object(service, "COMFY_INPUT_DIR", self.comfy_in),
        ]
        for p in self.patches:
            p.start()
        self.client = TestClient(service.app)

    def tearDown(self):
        for p in reversed(self.patches):
            p.stop()
        self.temp_dir.cleanup()

    def test_prompts_are_never_saved_to_disk_or_database(self):
        secret_prompt = "TOP_SECRET_PROMPT_123456_NEVER_PERSIST"
        resp = self.client.post("/api/jobs", data={"prompt": secret_prompt, "mode": "fast"})
        self.assertEqual(resp.status_code, 202)
        job_id = resp.json()["job_id"]

        # 1. Verify SQLite DB does not contain the prompt string
        with sqlite3.connect(self.state_dir / "jobs.sqlite3") as db:
            rows = db.execute("SELECT data FROM jobs").fetchall()
            for (row_data,) in rows:
                self.assertNotIn(secret_prompt, row_data)

        # 2. Verify GET /api/jobs/{job_id} does NOT return the secret prompt
        get_job_resp = self.client.get(f"/api/jobs/{job_id}").json()
        self.assertEqual(get_job_resp["prompt"], "[Protected]")
        self.assertNotIn(secret_prompt, json.dumps(get_job_resp))

        # 3. Verify GET /api/history does NOT return the secret prompt
        hist_resp = self.client.get("/api/history").json()
        self.assertNotIn(secret_prompt, json.dumps(hist_resp))

        # 4. Verify no .workflow.json file was written to disk
        workflow_files = list(self.state_dir.glob("*.workflow.json"))
        self.assertEqual(workflow_files, [])

        # 5. Verify workflow download endpoint is forbidden
        wf_resp = self.client.get(f"/api/jobs/{job_id}/workflow")
        self.assertEqual(wf_resp.status_code, 403)

    def test_generated_image_contains_zero_prompt_metadata_and_wipes_comfy_output(self):
        secret_prompt = "CONFIDENTIAL_OUTFIT_RED_DRESS_987"
        resp = self.client.post("/api/jobs", data={"prompt": secret_prompt, "mode": "fast"})
        job_id = resp.json()["job_id"]

        # Simulate ComfyUI output creation inside COMFY_OUTPUT_DIR
        mock_comfy_output = self.comfy_out / "comfy_raw.png"
        raw_img = Image.new("RGB", (128, 128), "red")
        # Even if ComfyUI embedded prompt metadata in raw image:
        from PIL import PngImagePlugin
        meta = PngImagePlugin.PngInfo()
        meta.add_text("prompt", secret_prompt)
        raw_img.save(mock_comfy_output, pnginfo=meta)

        with patch.object(service.CLIENT, "run", return_value=mock_comfy_output), \
             patch.object(service.CLIENT, "wipe_history") as mock_wipe:
            service.process_job(job_id, service.CANCEL_EVENTS[job_id])
            mock_wipe.assert_called()

        # 1. Verify mock_comfy_output was deleted from ComfyUI output directory
        self.assertFalse(mock_comfy_output.exists())

        # 2. Verify final image is saved strictly in OUTPUT_DIR and has NO prompt metadata
        job_data = self.client.get(f"/api/jobs/{job_id}").json()
        saved_file = self.output_dir / Path(job_data["image_url"]).name
        self.assertTrue(saved_file.exists())
        with Image.open(saved_file) as im:
            self.assertNotIn("prompt", im.info)
            self.assertNotIn("qwen_studio", im.info)
            # Inspect raw bytes of PNG to ensure secret_prompt is not in chunks
            raw_bytes = saved_file.read_bytes()
            self.assertNotIn(secret_prompt.encode("utf-8"), raw_bytes)


if __name__ == "__main__":
    unittest.main()
