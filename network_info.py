"""Network and remote access discovery for Qwen Image 2.1 Studio."""
import shutil
import socket
import subprocess
from typing import Optional


def get_lan_ip() -> Optional[str]:
    """Retrieve the primary local IPv4 address connected to the local network."""
    try:
        s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        s.connect(("8.8.8.8", 80))
        ip = s.getsockname()[0]
        s.close()
        return ip
    except Exception:
        return None


def get_tailscale_ip() -> Optional[str]:
    """Retrieve the Tailscale IPv4 address if Tailscale is running and connected."""
    tailscale_exe = shutil.which("tailscale") or r"C:\Program Files\Tailscale\tailscale.exe"
    try:
        res = subprocess.run([tailscale_exe, "ip", "-4"], capture_output=True, text=True, timeout=2)
        if res.returncode == 0 and res.stdout.strip():
            lines = res.stdout.strip().splitlines()
            if lines and lines[0].strip():
                return lines[0].strip()
    except Exception:
        pass
    return None


def format_connection_banner(port: int = 7860) -> str:
    """Format an informative banner displaying local, LAN, and Tailscale connection URLs."""
    lan_ip = get_lan_ip()
    ts_ip = get_tailscale_ip()
    lines = [
        "=" * 70,
        "  Qwen Image 2.1 Studio - Mobile (iPhone) & Remote Access",
        "=" * 70,
        f"  * Laptop (local):       http://localhost:{port}",
    ]
    if lan_ip:
        lines.append(f"  * iPhone (Same Wi-Fi):  http://{lan_ip}:{port}   (Open in Safari on iPhone)")
    if ts_ip:
        lines.append(f"  * iPhone (Tailscale):   http://{ts_ip}:{port}   (Works from anywhere!)")
    else:
        lines.append("  * Tailscale:            Not active yet (Log in to Tailscale on laptop & iPhone)")
    lines.append("=" * 70)
    return "\n".join(lines)
