import json
import unittest
from unittest.mock import MagicMock, patch

import desktop_app


class DesktopIntegrationTests(unittest.TestCase):
    @patch.object(desktop_app.subprocess, "Popen")
    @patch.object(desktop_app, "port_listening", return_value=True)
    @patch.object(desktop_app, "reachable", return_value=False)
    def test_busy_backend_timeout_cannot_launch_duplicate_server(self, reachable, listening, popen):
        with self.assertRaisesRegex(RuntimeError, 'No second server'):
            desktop_app.DesktopAppManager().ensure_studio_backend()
        popen.assert_not_called()

    @patch.object(desktop_app.subprocess, "Popen")
    @patch.object(desktop_app.urllib.request, "urlopen")
    @patch.object(desktop_app, "reachable", return_value=True)
    def test_desktop_reuses_updated_backend(self, reachable, urlopen, popen):
        response = MagicMock()
        response.__enter__.return_value.read.return_value = json.dumps({"enhancer": {"profile": desktop_app.PROFILE_VERSION}}).encode()
        urlopen.return_value = response
        desktop_app.DesktopAppManager().ensure_studio_backend()
        popen.assert_not_called()

    @patch.object(desktop_app.subprocess, "Popen")
    @patch.object(desktop_app.urllib.request, "urlopen")
    @patch.object(desktop_app, "reachable", return_value=True)
    def test_desktop_does_not_silently_use_old_backend(self, reachable, urlopen, popen):
        response = MagicMock()
        response.__enter__.return_value.read.return_value = b'{"enhancer":{"model":"qwen21-dolphin"}}'
        urlopen.return_value = response
        with self.assertRaisesRegex(RuntimeError, "older Studio backend"):
            desktop_app.DesktopAppManager().ensure_studio_backend()
        popen.assert_not_called()

    @patch.object(desktop_app.network_info, "format_connection_banner", return_value="BANNER")
    @patch.object(desktop_app.subprocess, "Popen")
    @patch.object(desktop_app, "port_listening", return_value=False)
    @patch.object(desktop_app, "reachable", side_effect=[False, True])
    def test_desktop_launches_with_listen_host(self, reachable, listening, popen, mock_banner):
        mock_proc = MagicMock()
        mock_proc.poll.return_value = None
        popen.return_value = mock_proc
        app = desktop_app.DesktopAppManager(listen_host="0.0.0.0")
        app.ensure_studio_backend()
        popen.assert_called_once()
        cmd = popen.call_args[0][0]
        self.assertIn("--host", cmd)
        host_idx = cmd.index("--host")
        self.assertEqual(cmd[host_idx + 1], "0.0.0.0")


    @patch("desktop_app.Path.exists", return_value=True)
    @patch.object(desktop_app.subprocess, "Popen")
    def test_run_edge_app_enforces_inprivate(self, popen, mock_exists):
        proc_mock = MagicMock()
        proc_mock.wait.return_value = 0
        popen.return_value = proc_mock
        app = desktop_app.DesktopAppManager()
        launched = app.run_edge_app()
        self.assertTrue(launched)
        popen.assert_called_once()
        cmd = popen.call_args[0][0]
        self.assertIn("--inprivate", cmd)

    def test_run_webview_app_enforces_private_mode(self):
        mock_webview = MagicMock()
        with patch.dict("sys.modules", {"webview": mock_webview}):
            app = desktop_app.DesktopAppManager()
            launched = app.run_webview_app()
            self.assertTrue(launched)
            mock_webview.start.assert_called_once()
            kwargs = mock_webview.start.call_args[1]
            self.assertTrue(kwargs.get("private_mode"))

    def test_shutdown_cleans_residual_profiles_and_caches(self):
        import tempfile
        from pathlib import Path
        with tempfile.TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)
            webview_cache = tmp_path / "webview_cache"
            edge_profile = tmp_path / "edge_desktop_profile"
            webview_cache.mkdir()
            edge_profile.mkdir()
            (webview_cache / "temp.dat").write_text("cache")
            (edge_profile / "history.dat").write_text("history")

            with patch.object(desktop_app, "STATE_DIR", tmp_path):
                app = desktop_app.DesktopAppManager()
                app.shutdown()
                self.assertFalse(webview_cache.exists())
                self.assertFalse(edge_profile.exists())


if __name__ == "__main__":
    unittest.main()
