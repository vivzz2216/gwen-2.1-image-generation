"""Check adding an absent accessory with a reviewed mask and focused Ultra crop."""
import json
from pathlib import Path
import httpx
import numpy as np
from PIL import Image
from verify_studio_live import wait

ROOT = Path(__file__).resolve().parent


def main():
    source = ROOT / "assets" / "xbox_gamer.jpg"
    with httpx.Client(base_url="http://127.0.0.1:7860", timeout=300, trust_env=False) as client:
        with source.open("rb") as photo:
            proposal = client.post("/api/mask", data={"prompt": "Add black sunglasses to me", "region": "eyewear"},
                                  files={"image": (source.name, photo, "image/jpeg")}).raise_for_status().json()
        assert proposal["method"] == "estimated_face_anchor"
        print("Accessory placement warning:", proposal["warnings"], flush=True)
        original_path = ROOT / proposal["source_url"].lstrip("/")
        mask_path = ROOT / proposal["mask_url"].lstrip("/")
        with original_path.open("rb") as photo, mask_path.open("rb") as mask:
            job = client.post("/api/jobs", data={"task": "edit", "prompt": "Add realistic black sunglasses to the person's eyes. Keep the same head angle and identity; preserve everything else.",
                    "region": "eyewear", "preserve": "true", "mode": "ultra", "seed": "471"},
                files={"image": ("original.png", photo, "image/png"), "mask": ("mask.png", mask, "image/png")}).raise_for_status().json()
        job = wait(client, job)
    with Image.open(original_path) as im:
        original = np.array(im)
    with Image.open(ROOT / job["image_url"].lstrip("/")) as im:
        edited = np.array(im)
    with Image.open(mask_path) as im:
        outside = np.array(im.convert("L")) < 128
    assert np.array_equal(original[outside], edited[outside])
    assert np.any(original[~outside] != edited[~outside])
    (ROOT / "outputs" / "accessory_verification.json").write_text(json.dumps(job, indent=2), encoding="utf-8")
    print("Accessory exterior preservation PASS", job["image_url"], job["elapsed_sec"], flush=True)


if __name__ == "__main__":
    main()
