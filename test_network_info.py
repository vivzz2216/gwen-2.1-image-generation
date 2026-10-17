"""Unit tests for remote connection helper and network info."""
import unittest
from unittest.mock import patch, MagicMock

import network_info


class NetworkInfoTests(unittest.TestCase):
    def test_get_lan_ip_with_mock_socket(self):
        with patch("socket.socket") as mock_socket_cls:
            mock_sock = MagicMock()
            mock_sock.getsockname.return_value = ("192.168.1.50", 54321)
            mock_socket_cls.return_value = mock_sock
            ip = network_info.get_lan_ip()
            self.assertEqual(ip, "192.168.1.50")

    def test_get_lan_ip_failure_returns_none(self):
        with patch("socket.socket", side_effect=OSError("Network unreachable")):
            ip = network_info.get_lan_ip()
            self.assertIsNone(ip)

    def test_get_tailscale_ip_success(self):
        with patch("subprocess.run") as mock_run:
            mock_run.return_value = MagicMock(returncode=0, stdout="100.85.12.34\n")
            ip = network_info.get_tailscale_ip()
            self.assertEqual(ip, "100.85.12.34")

    def test_get_tailscale_ip_failure_returns_none(self):
        with patch("subprocess.run", side_effect=Exception("Timeout")):
            ip = network_info.get_tailscale_ip()
            self.assertIsNone(ip)

    def test_format_connection_banner(self):
        with patch("network_info.get_lan_ip", return_value="192.168.0.101"), \
             patch("network_info.get_tailscale_ip", return_value="100.90.80.70"):
            banner = network_info.format_connection_banner(7860)
            self.assertIn("http://localhost:7860", banner)
            self.assertIn("http://192.168.0.101:7860", banner)
            self.assertIn("http://100.90.80.70:7860", banner)
