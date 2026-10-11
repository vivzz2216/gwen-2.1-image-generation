import json
import unittest
from unittest.mock import MagicMock, patch

import desktop_app


class DesktopIntegrationTests(unittest.TestCase):
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


if __name__ == "__main__":
    unittest.main()
