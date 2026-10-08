from pathlib import Path

from huggingface_hub import snapshot_download


ROOT = Path(__file__).resolve().parent
MODEL_DIR = ROOT / "ComfyUI_windows_portable" / "ComfyUI" / "models"

CORE_FILES = [
    "diffusion_models/qwen_image_2.1_int8_convrot.safetensors",
    "text_encoders/qwen3vl_8b_int8_convrot.safetensors",
    "vae/qwen_image_2.1_vae_bf16.safetensors",
]


if __name__ == "__main__":
    MODEL_DIR.mkdir(parents=True, exist_ok=True)
    print(f"Downloading official Qwen-Image-2.1 assets to {MODEL_DIR}", flush=True)
    snapshot_download(
        repo_id="Comfy-Org/Qwen-Image-2.1",
        allow_patterns=CORE_FILES,
        local_dir=MODEL_DIR,
        max_workers=3,
    )
    print("Core Qwen-Image-2.1 model download complete.", flush=True)
