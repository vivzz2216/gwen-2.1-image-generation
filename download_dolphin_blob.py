"""Direct IPv4 blob downloader for Ollama dolphin-llama3 weights."""
import hashlib
import os
import socket
import sys
import time
import urllib.request
from pathlib import Path

# Force IPv4 resolution
_orig_getaddrinfo = socket.getaddrinfo
def ipv4_getaddrinfo(host, port, family=0, *args, **kwargs):
    return _orig_getaddrinfo(host, port, socket.AF_INET, *args, **kwargs)
socket.getaddrinfo = ipv4_getaddrinfo

BLOB_URL = (
    "https://dd20bb891979d25aebc8bec07b2b3bbc.r2.cloudflarestorage.com/ollama/docker/registry/v2/blobs/sha256/ea/"
    "ea025c107c1c3e5a87380b25e205db3b19a4d2ed56f1954f531573a963621580/data?"
    "X-Amz-Algorithm=AWS4-HMAC-SHA256&X-Amz-Credential=66040c77ac1b787c3af820529859349a%2F20261008%2Fauto%2Fs3%2Faws4_request&"
    "X-Amz-Date=20261008T194140Z&X-Amz-Expires=86400&X-Amz-SignedHeaders=host&"
    "X-Amz-Signature=0ed1f27f491defbd6b1db66104d06f0042d7b6072b98440489563f15f68edcf7"
)

TOTAL_SIZE = 4661222752
EXPECTED_SHA256 = "ea025c107c1c3e5a87380b25e205db3b19a4d2ed56f1954f531573a963621580"

BLOBS_DIR = Path(os.path.expanduser("~")) / ".ollama" / "models" / "blobs"
FINAL_PATH = BLOBS_DIR / f"sha256-{EXPECTED_SHA256}"
TEMP_PATH = BLOBS_DIR / f"sha256-{EXPECTED_SHA256}.downloading"


def main():
    BLOBS_DIR.mkdir(parents=True, exist_ok=True)
    if FINAL_PATH.exists() and FINAL_PATH.stat().st_size == TOTAL_SIZE:
        print(f"Final blob already exists: {FINAL_PATH} ({TOTAL_SIZE} bytes). Verifying checksum...", flush=True)
        h = hashlib.sha256()
        with open(FINAL_PATH, "rb") as f:
            while chunk := f.read(1024 * 1024 * 4):
                h.update(chunk)
        if h.hexdigest().lower() == EXPECTED_SHA256:
            print("Checksum verified OK! Blob is ready for Ollama.", flush=True)
            return 0
        print("Checksum mismatch, re-downloading...", flush=True)
        FINAL_PATH.unlink()

    existing = TEMP_PATH.stat().st_size if TEMP_PATH.exists() else 0
    if existing >= TOTAL_SIZE:
        existing = 0
        TEMP_PATH.unlink(missing_ok=True)

    mode = "ab" if existing > 0 else "wb"
    print(f"Starting IPv4 download from byte {existing} / {TOTAL_SIZE} ({existing / TOTAL_SIZE * 100:.1f}%)...", flush=True)

    retries = 0
    while existing < TOTAL_SIZE and retries < 10:
        try:
            headers = {"User-Agent": "ollama/0.4.0"}
            if existing > 0:
                headers["Range"] = f"bytes={existing}-{TOTAL_SIZE - 1}"
            req = urllib.request.Request(BLOB_URL, headers=headers)
            with urllib.request.urlopen(req, timeout=30) as resp, open(TEMP_PATH, mode) as out:
                last_log = time.monotonic()
                bytes_since_log = 0
                while True:
                    chunk = resp.read(1024 * 1024 * 2)  # 2MB chunks
                    if not chunk:
                        break
                    out.write(chunk)
                    existing += len(chunk)
                    bytes_since_log += len(chunk)
                    now = time.monotonic()
                    if now - last_log >= 5.0:
                        speed = (bytes_since_log / (1024 * 1024)) / (now - last_log)
                        pct = (existing / TOTAL_SIZE) * 100
                        remain_mb = (TOTAL_SIZE - existing) / (1024 * 1024)
                        eta_s = remain_mb / speed if speed > 0 else 0
                        print(f"Progress: {existing / 1024 / 1024:.1f} MB / {TOTAL_SIZE / 1024 / 1024:.1f} MB ({pct:.1f}%) | {speed:.2f} MB/s | ETA: {eta_s:.0f}s", flush=True)
                        last_log = now
                        bytes_since_log = 0
        except Exception as exc:
            retries += 1
            print(f"Network glitch ({exc}), retrying in 3s (retry {retries}/10)...", flush=True)
            time.sleep(3)
            mode = "ab"

    if existing < TOTAL_SIZE:
        print(f"Download failed after retries ({existing}/{TOTAL_SIZE} bytes).", flush=True)
        return 1

    print("Download finished. Verifying SHA256 checksum...", flush=True)
    h = hashlib.sha256()
    with open(TEMP_PATH, "rb") as f:
        while chunk := f.read(1024 * 1024 * 4):
            h.update(chunk)
    digest = h.hexdigest().lower()
    if digest != EXPECTED_SHA256:
        print(f"ERROR: Checksum mismatch! Got {digest}, expected {EXPECTED_SHA256}", flush=True)
        return 1

    print("Checksum verified! Moving to final blob path...", flush=True)
    if FINAL_PATH.exists():
        FINAL_PATH.unlink()
    TEMP_PATH.rename(FINAL_PATH)

    # Clean up any partial files
    for p in BLOBS_DIR.glob(f"sha256-{EXPECTED_SHA256}-partial*"):
        try:
            p.unlink()
        except OSError:
            pass

    print(f"SUCCESS: {FINAL_PATH} is ready for Ollama!", flush=True)
    return 0


if __name__ == "__main__":
    sys.exit(main())
