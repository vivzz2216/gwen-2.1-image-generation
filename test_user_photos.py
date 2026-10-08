"""Run reviewed edits against the user's input_test photos, keeping originals intact."""
import argparse
import io
import json
import shutil
import time
from pathlib import Path

import httpx
import numpy as np
from PIL import Image, ImageOps

ROOT = Path(__file__).resolve().parent
SUITE = ROOT / "outputs" / "photo_tests"
CASES = [
    ("jenna-ortega-style-1677484079.avif", "black_suit", "outfit", "balanced", "Restyle only the existing gown as a tailored black evening-suit look, with a crisp opaque white collar detail worked into the existing neckline. Keep the original gown coverage and silhouette. Preserve the person's face, hair, exposed skin, pose, hands, and original background."),
    ("jenna-ortega-style-1677484079.avif", "editorial_style", "global", "balanced", "Restyle this image as an elegant hand-painted watercolor fashion illustration, with delicate ink outlines and a soft pastel color palette. Keep the same recognizable person, expression, pose, clothing design and composition."),
    ("jenna-test.webp", "emerald_dress", "outfit", "ultra", "Replace only the white dress with an elegant emerald-green satin evening dress with opaque fabric and the same overall silhouette. Keep the face, hair, hands, legs, pose and festival backdrop unchanged."),
    ("jenna-test.webp", "sunglasses", "eyewear", "ultra", "Add realistic slim black cat-eye sunglasses covering the eyes. Keep the original expression, head angle, facial identity and every other detail unchanged."),
    ("madison_test.jpg", "blue_top", "top", "balanced", "Change only the white top into an opaque royal-blue satin top with the same asymmetric neckline and overall silhouette. Preserve the face, hair, exposed skin, body shape, phone, hands, jewelry, jeans and background."),
    ("madison_test.jpg", "gold_necklace", "necklace", "ultra", "Add a fine delicate gold necklace with a small round gold pendant around the neck, with realistic reflections matching the room lighting. Preserve the face, hair, pose, phone, hands, clothes and background."),
    ("madison_test2.jpg", "red_top", "top", "balanced", "Replace only the white top with an opaque burgundy-red satin camisole, retaining the original neckline and fit. Keep the same face, hair, hands, jewelry, pose, lighting and background."),
    ("madison_test2.jpg", "sunglasses", "eyewear", "ultra", "Add realistic black aviator sunglasses over the eyes, correctly aligned to the existing head angle. Preserve the person's identity, mouth, expression, hair, jewelry, clothes and background."),
    ("madison_test3.jpg", "black_blouse", "outfit", "ultra", "Change only the patterned top to solid black silk, retaining the exact same cut, straps, coverage, and fit. Keep the original face, hair, pose, earrings, exposed skin outside the clothing and room background unchanged."),
    ("madison_test3.jpg", "auburn_hair", "hair", "balanced", "Change only the hair color to a natural warm auburn shade. Keep the exact hairstyle, ponytail silhouette, hair texture, face, expression, earrings, clothes, lighting and background unchanged."),
]


def initial_manifest():
    SUITE.mkdir(parents=True, exist_ok=True)
    manifest = []
    for index, (filename, label, region, mode, prompt) in enumerate(CASES):
        folder = SUITE / f"{index + 1:02d}_{Path(filename).stem}_{label}"
        folder.mkdir(exist_ok=True)
        with Image.open(ROOT / "assets" / "input_test" / filename) as im:
            original = ImageOps.exif_transpose(im).convert("RGB")
            original.save(folder / "original.png")
        manifest.append({"index": index + 1, "filename": filename, "label": label, "region": region,
                         "mode": mode, "prompt": prompt, "folder": str(folder.relative_to(ROOT)), "status": "pending"})
    return manifest


def write_report(manifest):
    (SUITE / "report.json").write_text(json.dumps(manifest, indent=2), encoding="utf-8")


def prepare():
    report = SUITE / "report.json"
    manifest = json.loads(report.read_text(encoding="utf-8")) if report.exists() else initial_manifest()
    with httpx.Client(base_url="http://127.0.0.1:7860", timeout=300, trust_env=False) as client:
        for case in manifest:
            folder = ROOT / case["folder"]
            if case["region"] == "global" or (folder / "mask.png").exists():
                continue
            print("Mask proposal", case["index"], case["label"], flush=True)
            with (folder / "original.png").open("rb") as source:
                response = client.post("/api/mask", data={"prompt": case["prompt"], "region": case["region"]},
                                       files={"image": ("original.png", source, "image/png")})
            if response.status_code != 200:
                case.update(status="mask_failed", error=response.text)
                print("MASK FAILED", case["index"], response.text, flush=True)
                write_report(manifest)
                continue
            proposal = response.json()
            mask_data = client.get(proposal["mask_url"]).raise_for_status().content
            (folder / "mask.png").write_bytes(mask_data)
            case["mask_proposal"] = proposal
            case["status"] = "needs_mask_review"
            with Image.open(folder / "original.png") as source, Image.open(io.BytesIO(mask_data)) as mask:
                overlay = source.convert("RGBA")
                color = Image.new("RGBA", source.size, (40, 230, 160, 0))
                color.putalpha(mask.convert("L").point(lambda p: 95 if p >= 128 else 0))
                Image.alpha_composite(overlay, color).save(folder / "mask_preview.png")
            write_report(manifest)
    write_report(manifest)
    print("Proposals ready for visual review in", SUITE, flush=True)


def run(selected=None):
    manifest = json.loads((SUITE / "report.json").read_text(encoding="utf-8"))
    with httpx.Client(base_url="http://127.0.0.1:7860", timeout=300, trust_env=False) as client:
        for case in manifest:
            if selected and case["index"] not in selected:
                continue
            if case["status"] == "succeeded" or case["status"] == "mask_failed":
                continue
            folder = ROOT / case["folder"]
            protected = case["region"] != "global"
            if protected and not case.get("mask_reviewed"):
                raise RuntimeError(f"Case {case['index']} mask must be reviewed before generation")
            files = {"image": ("original.png", (folder / "original.png").read_bytes(), "image/png")}
            if protected:
                files["mask"] = ("mask.png", (folder / "mask.png").read_bytes(), "image/png")
            response = client.post("/api/jobs", data={"task": "edit", "mode": case["mode"], "prompt": case["prompt"],
                 "region": case["region"], "preserve": str(protected).lower(), "seed": str(9700 + case["index"])}, files=files)
            if response.status_code != 202:
                case.update(status="failed", error=response.text)
                write_report(manifest)
                continue
            job_id = response.json()["job_id"]
            case.update(status="running", job_id=job_id)
            write_report(manifest)
            print("TEST", case["index"], case["filename"], case["label"], case["mode"], job_id, flush=True)
            previous = None
            while True:
                job = client.get(f"/api/jobs/{job_id}").raise_for_status().json()
                if job["phase"] != previous:
                    print(case["index"], job["phase"], job["elapsed_sec"], flush=True)
                    previous = job["phase"]
                if job["status"] in ("succeeded", "failed", "cancelled"):
                    break
                time.sleep(4)
            case.update(status=job["status"], job=job)
            if job["status"] == "succeeded":
                output = ROOT / job["image_url"].lstrip("/")
                shutil.copy2(output, folder / "result.png")
                if protected:
                    with Image.open(folder / "original.png") as im:
                        original = np.array(im)
                    with Image.open(output) as im:
                        edited = np.array(im)
                    with Image.open(folder / "mask.png") as im:
                        outside = np.array(im.convert("L")) < 128
                    case["protected_pixels_unchanged"] = bool(np.array_equal(original[outside], edited[outside]))
                    case["protected_pixels"] = int(outside.sum())
                    case["selected_pixels_changed"] = int(np.any(original != edited, axis=-1)[~outside].sum())
                    if not case["protected_pixels_unchanged"] or not case["selected_pixels_changed"]:
                        raise AssertionError(f"Case {case['index']} preservation/edit check failed")
                compare(folder)
            write_report(manifest)
            print("RESULT", case["index"], case["status"], job["elapsed_sec"], flush=True)


def compare(folder):
    from PIL import ImageDraw
    thumbs = []
    for name in ("original.png", "result.png"):
        with Image.open(folder / name) as im:
            thumb = im.convert("RGB")
            thumb.thumbnail((500, 650))
            thumbs.append(thumb)
    sheet = Image.new("RGB", (1040, 700), (18, 22, 28))
    draw = ImageDraw.Draw(sheet)
    for i, thumb in enumerate(thumbs):
        draw.text((20 + i * 520, 12), "Original" if i == 0 else "Edited", fill="white")
        sheet.paste(thumb, (20 + i * 520, 42))
    sheet.save(folder / "comparison.jpg", quality=93)


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("action", choices=("prepare", "run"))
    parser.add_argument("--cases", nargs="*", type=int)
    args = parser.parse_args()
    prepare() if args.action == "prepare" else run(args.cases)
