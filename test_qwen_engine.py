"""Workflow semantics and queue/output boundary tests, without model loading."""
import tempfile
import threading
import types
import unittest
from unittest.mock import patch
from pathlib import Path

from PIL import Image

from qwen_engine import (
    ComfyClient, GenerationCancelled, ImageGenerationError, MODES,
    build_edit, build_generation, generation_dimensions, image_dimensions,
)


PROMPT_ID = "00000000-0000-4000-8000-000000000001"


class WorkflowTests(unittest.TestCase):
    def test_modes_change_native_work_and_memory(self):
        profiles = [build_generation("a copper robot", mode) for mode in MODES]
        self.assertEqual([p["6"]["inputs"]["width"] for p in profiles], [512, 768, 1024])
        self.assertEqual([p["7"]["inputs"]["steps"] for p in profiles], [16, 28, 36])
        self.assertEqual([p["8"]["class_type"] for p in profiles],
                         ["VAEDecode", "VAEDecodeTiled", "VAEDecodeTiled"])
        self.assertEqual(profiles[0]["2"]["inputs"]["device"], "off")
        self.assertEqual(profiles[1]["2"]["inputs"]["dtype"], "int8")

    def test_native_edit_uses_empty_target_full_denoising(self):
        workflow = build_edit("Replace the shoes", "source.png", reference_names=["shoes.png"])
        self.assertEqual(workflow["7"]["inputs"]["latent_image"], ["5", 2])
        self.assertEqual(workflow["7"]["inputs"]["denoise"], 1.0)
        self.assertIn("images.image_2", workflow["5"]["inputs"])
        self.assertEqual(workflow["32"]["inputs"]["alpha"], ["22", 1])

    def test_isolated_input_subfolder_is_allowed_and_traversal_is_not(self):
        workflow = build_edit("Add sunglasses", "studio_uploads/source.png")
        self.assertEqual(workflow["10"]["inputs"]["image"], "studio_uploads/source.png")
        for name in ("studio_uploads/../source.png", "C:\\source.png", "/source.png",
                     "studio_uploads//source.png", "\\\\host\\source.png"):
            with self.subTest(name=name), self.assertRaises(ValueError):
                build_edit("Add sunglasses", name)

    def test_rgba_mask_anchor_encodes_source_at_matching_size(self):
        workflow = build_edit("Change jacket", "source.png", mode="ultra",
                              source_has_alpha=True, source_size=(600, 900), mask_name="mask.png")
        self.assertEqual(workflow["11"]["inputs"]["alpha"], ["10", 1])
        self.assertEqual(workflow["7"]["inputs"]["latent_image"], ["15", 0])
        self.assertEqual(workflow["13"]["inputs"]["pixels"], ["12", 0])
        self.assertEqual(workflow["14"]["inputs"]["channel"], "red")
        self.assertEqual((workflow["12"]["inputs"]["width"], workflow["12"]["inputs"]["height"]),
                         image_dimensions("ultra", (600, 900)))

    def test_dimensions_and_invalid_inputs(self):
        for mode in MODES:
            for ratio in ("1:1", "16:9", "9:16", "3:2"):
                width, height = generation_dimensions(mode, ratio)
                self.assertEqual(width % 32, 0)
                self.assertEqual(height % 32, 0)
        for kwargs in ({"mode": "unknown"}, {"steps": 0}, {"seed": -1},
                       {"seed": 2**64}, {"source_name": "../escape.png"},
                       {"mask_name": "mask.png"}, {"aspect_ratio": "wrong"}):
            with self.subTest(kwargs=kwargs), self.assertRaises(ValueError):
                build_generation("valid prompt", **kwargs)


class ClientTests(unittest.TestCase):
    def test_websocket_progress_filters_other_jobs(self):
        import json
        class Timeout(Exception):
            pass
        events = iter([
            {"type": "progress", "data": {"prompt_id": "other", "value": 99, "max": 100}},
            {"type": "executing", "data": {"prompt_id": PROMPT_ID, "node": "7"}},
            {"type": "progress", "data": {"prompt_id": PROMPT_ID, "value": 8, "max": 28, "node": "7"}},
        ])
        class Socket:
            def recv(self):
                try:
                    return json.dumps(next(events))
                except StopIteration:
                    raise Timeout()
        with patch.dict("sys.modules", {"websocket": types.SimpleNamespace(WebSocketTimeoutException=Timeout)}):
            result = ComfyClient._read_progress(Socket(), PROMPT_ID)
        self.assertEqual(result, {"stage": "sampling", "node": "7", "step": 8, "steps": 28})

    def test_output_validates_pixels_and_prevents_directory_escape(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            client = ComfyClient("http://local", root)
            Image.new("RGBA", (32, 32), (255, 0, 0, 100)).save(root / "result.png")
            record = {"status": {"completed": True, "status_str": "success"},
                      "outputs": {"9": {"images": [{"filename": "result.png", "type": "output"}]}}}
            self.assertEqual(client._output(record), (root / "result.png").resolve())
            record["outputs"]["9"]["images"][0]["filename"] = "../outside.png"
            with self.assertRaises(ImageGenerationError):
                client._output(record)
            (root / "bad.png").write_bytes(b"not an image")
            record["outputs"]["9"]["images"][0]["filename"] = "bad.png"
            with self.assertRaises(ImageGenerationError):
                client._output(record)

    def test_cancellation_only_reaches_owned_atomic_job(self):
        client = ComfyClient("http://local", Path("outputs"))
        calls = []
        client.request = lambda path, *args, **kwargs: calls.append(path) or {"cancelled": True}
        self.assertFalse(client.cancel(PROMPT_ID))
        self.assertEqual(calls, [])
        client._owned.add(PROMPT_ID)
        self.assertTrue(client.cancel(PROMPT_ID))
        self.assertEqual(calls, [f"/api/jobs/{PROMPT_ID}/cancel"])

    def test_cancel_before_submission_has_no_remote_side_effect(self):
        client = ComfyClient("http://local", Path("outputs"))
        event = threading.Event()
        event.set()
        client.request = lambda *args, **kwargs: self.fail("Cancelled job was submitted")
        with self.assertRaises(GenerationCancelled):
            client.run(build_generation("a tree"), cancel_event=event)

    def test_cancel_running_job_waits_for_release_and_does_not_interrupt_other_job(self):
        client = ComfyClient("http://local", Path("outputs"), poll_interval=0)
        event = threading.Event()
        calls = []
        def request(path, *args, **kwargs):
            calls.append(path)
            if path == "/prompt":
                return {"prompt_id": PROMPT_ID}
            if path.endswith("/cancel"):
                return {"cancelled": True}
            if path == "/queue":
                return {"queue_running": [[0, "another-job"]], "queue_pending": []}
            self.fail(f"Unexpected request {path}")
        client.request = request
        with self.assertRaises(GenerationCancelled):
            client.run(build_generation("a tree"), cancel_event=event,
                       on_submitted=lambda prompt_id: event.set())
        self.assertEqual(calls, ["/prompt", f"/api/jobs/{PROMPT_ID}/cancel", "/queue"])
        self.assertEqual(client._owned, set())

    def test_backend_reports_execution_error_instead_of_previous_output(self):
        client = ComfyClient("http://local", Path("outputs"))
        record = {"status": {"status_str": "error", "messages": [
            ["execution_error", {"exception_message": "CUDA out of memory"}]]}, "outputs": {}}
        with self.assertRaisesRegex(ImageGenerationError, "CUDA out of memory"):
            client._output(record)


if __name__ == "__main__":
    unittest.main()
