import argparse
import json
import sys
import time
import urllib.error
import urllib.request
import uuid
from pathlib import Path


SERVER = "http://127.0.0.1:8188"
ROOT = Path(__file__).resolve().parent
OUTPUT_DIR = ROOT / "ComfyUI_windows_portable" / "ComfyUI" / "output"


def request_json(path: str, method: str = "GET", payload: dict | None = None):
    data = None if payload is None else json.dumps(payload).encode("utf-8")
    request = urllib.request.Request(
        SERVER + path,
        data=data,
        method=method,
        headers={"Content-Type": "application/json"},
    )
    with urllib.request.urlopen(request, timeout=30) as response:
        return json.loads(response.read().decode("utf-8"))


def t2i_workflow(prompt: str, steps: int, width: int, height: int, seed: int):
    return {
        "1": {
            "class_type": "UNETLoader",
            "inputs": {
                "unet_name": "qwen_image_2.1_int8_convrot.safetensors",
                "weight_dtype": "default",
            },
        },
        "2": {
            "class_type": "QwenImage21Cache",
            "inputs": {"model": ["1", 0], "device": "off", "dtype": "default"},
        },
        "3": {
            "class_type": "CLIPLoader",
            "inputs": {
                "clip_name": "qwen3vl_8b_int8_convrot.safetensors",
                "type": "qwen_image",
                "device": "cpu",
            },
        },
        "4": {
            "class_type": "VAELoader",
            "inputs": {"vae_name": "qwen_image_2.1_vae_bf16.safetensors"},
        },
        "5": {
            "class_type": "TextEncodeQwenImage21",
            "inputs": {
                "clip": ["3", 0],
                "prompt": prompt,
                "negative_prompt": "",
                "resolution": max(width, height),
            },
        },
        "6": {
            "class_type": "EmptyLatentImage",
            "inputs": {"width": width, "height": height, "batch_size": 1},
        },
        "7": {
            "class_type": "KSampler",
            "inputs": {
                "model": ["2", 0],
                "positive": ["5", 0],
                "negative": ["5", 1],
                "latent_image": ["6", 0],
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
            "inputs": {"samples": ["7", 0], "vae": ["4", 0]},
        },
        "9": {
            "class_type": "SaveImage",
            "inputs": {"images": ["8", 0], "filename_prefix": "Qwen21_test"},
        },
    }


def run(prompt: str, steps: int = 4, width: int = 512, height: int = 512, seed: int = 42):
    client_id = str(uuid.uuid4())
    submitted = request_json(
        "/prompt",
        "POST",
        {"prompt": t2i_workflow(prompt, steps, width, height, seed), "client_id": client_id},
    )
    prompt_id = submitted["prompt_id"]
    print(f"Queued Qwen prompt {prompt_id}", flush=True)

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
                raise RuntimeError("Generation completed without an output image")
            output = OUTPUT_DIR / images[0].get("subfolder", "") / images[0]["filename"]
            print(f"Completed in {time.monotonic() - started:.1f}s: {output}", flush=True)
            return output
        print(f"Qwen generation running ({time.monotonic() - started:.0f}s)", flush=True)
        time.sleep(15)


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Run a local Qwen-Image-2.1 ComfyUI test")
    parser.add_argument("prompt", nargs="?", default="A small copper robot watering a sunflower, studio photograph")
    parser.add_argument("--steps", type=int, default=4)
    parser.add_argument("--width", type=int, default=512)
    parser.add_argument("--height", type=int, default=512)
    parser.add_argument("--seed", type=int, default=42)
    args = parser.parse_args()
    print(run(args.prompt, args.steps, args.width, args.height, args.seed))
