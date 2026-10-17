"""Diagnostic helper to check iPhone connection status, network IPs, and firewall."""
import socket
import subprocess
import urllib.request
import network_info


def check_port_open(host: str, port: int) -> bool:
    try:
        with socket.create_connection((host, port), timeout=1.5):
            return True
    except OSError:
        return False


def is_firewall_allowed(port: int = 7860) -> bool:
    try:
        cmd = f'Get-NetFirewallRule | Where-Object {{ $_.DisplayName -match "{port}|Qwen" -and $_.Direction -eq "Inbound" -and $_.Action -eq "Allow" -and $_.Enabled -eq "True" }}'
        res = subprocess.run(["powershell", "-NoProfile", "-Command", cmd], capture_output=True, text=True, timeout=5)
        return bool(res.stdout.strip())
    except Exception:
        return False


def main():
    port = 7860
    lan_ip = network_info.get_lan_ip()
    ts_ip = network_info.get_tailscale_ip()
    is_running = check_port_open("127.0.0.1", port)
    firewall_ok = is_firewall_allowed(port)

    print("=" * 70)
    print("  Qwen Image Studio - iPhone Connection Diagnostics")
    print("=" * 70)

    print(f"\n[1] Studio Server Status:")
    if is_running:
        print(f"    * Running and listening on port {port} [OK]")
    else:
        print(f"    * Studio server is NOT running right now.")
        print(f"      Start it by double-clicking 'start_studio.bat'")

    print(f"\n[2] Windows Firewall (Port {port}):")
    if firewall_ok:
        print(f"    * Inbound rule configured [OK]")
    else:
        print(f"    * Inbound rule NOT found yet.")
        print(f"      -> Fix: Double-click 'open_firewall_for_iphone.bat' once to allow port {port}.")

    print(f"\n[3] Network URLs for your iPhone:")
    if lan_ip:
        print(f"    * Home Wi-Fi URL:      http://{lan_ip}:{port}")
        print(f"      (Works instantly if your iPhone is connected to the same Wi-Fi)")
    else:
        print(f"    * Wi-Fi: No local IPv4 network detected.")

    if ts_ip:
        print(f"    * Tailscale URL:        http://{ts_ip}:{port}")
        print(f"      (Works anywhere: cellular 5G, outside home, or any Wi-Fi)")
    else:
        print(f"    * Tailscale: Not connected on this laptop yet.")
        print(f"      (To enable Tailscale: open Tailscale on your laptop and log in)")

    print("\n" + "=" * 70)
    print("  Quick Setup Steps:")
    print("  1. If you haven't opened the firewall: run 'open_firewall_for_iphone.bat'.")
    print("  2. Start the studio: run 'start_studio.bat'.")
    print("  3. On your iPhone:")
    print("     - For home Wi-Fi: open Safari and go to: http://{}:{}".format(lan_ip or "192.168.0.101", port))
    print("     - For Tailscale (anywhere):")
    print("       a) Open Tailscale on laptop and sign in.")
    print("       b) Install 'Tailscale' from the iPhone App Store and sign in with the same account.")
    print("       c) In Safari on iPhone, open: http://<laptop-tailscale-ip>:{}".format(port))
    print("=" * 70)


if __name__ == "__main__":
    main()
