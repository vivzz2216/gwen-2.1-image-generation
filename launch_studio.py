"""Start/reuse the bundled local Qwen backend and the Studio on Windows."""
import os
import subprocess
import sys
import threading
import time
import urllib.request
import webbrowser
from pathlib import Path

ROOT = Path(__file__).resolve().parent
PORTABLE = ROOT / "ComfyUI_windows_portable"


def reachable(url):
    try:
        with urllib.request.urlopen(url, timeout=2) as response:
            return response.status == 200
    except OSError:
        return False


def main():
    print("[1/2] Checking ComfyUI backend (http://127.0.0.1:8188)...", flush=True)
    if not reachable("http://127.0.0.1:8188/system_stats"):
        python = PORTABLE / "python_embeded" / "python.exe"
        models = PORTABLE / "ComfyUI" / "models"
        required = [
            "diffusion_models/qwen_image_2.1_int8_convrot.safetensors",
            "text_encoders/qwen3vl_8b_int8_convrot.safetensors",
            "vae/qwen_image_2.1_vae_bf16.safetensors"
        ]
        if not python.exists() or any(not (models / name).exists() for name in required):
            raise RuntimeError("The bundled Qwen/ComfyUI installation is missing. Restore its models before starting.")
        print("Starting local Qwen Image 2.1 on the NVIDIA GPU. First startup can take several minutes...", flush=True)
        with (PORTABLE / "qwen_comfyui.stdout.log").open("a") as stdout, (PORTABLE / "qwen_comfyui.stderr.log").open("a") as stderr:
            process = subprocess.Popen([
                str(python), "-s", "ComfyUI/main.py", "--windows-standalone-build",
                "--listen", "127.0.0.1", "--port", "8188", "--lowvram", "--cpu-vae", "--disable-metadata",
                "--preview-method", "none", "--reserve-vram", "1.0"
            ], cwd=PORTABLE, stdout=stdout, stderr=stderr, creationflags=subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0)
        deadline = time.monotonic() + 300
        while not reachable("http://127.0.0.1:8188/system_stats"):
            if process.poll() is not None:
                raise RuntimeError("ComfyUI stopped during startup. Read ComfyUI_windows_portable/qwen_comfyui.stderr.log.")
            if time.monotonic() > deadline:
                raise RuntimeError("ComfyUI is taking longer than five minutes. Check its log, then rerun this launcher.")
            time.sleep(2)
    print("[1/2] Qwen backend is ready: http://localhost:8188", flush=True)

    if reachable("http://127.0.0.1:7860/api/capabilities"):
        print("[2/2] Studio is already running: http://localhost:7860", flush=True)
        print("Opening browser at http://localhost:7860...", flush=True)
        webbrowser.open("http://localhost:7860")
        return

    print("[2/2] Starting Studio web server on http://localhost:7860...", flush=True)
    print("Opening browser at http://localhost:7860. Keep this window open; Ctrl+C stops the Studio.", flush=True)

    def open_browser():
        time.sleep(1.5)
        try:
            webbrowser.open("http://localhost:7860")
        except Exception:
            pass

    threading.Thread(target=open_browser, daemon=True).start()
    subprocess.run([sys.executable, "-m", "uvicorn", "studio_app:app", "--host", "127.0.0.1", "--port", "7860"], cwd=ROOT)


if __name__ == "__main__":
    try:
        main()
    except (OSError, RuntimeError) as exc:
        print(f"Startup failed: {exc}", file=sys.stderr)
        sys.exit(1)
