import io
import unittest
from unittest.mock import patch

from fastapi.testclient import TestClient
from PIL import Image

import studio_service as studio_app


def jpeg_bytes() -> bytes:
    buffer = io.BytesIO()
    Image.new("RGB", (256, 256), "blue").save(buffer, format="JPEG")
    return buffer.getvalue()


class GenerationTests(unittest.TestCase):
    def setUp(self):
        self.client = TestClient(studio_app.app)

    def test_blank_prompt_is_rejected(self):
        response = self.client.post("/api/generate", json={"prompt": "   "})
        self.assertEqual(response.status_code, 422)

    @patch("studio_service._run_comfy_workflow", return_value="/outputs/qwen_test.png")
    def test_prompt_uses_local_qwen_workflow(self, run_workflow):
        response = self.client.post("/api/generate", json={"prompt": "a blue robot", "seed": 7})
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()["image_url"], "/outputs/qwen_test.png")
        workflow = run_workflow.call_args.args[0]
        self.assertEqual(workflow["1"]["inputs"]["unet_name"], "qwen_image_2.1_int8_convrot.safetensors")
        self.assertEqual(workflow["3"]["inputs"]["clip_name"], "qwen3vl_8b_int8_convrot.safetensors")

    @patch("studio_service._run_comfy_workflow", return_value="/outputs/qwen_edit.png")
    def test_image_and_prompt_use_qwen_edit_workflow(self, run_workflow):
        response = self.client.post(
            "/api/edit",
            data={"prompt": "make the background green", "num_steps": "4"},
            files={"image": ("source.jpg", jpeg_bytes(), "image/jpeg")},
        )
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()["image_url"], "/outputs/qwen_edit.png")
        workflow = run_workflow.call_args.args[0]
        self.assertEqual(workflow["7"]["inputs"]["latent_image"], ["5", 2])

    @patch("studio_service.generate_live_ai_image", side_effect=studio_app.ImageGenerationError("offline"))
    def test_provider_failure_is_reported_instead_of_returning_an_old_image(self, _generate):
        response = self.client.post("/api/generate", json={"prompt": "new scene"})
        self.assertEqual(response.status_code, 502)
        self.assertIn("offline", response.json()["detail"])


if __name__ == "__main__":
    unittest.main()
