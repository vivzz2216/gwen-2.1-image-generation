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

    def test_windows_system_stats_endpoint(self):
        response = self.client.get("/api/windows/system-stats")
        self.assertEqual(response.status_code, 200)
        data = response.json()
        self.assertIn("status", data)
        self.assertEqual(data["status"], "ready")
        self.assertIn("ram", data)
        if data["ram"]:
            self.assertIn("total_gb", data["ram"])

    @patch("os.startfile", create=True)
    def test_windows_open_folder_endpoint(self, mock_startfile):
        response = self.client.post("/api/windows/open-folder")
        self.assertEqual(response.status_code, 200)
        data = response.json()
        self.assertEqual(data["status"], "success")

    def test_capabilities_reports_lora(self):
        response = self.client.get("/api/capabilities")
        self.assertEqual(response.status_code, 200)
        data = response.json()
        self.assertIn("lora", data)
        self.assertTrue(data["lora"]["installed"])
        self.assertEqual(data["lora"]["name"], "NSFW Qwen Lora.safetensors")
        self.assertEqual(data["lora"]["recommended_strength"], 0.8)

    @patch("studio_service._run_comfy_workflow", return_value="/outputs/qwen_lora.png")
    def test_generate_endpoint_lora_toggle(self, run_workflow):
        # When LoRA is enabled
        res_enabled = self.client.post("/api/generate", json={"prompt": "scene", "lora_enabled": True, "lora_strength": 0.85})
        self.assertEqual(res_enabled.status_code, 200)
        wf_enabled = run_workflow.call_args.args[0]
        self.assertIn("1b", wf_enabled)
        self.assertEqual(wf_enabled["1b"]["inputs"]["strength_model"], 0.85)

        # When LoRA is disabled
        res_disabled = self.client.post("/api/generate", json={"prompt": "scene", "lora_enabled": False})
        self.assertEqual(res_disabled.status_code, 200)
        wf_disabled = run_workflow.call_args.args[0]
        self.assertNotIn("1b", wf_disabled)
        self.assertEqual(wf_disabled["2"]["inputs"]["model"], ["1", 0])

    def test_capabilities_lists_available_loras(self):
        data = self.client.get("/api/capabilities").json()
        available = data["lora"]["available"]
        self.assertIn("NSFW Qwen Lora.safetensors", available)
        self.assertNotIn("NSFW Qwen by TheseAlpacas V2.safetensors", available)

    @patch("studio_service._run_comfy_workflow", return_value="/outputs/qwen_lora.png")
    def test_generate_uses_selected_lora(self, run_workflow):
        res = self.client.post("/api/generate", json={"prompt": "scene", "lora_name": "NSFW Qwen Lora.safetensors"})
        self.assertEqual(res.status_code, 200)
        wf = run_workflow.call_args.args[0]
        self.assertEqual(wf["1b"]["inputs"]["lora_name"], "NSFW Qwen Lora.safetensors")

    @patch("studio_service._run_comfy_workflow", return_value="/outputs/qwen_lora.png")
    def test_generate_rejects_unknown_or_traversal_lora(self, run_workflow):
        for bad in ("missing.safetensors", "..\\..\\evil.safetensors"):
            res = self.client.post("/api/generate", json={"prompt": "scene", "lora_name": bad})
            self.assertEqual(res.status_code, 422)
        run_workflow.assert_not_called()

    def test_capabilities_reports_dual_enhancers(self):
        response = self.client.get("/api/capabilities")
        self.assertEqual(response.status_code, 200)
        enhancer = response.json().get("enhancer", {})
        self.assertIn("models", enhancer)
        self.assertIn("dolphin", enhancer["models"])
        self.assertIn("qwen", enhancer["models"])
        self.assertEqual(enhancer["models"]["dolphin"]["name"], "qwen21-dolphin")
        self.assertEqual(enhancer["models"]["qwen"]["name"], "qwen21-qwen4b")

    @patch.object(studio_app.CLIENT, "request", return_value={"queue_running": [], "queue_pending": []})
    @patch("dolphin_prompting.enhance", return_value={"enhanced": "A cinematic landscape."})
    @patch("dolphin_prompting.unload")
    @patch("studio_service.release_qwen_for_enhancer")
    def test_enhance_endpoint_accepts_dolphin_and_qwen_models(self, _release, _unload, mock_enhance, _queue):
        res_dolphin = self.client.post("/api/enhance", json={"prompt": "landscape", "model": "dolphin"})
        self.assertEqual(res_dolphin.status_code, 200)
        self.assertEqual(mock_enhance.call_args.kwargs["model"], "qwen21-dolphin")

        res_qwen = self.client.post("/api/enhance", json={"prompt": "landscape", "model": "qwen"})
        self.assertEqual(res_qwen.status_code, 200)
        self.assertEqual(mock_enhance.call_args.kwargs["model"], "qwen21-qwen4b")


if __name__ == "__main__":
    unittest.main()


