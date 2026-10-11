"""Qwen Image 2.1 Studio - Native Windows Desktop Application.

Provides a dedicated, frameless/standalone Windows desktop app window powered by
Microsoft Edge WebView2, integrated process management, hardware monitoring,
and clean window lifecycle management.
"""
import argparse
import atexit
import json
import logging
import os
import shutil
import subprocess
import sys
import threading
import time
import urllib.request
from pathlib import Path
from dolphin_prompting import PROFILE_VERSION

ROOT = Path(__file__).resolve().parent
PORTABLE = ROOT / "ComfyUI_windows_portable"
ASSETS_DIR = ROOT / "assets"
STATE_DIR = ROOT / "outputs" / ".studio"
STATE_DIR.mkdir(parents=True, exist_ok=True)
LOG_FILE = STATE_DIR / "desktop_app.log"
ICON_ICO = ASSETS_DIR / "qwen_studio.ico" if (ASSETS_DIR / "qwen_studio.ico").exists() else ASSETS_DIR / "app_icon.ico"
ICON_PNG = ASSETS_DIR / "qwen_icon.png" if (ASSETS_DIR / "qwen_icon.png").exists() else ASSETS_DIR / "app_icon.png"


logging.basicConfig(
    filename=str(LOG_FILE),
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S"
)
logger = logging.getLogger("DesktopApp")

# Set Windows Taskbar AppUserModelID so taskbar icon and grouping match Qwen Studio
if os.name == "nt":
    try:
        import ctypes
        app_id = "Qwen.ImageStudio.Desktop.2.1"
        ctypes.windll.shell32.SetCurrentProcessExplicitAppUserModelID(app_id)
    except Exception as exc:
        logger.debug(f"Could not set AppUserModelID: {exc}")


def reachable(url: str, timeout: float = 2.0) -> bool:
    """Return True if the given HTTP endpoint responds successfully."""
    try:
        with urllib.request.urlopen(url, timeout=timeout) as resp:
            return resp.status in (200, 202)
    except OSError:
        return False


class DesktopAppManager:
    def __init__(self, port: int = 7860, host: str = "127.0.0.1", force_edge: bool = False):
        self.port = port
        self.host = host
        self.force_edge = force_edge
        self.studio_proc = None
        self.comfy_proc = None
        self.ollama_proc = None
        self.base_url = f"http://{host}:{port}"
        atexit.register(self.shutdown)

    def ensure_ollama(self):
        """Ensure Ollama service is reachable if installed on the host."""
        if reachable("http://127.0.0.1:11434/api/tags", timeout=1.0):
            logger.info("Ollama is already running.")
            return

        ollama_path = shutil.which("ollama")
        if not ollama_path:
            candidate = Path(os.environ.get("LOCALAPPDATA", "")) / "Programs" / "Ollama" / "ollama.exe"
            if candidate.exists():
                ollama_path = str(candidate)

        if ollama_path and Path(ollama_path).exists():
            logger.info(f"Starting Ollama from {ollama_path}")
            flags = subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0
            self.ollama_proc = subprocess.Popen([ollama_path, "serve"], creationflags=flags)
            for _ in range(12):
                if reachable("http://127.0.0.1:11434/api/tags", timeout=0.8):
                    logger.info("Ollama enhancer service ready.")
                    return
                time.sleep(0.5)

    def ensure_comfyui(self):
        """Ensure ComfyUI generation engine is running."""
        if reachable("http://127.0.0.1:8188/system_stats", timeout=1.5):
            logger.info("ComfyUI engine is already running.")
            return

        python_exe = PORTABLE / "python_embeded" / "python.exe"
        models_dir = PORTABLE / "ComfyUI" / "models"
        required_weights = [
            "diffusion_models/qwen_image_2.1_int8_convrot.safetensors",
            "text_encoders/qwen3vl_8b_int8_convrot.safetensors",
            "vae/qwen_image_2.1_vae_bf16.safetensors"
        ]

        if not python_exe.exists() or any(not (models_dir / req).exists() for req in required_weights):
            logger.warning("Bundled ComfyUI models missing or not portable installation.")
            return

        logger.info("Launching bundled ComfyUI backend in background...")
        flags = subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0
        stdout_log = (PORTABLE / "qwen_comfyui.stdout.log").open("a")
        stderr_log = (PORTABLE / "qwen_comfyui.stderr.log").open("a")
        comfy_args = [
            str(python_exe), "-s", "ComfyUI/main.py", "--windows-standalone-build",
            "--listen", "127.0.0.1", "--port", "8188", "--lowvram", "--disable-metadata",
            "--preview-method", "none", "--reserve-vram", "1.0"
        ]
        if os.getenv("QWEN_CPU_VAE") == "1":
            comfy_args.append("--cpu-vae")

        self.comfy_proc = subprocess.Popen(
            comfy_args, cwd=PORTABLE, stdout=stdout_log, stderr=stderr_log, creationflags=flags
        )

    def ensure_studio_backend(self):
        """Ensure the FastAPI/Uvicorn backend is running."""
        if reachable(f"{self.base_url}/api/capabilities", timeout=1.5):
            with urllib.request.urlopen(f"{self.base_url}/api/capabilities", timeout=3) as response:
                info = json.loads(response.read().decode("utf-8"))
            if info.get("enhancer", {}).get("profile") != PROFILE_VERSION:
                raise RuntimeError(
                    "An older Studio backend is still running on this port. "
                    "Finish its image jobs, stop its Studio server, then reopen the desktop shortcut. "
                    "Refreshing the page alone cannot activate the action and body-part checks."
                )
            logger.info("Studio backend is already up.")
            return

        logger.info(f"Starting Studio backend on {self.base_url}...")
        venv_py = ROOT / ".venv" / "Scripts" / "python.exe"
        py_exe = str(venv_py) if venv_py.exists() else sys.executable

        flags = subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0
        stdout_log = (STATE_DIR / "studio_backend.stdout.log").open("a", encoding="utf-8")
        stderr_log = (STATE_DIR / "studio_backend.stderr.log").open("a", encoding="utf-8")
        self.studio_proc = subprocess.Popen(
            [py_exe, "-m", "uvicorn", "studio_app:app", "--host", self.host, "--port", str(self.port)],
            cwd=ROOT,
            stdout=stdout_log,
            stderr=stderr_log,
            creationflags=flags
        )

        deadline = time.monotonic() + 30
        while time.monotonic() < deadline:
            if reachable(f"{self.base_url}/api/session/status", timeout=1.5) or reachable(f"{self.base_url}/api/capabilities", timeout=1.5):
                logger.info("Studio backend is ready.")
                return
            if self.studio_proc.poll() is not None:
                raise RuntimeError("Studio backend exited unexpectedly during startup. Check outputs/.studio/studio_backend.stderr.log.")
            time.sleep(0.5)

        raise TimeoutError("Studio backend did not respond within 30 seconds.")

    def run_edge_app(self) -> bool:
        """Launch via Microsoft Edge standalone App mode (dedicated window with no browser chrome)."""
        edge_candidates = [
            r"C:\Program Files (x86)\Microsoft\Edge\Application\msedge.exe",
            r"C:\Program Files\Microsoft\Edge\Application\msedge.exe",
            shutil.which("msedge") or ""
        ]
        edge_path = next((p for p in edge_candidates if p and Path(p).exists()), None)
        if not edge_path:
            logger.warning("Microsoft Edge not found for app mode.")
            return False

        user_data = STATE_DIR / "edge_desktop_profile"
        user_data.mkdir(parents=True, exist_ok=True)

        cmd = [
            edge_path,
            f"--app={self.base_url}",
            f"--user-data-dir={user_data}",
            "--window-size=1440,900",
            "--window-position=80,60",
            "--app-id=QwenImageStudio",
            "--disable-features=TranslateUI",
            "--no-first-run",
            "--no-default-browser-check"
        ]
        logger.info(f"Launching Edge Standalone App: {' '.join(cmd)}")
        proc = subprocess.Popen(cmd)
        proc.wait()
        return True

    def run_webview_app(self) -> bool:
        """Launch native desktop window using pywebview (Edge WebView2 Chromium runtime)."""
        try:
            import webview
        except ImportError:
            logger.info("pywebview not installed, using Edge app fallback.")
            return False

        logger.info("Creating native desktop window via pywebview...")
        storage_path = str(STATE_DIR / "webview_cache")

        window = webview.create_window(
            title="Qwen Image 2.1 Studio",
            url=self.base_url,
            width=1440,
            height=900,
            min_size=(1024, 680),
            background_color="#090b10",
            text_select=True,
            zoomable=True
        )

        icon_arg = str(ICON_ICO) if ICON_ICO.exists() else None

        try:
            webview.start(
                gui="edgechromium",
                icon=icon_arg,
                storage_path=storage_path,
                private_mode=False
            )
            return True
        except Exception as exc:
            logger.warning(f"pywebview window failed: {exc}. Falling back to Edge App mode.")
            return False

    def launch(self):
        """Start all services and open the native Windows desktop app window."""
        logger.info("Starting Qwen Image Studio Desktop Application...")
        
        # 1. Start background services
        threading.Thread(target=self.ensure_ollama, daemon=True).start()
        threading.Thread(target=self.ensure_comfyui, daemon=True).start()
        self.ensure_studio_backend()

        # 2. Launch native desktop application window
        launched = False
        if not self.force_edge:
            launched = self.run_webview_app()

        if not launched:
            launched = self.run_edge_app()

        if not launched:
            # Fallback to default browser
            import webbrowser
            logger.info("Opening default browser...")
            webbrowser.open(self.base_url)
            # Keep process alive until user aborts
            try:
                while True:
                    time.sleep(1)
            except KeyboardInterrupt:
                pass

        self.shutdown()

    def shutdown(self):
        """Cleanly terminate backend subprocesses."""
        logger.info("Shutting down Desktop Application...")
        if self.studio_proc and self.studio_proc.poll() is None:
            logger.info("Terminating Studio backend process...")
            try:
                self.studio_proc.terminate()
                self.studio_proc.wait(timeout=3)
            except Exception:
                try:
                    self.studio_proc.kill()
                except Exception:
                    pass
            self.studio_proc = None


def main():
    parser = argparse.ArgumentParser(description="Qwen Image 2.1 Studio Windows Desktop App")
    parser.add_argument("--port", type=int, default=7860, help="Studio web port (default: 7860)")
    parser.add_argument("--edge", action="store_true", help="Force Microsoft Edge Standalone App mode")
    args = parser.parse_args()

    app = DesktopAppManager(port=args.port, force_edge=args.edge)
    try:
        app.launch()
    except Exception as exc:
        logger.exception("Fatal error in desktop app:")
        if os.name == "nt":
            import ctypes
            ctypes.windll.user32.MessageBoxW(
                0,
                f"An error occurred while launching Qwen Image Studio:\n\n{exc}",
                "Qwen Image 2.1 Studio Error",
                0x10  # MB_ICONERROR
            )
        sys.exit(1)


if __name__ == "__main__":
    main()
