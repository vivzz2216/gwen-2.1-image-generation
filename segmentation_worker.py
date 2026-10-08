"""Isolated CPU SegFormer inference and explicit resumable model installation.

Model author: https://huggingface.co/mattmdjaga/segformer_b2_clothes
Only safetensors are loaded; no downloaded Python/model code is executed.
"""

from pathlib import Path
import argparse
import gc
import hashlib
import json
import os
import shutil
import subprocess
import sys

ROOT = Path(__file__).resolve().parent
sys.path.insert(0, str(ROOT))
MODEL_DIR = ROOT / "models" / "segformer_b2_clothes"
MODEL_ID = "mattmdjaga/segformer_b2_clothes"
# Pinned author-repository revision, verified against the model's public manifest.
MODEL_REVISION = "584abc1e1d260e23c0fc627c5217a09b2b461046"
DETECTOR_FILE = "ssdlite320_mobilenet_v3_large_coco-a79551df.pth"
DETECTOR_URL = "https://download.pytorch.org/models/" + DETECTOR_FILE


def install():
    revision = MODEL_REVISION
    MODEL_DIR.mkdir(parents=True, exist_ok=True)
    # Windows curl uses the OS trust/proxy settings, avoiding Python TLS/proxy
    # stalls on machines where browser downloads work but Python requests hang.
    curl = shutil.which("curl.exe") or shutil.which("curl")
    for filename in ("config.json", "preprocessor_config.json", "model.safetensors", "README.md"):
        destination = MODEL_DIR / filename
        if destination.exists() and (filename != "model.safetensors" or destination.stat().st_size == 109493236):
            continue
        print(f"Downloading {filename} from {MODEL_ID}@{revision}", flush=True)
        if curl:
            partial = destination.with_name(filename + ".part")
            if filename == "model.safetensors" and partial.exists() and partial.stat().st_size == 109493236:
                partial.replace(destination)
                continue
            command = [curl, "-f", "-L", "--connect-timeout", "15", "--max-time", "600", "--retry", "3", "-C", "-", "-o", str(partial), f"https://huggingface.co/{MODEL_ID}/resolve/{revision}/{filename}"]
            subprocess.run(command, check=True, timeout=650, creationflags=subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0)
            if filename == "model.safetensors" and partial.stat().st_size != 109493236:
                raise RuntimeError("Downloaded segmentation weights have an unexpected size; partial file retained for resume.")
            partial.replace(destination)
        else:
            from huggingface_hub import hf_hub_download
            hf_hub_download(MODEL_ID, filename, revision=revision, local_dir=str(MODEL_DIR))
    (MODEL_DIR / "installed.json").write_text(json.dumps({"model": MODEL_ID, "revision": revision, "source": "https://huggingface.co/" + MODEL_ID}, indent=2), encoding="utf-8")
    detector = MODEL_DIR / DETECTOR_FILE
    if not detector.exists():
        partial = detector.with_name(DETECTOR_FILE + ".part")
        if not partial.exists() or not hashlib.sha256(partial.read_bytes()).hexdigest().startswith("a79551df"):
            if not curl:
                raise RuntimeError("curl is required to install the local person detector.")
            print("Downloading official 13.4 MB CPU person detector", flush=True)
            subprocess.run([curl, "-f", "-L", "--connect-timeout", "15", "--max-time", "600", "--retry", "3", "-C", "-", "-o", str(partial), DETECTOR_URL], check=True, timeout=650)
        if not hashlib.sha256(partial.read_bytes()).hexdigest().startswith("a79551df"):
            raise RuntimeError("Person detector checksum does not match the official torchvision weights.")
        partial.replace(detector)
    print("Local CPU masking model installed.", flush=True)


def infer(source: str, destination: str):
    os.environ["CUDA_VISIBLE_DEVICES"] = ""
    import numpy as np
    from PIL import Image
    import torch
    from scipy import ndimage
    from transformers import SegformerImageProcessor, SegformerForSemanticSegmentation
    from torchvision.models.detection import ssdlite320_mobilenet_v3_large
    from torchvision.transforms.functional import pil_to_tensor
    from editing_regions import prepare_segmentation_input

    torch.set_num_threads(max(1, min(4, (os.cpu_count() or 4) // 2)))
    with Image.open(source) as opened:
        # Enforce the bound even when this worker is invoked directly, rather
        # than through the Studio's already-bounded parent input.
        image = prepare_segmentation_input(opened)
    detector = ssdlite320_mobilenet_v3_large(weights=None, weights_backbone=None, num_classes=91).to("cpu").eval()
    detector.load_state_dict(torch.load(MODEL_DIR / DETECTOR_FILE, map_location="cpu", weights_only=True))
    with torch.inference_mode():
        objects = detector([pil_to_tensor(image).float().div(255)])[0]
    accepted = (objects["labels"] == 1) & (objects["scores"] >= .55)
    boxes = objects["boxes"][accepted]
    person_scores = objects["scores"][accepted].tolist()
    areas = (boxes[:, 2] - boxes[:, 0]) * (boxes[:, 3] - boxes[:, 1])
    significant = (areas >= float(areas.max()) * .25) if areas.numel() else torch.zeros(0, dtype=torch.bool)
    person_count = int(significant.sum())
    del detector, objects
    gc.collect()
    folder = Path(destination)
    if person_count != 1:
        # Return detector diagnostics without loading another model when manual
        # selection is already required (missing or ambiguous subject).
        Image.new("L", image.size).save(folder / "labels.png")
        Image.new("L", image.size).save(folder / "confidence.png")
        (folder / "result.json").write_text(json.dumps({"face_count": 0, "person_count": person_count, "person_scores": person_scores, "device": "cpu", "model": MODEL_ID}), encoding="utf-8")
        return
    processor = SegformerImageProcessor.from_pretrained(str(MODEL_DIR), local_files_only=True)
    model = SegformerForSemanticSegmentation.from_pretrained(str(MODEL_DIR), local_files_only=True, use_safetensors=True).to("cpu").eval()
    inputs = processor(images=image, return_tensors="pt")
    with torch.inference_mode():
        logits = model(**inputs).logits.float()
        logits = torch.nn.functional.interpolate(logits, size=(image.height, image.width), mode="bilinear", align_corners=False)
        confidence, labels = logits.softmax(dim=1).max(dim=1)
        prediction = labels[0].numpy().astype(np.uint8)
        scores = confidence[0].numpy()
    connected, _ = ndimage.label((prediction == 11) & (scores >= .6))
    sizes = np.bincount(connected.ravel())[1:]
    threshold = max(25, (float(sizes.max()) * .25) if sizes.size else 25)
    face_count = int(np.count_nonzero(sizes >= threshold))
    Image.fromarray(prediction).save(folder / "labels.png")
    Image.fromarray((scores * 255).round().astype(np.uint8)).save(folder / "confidence.png")
    (folder / "result.json").write_text(json.dumps({"face_count": face_count, "person_count": person_count, "person_scores": person_scores, "device": "cpu", "model": MODEL_ID}), encoding="utf-8")


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("action", choices=("install", "infer"))
    parser.add_argument("source", nargs="?")
    parser.add_argument("destination", nargs="?")
    arguments = parser.parse_args()
    if arguments.action == "install":
        install()
    elif arguments.source and arguments.destination:
        infer(arguments.source, arguments.destination)
    else:
        parser.error("infer requires source image and destination directory")
