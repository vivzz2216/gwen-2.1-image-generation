import argparse
import json
import shutil
import sys
import time
import uuid
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from test_comfy_qwen import OUTPUT_DIR, request_json


ROOT = Path(__file__).resolve().parent
COMFY_DIR = ROOT / "ComfyUI_windows_portable" / "ComfyUI"
INPUT_DIR = COMFY_DIR / "input"


def edit_workflow(image_name: str, prompt: str, steps: int, seed: int):
    return {
        "1": {"class_type": "LoadImage", "inputs": {"image": image_name}},
        "2": {
            "class_type": "UNETLoader",
            "inputs": {
                "unet_name": "qwen_image_2.1_int8_convrot.safetensors",
                "weight_dtype": "default",
            },
        },
        "3": {
            "class_type": "QwenImage21Cache",
            "inputs": {"model": ["2", 0], "device": "off", "dtype": "default"},
        },
        "4": {
            "class_type": "CLIPLoader",
            "inputs": {
                "clip_name": "qwen3vl_8b_int8_convrot.safetensors",
                "type": "qwen_image",
                "device": "cpu",
            },
        },
        "5": {
            "class_type": "VAELoader",
            "inputs": {"vae_name": "qwen_image_2.1_vae_bf16.safetensors"},
        },
        "6": {
            "class_type": "TextEncodeQwenImage21",
            "inputs": {
                "clip": ["4", 0],
                "images.image_1": ["1", 0],
                "vae": ["5", 0],
                "prompt": prompt,
                "negative_prompt": "",
                "resolution": 512,
            },
        },
        "7": {
            "class_type": "KSampler",
            "inputs": {
                "model": ["3", 0],
                "positive": ["6", 0],
                "negative": ["6", 1],
                "latent_image": ["6", 2],
                "seed": seed,
                "steps": steps,
                "cfg": 1.0,
                "sampler_name": "euler",
                "scheduler": "simple",
                "denoise": 1.0,
            },
        },
        "8": {
            "class_type": "VAEDecode",
            "inputs": {"samples": ["7", 0], "vae": ["5", 0]},
        },
        "9": {
            "class_type": "SaveImage",
            "inputs": {"images": ["8", 0], "filename_prefix": "Qwen21_edit_test"},
        },
    }


def run(source: Path, prompt: str, steps: int = 20, seed: int = 2718):
    INPUT_DIR.mkdir(parents=True, exist_ok=True)
    input_name = f"qwen_edit_input{source.suffix.lower()}"
    destination = INPUT_DIR / input_name
    shutil.copy2(source, destination)

    submitted = request_json(
        "/prompt",
        "POST",
        {
            "prompt": edit_workflow(input_name, prompt, steps, seed),
            "client_id": str(uuid.uuid4()),
        },
    )
    prompt_id = submitted["prompt_id"]
    print(f"Queued Qwen edit {prompt_id}", flush=True)
    started = time.monotonic()
    while True:
        history = request_json(f"/history/{prompt_id}")
        if prompt_id in history:
            record = history[prompt_id]
            status = record.get("status", {})
            if status.get("status_str") == "error" or not status.get("completed", False):
                raise RuntimeError(json.dumps(status, indent=2))
            images = record.get("outputs", {}).get("9", {}).get("images", [])
            if not images:
                raise RuntimeError("Edit completed without an output image")
            output = OUTPUT_DIR / images[0].get("subfolder", "") / images[0]["filename"]
            print(f"Completed in {time.monotonic() - started:.1f}s: {output}", flush=True)
            return output
        print(f"Qwen edit running ({time.monotonic() - started:.0f}s)", flush=True)
        time.sleep(15)


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Test Qwen-Image-2.1 image+prompt editing")
    parser.add_argument("source", type=Path)
    parser.add_argument("prompt")
    parser.add_argument("--steps", type=int, default=20)
    parser.add_argument("--seed", type=int, default=2718)
    args = parser.parse_args()
    print(run(args.source.resolve(), args.prompt, args.steps, args.seed))
