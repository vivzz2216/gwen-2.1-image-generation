import json
import unittest
from pathlib import Path
from unittest.mock import MagicMock, patch

from fastapi.testclient import TestClient
import dolphin_prompting as dolphin
import studio_service as service


class CandidateTests(unittest.TestCase):
    @patch.dict("os.environ", {}, clear=True)
    def test_selected_alias_and_rollback_configuration(self):
        path = MagicMock(spec=Path)
        path.read_text.return_value = '{"model":"qwen21-qwen4b"}'
        self.assertEqual(dolphin.configured_model(path), "qwen21-qwen4b")
        path.read_text.return_value = '{"model":"qwen21-dolphin"}'
        self.assertEqual(dolphin.configured_model(path), "qwen21-dolphin")
        path.read_text.return_value = '{"model":"unrecognized"}'
        self.assertEqual(dolphin.configured_model(path), "qwen21-dolphin")

    @patch("dolphin_prompting.MODEL", "qwen3.5:4b-q4_K_M")
    @patch("dolphin_prompting.urllib.request.urlopen")
    def test_unload_does_not_claim_another_variant_of_same_family(self, urlopen):
        response = MagicMock()
        response.__enter__.return_value.read.return_value = b'{"models":[{"name":"qwen3.5:9b"}]}'
        urlopen.return_value = response
        dolphin.unload()
        self.assertEqual(urlopen.call_count, 1)
        self.assertTrue(urlopen.call_args.args[0].endswith("/api/ps"))

    @patch("dolphin_prompting.urllib.request.urlopen")
    def test_qwen_candidate_has_small_context_and_non_thinking_mode(self, urlopen):
        response = MagicMock()
        response.__enter__.return_value.read.return_value = json.dumps({"done": True, "message": {"content": json.dumps({"rewritten_prompt": "A blue boat on a lake."})}}).encode()
        urlopen.return_value = response
        result = dolphin.enhance("A blue boat on a lake", model="qwen3.5:4b-q4_K_M")
        payload = json.loads(urlopen.call_args.args[0].data)
        self.assertFalse(payload["think"])
        self.assertEqual(payload["options"]["num_ctx"], 4096)
        self.assertEqual(payload["options"]["temperature"], .2)
        self.assertEqual(payload["options"]["presence_penalty"], 0.0)
        self.assertEqual(result["model"], "qwen3.5:4b-q4_K_M")

    def test_api_restricts_testing_override_to_supported_local_models(self):
        client = TestClient(service.app)
        result = client.post("/api/enhance", json={"prompt": "boat", "model": "arbitrary-model"})
        self.assertEqual(result.status_code, 422)

    @patch.object(service, "release_qwen_for_enhancer")
    @patch.object(service.CLIENT, "request", return_value={"queue_running": [], "queue_pending": []})
    @patch.object(dolphin, "unload")
    @patch.object(dolphin, "enhance", return_value={"enhanced": "A blue boat."})
    def test_override_runs_sequentially_and_is_not_kept_loaded(self, enhance, unload, _queue, _release):
        response = TestClient(service.app).post("/api/enhance", json={"prompt": "boat", "model": "qwen3.5:4b-q4_K_M"})
        self.assertEqual(response.status_code, 200)
        unload.assert_called_once()
        self.assertEqual(enhance.call_args.kwargs["keep_alive"], 0)
        self.assertEqual(enhance.call_args.kwargs["model"], "qwen3.5:4b-q4_K_M")

    def test_resolve_model_aliases(self):
        self.assertEqual(dolphin.resolve_model("dolphin"), "qwen21-dolphin")
        self.assertEqual(dolphin.resolve_model("qwen"), "qwen21-qwen4b")
        self.assertEqual(dolphin.resolve_model("qwen27b"), "qwen27b")
        self.assertEqual(dolphin.resolve_model("qwen21-dolphin"), "qwen21-dolphin")
        self.assertEqual(dolphin.resolve_model("qwen21-qwen4b"), "qwen21-qwen4b")

    @patch.object(service, "release_qwen_for_enhancer")
    @patch.object(service.CLIENT, "request", return_value={"queue_running": [], "queue_pending": []})
    @patch.object(dolphin, "unload")
    @patch.object(dolphin, "enhance", return_value={"enhanced": "Enhanced prompt."})
    def test_api_accepts_dolphin_and_qwen_aliases(self, enhance, _unload, _queue, _release):
        client = TestClient(service.app)
        res_dolphin = client.post("/api/enhance", json={"prompt": "test prompt", "model": "dolphin"})
        self.assertEqual(res_dolphin.status_code, 200)
        self.assertEqual(enhance.call_args.kwargs["model"], "qwen21-dolphin")

        res_qwen = client.post("/api/enhance", json={"prompt": "test prompt", "model": "qwen"})
        self.assertEqual(res_qwen.status_code, 200)
        self.assertEqual(enhance.call_args.kwargs["model"], "qwen21-qwen4b")


if __name__ == "__main__":
    unittest.main()

