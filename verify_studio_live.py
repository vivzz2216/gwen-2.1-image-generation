"""Hardware QA for three real modes, RGBA, and precise source preservation."""
import json
import argparse
import time
from pathlib import Path
import httpx
import numpy as np
from PIL import Image

ROOT = Path(__file__).resolve().parent


def wait(client, job):
    job_id = job["job_id"]
    previous = None
    while True:
        job = client.get(f"/api/jobs/{job_id}").raise_for_status().json()
        if job["phase"] != previous:
            print(job_id[:8], job["phase"], job["elapsed_sec"], flush=True)
            previous = job["phase"]
        if job["status"] in ("succeeded", "failed", "cancelled"):
            if job["status"] != "succeeded":
                raise RuntimeError(job)
            return job
        time.sleep(3)


def main(edits_only=False):
    report = []
    with httpx.Client(base_url="http://127.0.0.1:7860", timeout=300, trust_env=False) as client:
        for mode in ("fast", "balanced", "ultra"):
            if edits_only:
                items = client.get("/api/history").raise_for_status().json()["items"]
                previous = next((job for job in items if job.get("mode") == mode and job.get("status") == "succeeded"
                                 and job.get("request", {}).get("task") == "generate"), None)
                if previous is None:
                    raise RuntimeError(f"No completed {mode} test to resume from")
                report.append(previous)
                continue
            data = {"task": "generate", "mode": mode,
                    "prompt": "A polished copper robot watering a yellow sunflower in a terracotta pot, warm studio photograph, precise mechanical details",
                    "seed": "31415", "aspect_ratio": "1:1"}
            if mode == "fast":
                data.update(prompt="A cute red panda sticker, isolated subject, transparent background, RGBA image with alpha channel", is_transparent="true")
            job = wait(client, client.post("/api/jobs", data=data).raise_for_status().json())
            output = ROOT / job["result"]["image_url"].lstrip("/")
            with Image.open(output) as im:
                print(mode, im.size, im.mode, "seconds", job["elapsed_sec"], flush=True)
                assert im.size == ((512, 512) if mode == "fast" else (768, 768) if mode == "balanced" else (1024, 1024))
                if mode == "fast":
                    assert im.mode == "RGBA" and im.getchannel("A").getextrema()[0] < 255, "No generated transparency"
                im.verify()
            assert client.get(f"/api/jobs/{job['job_id']}/workflow").status_code == 200
            report.append(job)
        source = ROOT / "assets" / "xbox_gamer.jpg"
        with source.open("rb") as handle:
            mask_data = client.post("/api/mask", data={"prompt": "Change my outfit to a black suit", "region": "outfit"},
                                    files={"image": (source.name, handle, "image/jpeg")}).raise_for_status().json()
        print("Auto mask", mask_data, flush=True)
        mask_path = ROOT / mask_data["mask_url"].lstrip("/")
        source_path = ROOT / mask_data["source_url"].lstrip("/")
        for mode in ("balanced", "ultra"):
            with source_path.open("rb") as src, mask_path.open("rb") as mask:
                submitted = client.post("/api/jobs", data={"task": "edit", "mode": mode,
                    "prompt": "Change only my outfit to a tailored black suit. Keep my face, hands, pose, controller, and background exactly the same.",
                    "preserve": "true", "region": "outfit", "seed": "927"},
                    files={"image": ("source.png", src, "image/png"), "mask": ("mask.png", mask, "image/png")}).raise_for_status().json()
            job = wait(client, submitted)
            output = ROOT / job["result"]["image_url"].lstrip("/")
            with Image.open(source_path) as im:
                original = np.array(im)
            with Image.open(output) as im:
                edited = np.array(im)
            with Image.open(mask_path) as im:
                protected = np.array(im.convert("L")) < 128
            assert original.shape == edited.shape
            assert np.array_equal(original[protected], edited[protected]), "Protected pixels changed"
            assert np.any(original[~protected] != edited[~protected]), "Selected region was not edited"
            print(mode, "masked edit exact exterior preservation PASS", job["elapsed_sec"], flush=True)
            report.append(job)
    (ROOT / "outputs" / "verification_report.json").write_text(json.dumps(report, indent=2), encoding="utf-8")


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--edits-only", action="store_true", help="Reuse completed mode tests and rerun editing QA")
    main(parser.parse_args().edits_only)
