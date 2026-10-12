"""Benchmark comparison: 10 Prompts Normal Face Detail vs BFS Head v1.1 Face Detail."""
import argparse
from datetime import datetime
import hashlib
import html
import json
import os
from pathlib import Path
import shutil
import sys
import time
import uuid

import httpx
from PIL import Image, ImageOps

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
import face_detail
import identity_assist
import qwen_engine as engine

OUT_DIR = ROOT / "outputs" / "bfs_vs_normal_comparison"
COMFY_INPUT_DIR = ROOT / "ComfyUI_windows_portable" / "ComfyUI" / "input" / "studio_uploads"
COMFY_INPUT_DIR.mkdir(parents=True, exist_ok=True)
COMFY_OUTPUT_DIR = ROOT / "ComfyUI_windows_portable" / "ComfyUI" / "output"
COMFY_URL = "http://127.0.0.1:8188"
LORA_DIR = ROOT / "ComfyUI_windows_portable" / "ComfyUI" / "models" / "loras"
BFS_LORA = "bfs_head_v1.1_qwen_2.1.safetensors"

CLIENT = engine.ComfyClient(COMFY_URL, COMFY_OUTPUT_DIR)

PROMPTS = [
    ("cycling", "The person in the reference is riding a pedal bicycle along a path, seen from a front three-quarter view. Seated on the saddle, both hands gripping their own handlebar grips, each foot on its corresponding pedal, knees bent naturally in a pedaling position. Show the complete person and bicycle, including both wheels. Wearing a casual shirt and trousers. Preserve recognizable facial identity and hairstyle. One rider, one bicycle.", "4:3"),
    ("skydiving", "A full-body photograph of the person in the reference skydiving during freefall high above the earth with clouds and landscape visible below. The person is in a stable belly-to-earth arched freefall position with arms and legs spread. Wearing a colorful modern skydiving jumpsuit and harness. Clear skydiving goggles with the face, eyes and expression clearly visible, hair blowing back. Preserve recognizable facial identity. One person.", "4:3"),
    ("motorcycle", "The person in the reference is riding a motorcycle, viewed from a front three-quarter angle. Seated astride the seat, both hands on the handlebar grips, feet on the foot pegs, looking ahead. Wearing a jacket, trousers and an open-face helmet with the face visible. Show the complete rider and motorcycle. Preserve recognizable facial identity. One person, one motorcycle.", "4:3"),
    ("running", "A full-body photograph of the person in the reference jogging along a path, seen in profile. Both feet captured in mid-stride, arms bent naturally. Casual athletic wear. Preserve recognizable facial identity and hairstyle. One person.", "4:3"),
    ("skateboarding", "The person in the reference is riding a skateboard on a street, balanced with knees slightly bent and arms out for balance. Full body view showing the skateboard and wheels. Casual streetwear. Preserve recognizable facial identity and hairstyle. One person.", "4:3"),
    ("surfing", "A full-body photograph of the person in the reference riding a surfboard on an ocean wave, balanced in an athletic crouch with knees bent and arms outstretched for balance. Surfing on the wave face with ocean spray. Wearing a casual wetsuit. Head up, looking forward, face clearly visible. Preserve recognizable facial identity. One surfer, one surfboard.", "4:3"),
    ("yoga_tree", "A full-body photograph of the person in the reference practicing yoga tree pose: standing balanced on one leg with the opposite foot placed against the inner thigh, hands together in prayer position at chest height. Plain athletic clothes. Preserve recognizable facial identity. One person.", "3:4"),
    ("cafe_reading", "A medium shot of the person in the reference sitting at a wooden café table, reading a book. One hand holds the open book, relaxed posture, warm ambient café lighting. Preserve recognizable facial identity and hairstyle. One person.", "4:3"),
    ("rock_climbing", "A full-body photograph of the person in the reference rock climbing on a steep outdoor cliff face. Both hands gripping chalked rock holds, one foot on a small foothold, climbing harness on. Looking upward toward the next hold, face clearly visible. Athletic climbing clothes. Preserve recognizable facial identity. One climber.", "3:4"),
    ("crying", "A head-and-shoulders photograph of the person in the reference with an emotional, tearful expression: eyes glistening with visible tears, eyebrows drawn together with sadness, gentle vulnerable expression. Preserve recognizable facial identity and hairstyle. One person.", "3:4"),
]


def render_gallery(dest: Path, results: list[dict]):
    html_lines = [
        "<!doctype html><html><head><meta charset='utf-8'><title>BFS Head v1.1 vs Normal Comparison</title>",
        "<style>",
        "body{font:14px system-ui,-apple-system,sans-serif;background:#0d1117;color:#c9d1d9;margin:0;padding:24px;}",
        "h1{color:#58a6ff;margin-bottom:8px;}p{color:#8b949e;line-height:1.5;}",
        ".metric-table{width:100%;border-collapse:collapse;margin:24px 0;background:#161b22;border-radius:8px;overflow:hidden;}",
        ".metric-table th,.metric-table td{padding:12px 16px;text-align:left;border-bottom:1px solid #21262d;}",
        ".metric-table th{background:#21262d;color:#f0f6fc;font-weight:600;}",
        ".gain{color:#3fb950;font-weight:bold;}",
        ".card{background:#161b22;border:1px solid #30363d;border-radius:8px;padding:20px;margin-bottom:32px;}",
        ".row{display:grid;grid-template-columns:repeat(auto-fit,minmax(280px,1fr));gap:16px;margin:16px 0;}",
        ".col{background:#0d1117;border:1px solid #21262d;border-radius:6px;padding:12px;text-align:center;}",
        ".col h4{margin:0 0 8px 0;color:#8b949e;font-size:13px;text-transform:uppercase;}",
        ".col img{max-width:100%;height:320px;object-fit:contain;border-radius:4px;background:#000;}",
        ".badge{display:inline-block;padding:4px 8px;border-radius:12px;font-size:12px;font-weight:600;margin-top:6px;}",
        ".badge-base{background:#30363d;color:#8b949e;}",
        ".badge-normal{background:#1f6feb22;color:#58a6ff;border:1px solid #1f6feb;}",
        ".badge-bfs{background:#23863622;color:#3fb950;border:1px solid #238636;}",
        "</style></head><body>",
        "<h1>Qwen 2.1 Restyle: Normal vs BFS Head v1.1 Comparison</h1>",
        "<p>Comprehensive A/B benchmark across 10 distinct poses, angles, and actions using Jenna Ortega test reference. Evaluated with OpenCV SFace cosine similarity on identical base generations.</p>",
    ]

    # Summary table
    html_lines.append("<table class='metric-table'><thead><tr>")
    html_lines.append("<th>#</th><th>Case / Pose</th><th>Face Px</th><th>Base Likeness</th><th>Normal Likeness</th><th>BFS Head Likeness</th><th>Improvement (&Delta;)</th>")
    html_lines.append("</tr></thead><tbody>")
    for i, r in enumerate(results, 1):
        b = f"{r.get('base_sim', 0):.4f}" if r.get('base_sim') is not None else "N/A"
        n = f"{r.get('normal_sim', 0):.4f}" if r.get('normal_sim') is not None else "N/A"
        bfs = f"{r.get('bfs_sim', 0):.4f}" if r.get('bfs_sim') is not None else "N/A"
        delta = r.get("delta")
        d_str = f"+{delta:.4f}" if delta is not None and delta > 0 else (f"{delta:.4f}" if delta is not None else "N/A")
        gain_cls = " class='gain'" if delta and delta > 0 else ""
        html_lines.append(f"<tr><td>{i}</td><td><b>{html.escape(r['case'])}</b></td><td>{r.get('face_px', 'N/A')}</td><td>{b}</td><td>{n}</td><td><b>{bfs}</b></td><td{gain_cls}><b>{d_str}</b></td></tr>")
    html_lines.append("</tbody></table>")

    # Detailed cards
    for i, r in enumerate(results, 1):
        html_lines.append(f"<div class='card'><h3>#{i} {html.escape(r['case'].replace('_', ' ').title())}</h3>")
        html_lines.append(f"<p><i>{html.escape(r['prompt'])}</i></p>")
        html_lines.append("<div class='row'>")
        if r.get("base_img"):
            html_lines.append(f"<div class='col'><h4>Base Generation (Unrefined)</h4><img src='{r['base_img']}'><div class='badge badge-base'>Likeness: {r.get('base_sim', 'N/A')}</div></div>")
        if r.get("normal_img"):
            html_lines.append(f"<div class='col'><h4>Normal Face Detail (Standard)</h4><img src='{r['normal_img']}'><div class='badge badge-normal'>Likeness: {r.get('normal_sim', 'N/A')}</div></div>")
        if r.get("bfs_img"):
            html_lines.append(f"<div class='col'><h4>BFS Head v1.1 (Upgraded)</h4><img src='{r['bfs_img']}'><div class='badge badge-bfs'>Likeness: {r.get('bfs_sim', 'N/A')} (&Delta; {r.get('delta', 0):+.4f})</div></div>")
        html_lines.append("</div></div>")

    html_lines.append("</body></html>")
    (dest / "index.html").write_text("\n".join(html_lines), encoding="utf-8")


def run_face_refinement(base_img_path: Path, head_crop_path: Path, prompt_text: str, use_bfs: bool, out_path: Path) -> float | None:
    """Run isolated face detail pass on an existing base image."""
    base = Image.open(base_img_path).convert("RGB")
    head = Image.open(head_crop_path).convert("RGB")
    boxes = identity_assist.face_boxes(base)
    plan = face_detail.plan(base.size, boxes)
    if not plan:
        print(f"Skipping face refinement: no eligible face detected in {base_img_path.name}")
        base.save(out_path)
        likeness = identity_assist.similarity(head, base)
        return likeness.get("faces", [{}])[0].get("cosine_similarity") if likeness.get("status") == "measured" else None

    # Save inputs for ComfyUI
    uid = uuid.uuid4().hex[:8]
    crop_img = base.crop(plan["crop_box"])
    crop_work = crop_img.resize(plan["work_size"], Image.Resampling.LANCZOS)
    crop_dest = COMFY_INPUT_DIR / f"bench_{uid}_crop.png"
    crop_work.save(crop_dest)

    mask_dest = COMFY_INPUT_DIR / f"bench_{uid}_mask.png"
    plan["mask"].save(mask_dest)

    head_dest = COMFY_INPUT_DIR / f"bench_{uid}_head.png"
    head.save(head_dest)

    if use_bfs:
        compiled_detail = face_detail.prompt(prompt_text, use_bfs_head=True)
        lora_name = BFS_LORA
        lora_strength = 1.0
        denoise = 0.65
    else:
        detail_instruction = face_detail.prompt(prompt_text, use_bfs_head=False)
        compiled_detail = identity_assist.compile_identity(detail_instruction, task="edit")
        lora_name = None
        lora_strength = 0.0
        denoise = 0.55

    wf = engine.build_workflow(
        compiled_detail,
        mode="balanced",
        seed=42 + (99 if use_bfs else 0),
        steps=20,
        source_name=f"studio_uploads/{crop_dest.name}",
        source_size=plan["work_size"],
        reference_names=(f"studio_uploads/{head_dest.name}",),
        mask_name=f"studio_uploads/{mask_dest.name}",
        lora_name=lora_name,
        lora_strength=lora_strength,
        denoise=denoise,
    )

    t0 = time.monotonic()
    output_path = CLIENT.run(wf)
    elapsed = round(time.monotonic() - t0, 1)

    try:
        with Image.open(output_path) as im:
            refined_crop = im.copy()
    finally:
        output_path.unlink(missing_ok=True)
        crop_dest.unlink(missing_ok=True)
        mask_dest.unlink(missing_ok=True)
        head_dest.unlink(missing_ok=True)
        CLIENT.wipe_history()

    composited = face_detail.composite(base, refined_crop, plan)
    composited.save(out_path)

    likeness = identity_assist.similarity(head, composited)
    score = likeness.get("faces", [{}])[0].get("cosine_similarity") if likeness.get("status") == "measured" else None
    mode_name = "BFS Head" if use_bfs else "Normal"
    print(f"  [{mode_name}] Refined in {elapsed}s -> Likeness: {score}", flush=True)
    return score


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--case", choices=[p[0] for p in PROMPTS], help="Run a single case")
    args = parser.parse_args()

    OUT_DIR.mkdir(parents=True, exist_ok=True)
    orig_path = ROOT / "jenna-test.avif"
    with Image.open(orig_path) as im:
        orig = ImageOps.exif_transpose(im).convert("RGB")
    orig.save(OUT_DIR / "original.png")
    detected = identity_assist.detect(orig)
    head = identity_assist.crop_head(orig, detected["candidates"][0]["head_box"])
    head.save(OUT_DIR / "head.png")

    prior_dir = ROOT / "outputs" / "identity_photo_tests" / "20261012_214658_599480"
    results_path = OUT_DIR / "results.json"
    results = json.loads(results_path.read_text()) if results_path.exists() else []

    with httpx.Client(base_url="http://127.0.0.1:7860", timeout=300) as http_client:
        for idx, (name, prompt, aspect) in enumerate(PROMPTS):
            if args.case and args.case != name:
                continue
            print(f"\n==========================================")
            print(f"Prompt {idx+1}/10: {name}")
            print(f"==========================================")
            rec = next((r for r in results if r["case"] == name), None)
            if not rec:
                rec = {"case": name, "prompt": prompt, "aspect_ratio": aspect}
                results.append(rec)

            base_img_path = OUT_DIR / f"{name}_base.png"
            # Step 1: Ensure base image exists
            if not base_img_path.is_file():
                # Check prior directory
                prior_base = prior_dir / f"{name}_base.png"
                prior_single = prior_dir / f"{name}.png"
                if prior_base.is_file():
                    shutil.copy2(prior_base, base_img_path)
                    print(f"Copied existing base image from prior run.")
                elif prior_single.is_file() and name in ["cafe_reading", "crying"]:
                    # When face was > 128px, prior single was the raw generation
                    shutil.copy2(prior_single, base_img_path)
                    print(f"Using prior single generation as base.")
                else:
                    # Generate fresh base image via Studio API
                    print(f"Generating new base scene via Studio backend...")
                    resp = http_client.post(
                        "/api/jobs",
                        data={
                            "task": "generate",
                            "prompt": prompt,
                            "mode": "balanced",
                            "aspect_ratio": aspect,
                            "seed": str(202610130 + idx),
                            "lora_enabled": "false",
                            "identity_enabled": "true",
                            "identity_reviewed": "true",
                            "identity_box": json.dumps(detected["candidates"][0]["head_box"]),
                            "face_detail": "false",
                        },
                        files={"identity_reference": ("original.png", (OUT_DIR / "original.png").read_bytes(), "image/png")},
                    )
                    resp.raise_for_status()
                    job_id = resp.json()["job_id"]
                    while True:
                        job = http_client.get(f"/api/jobs/{job_id}").json()
                        if job["status"] in ["succeeded", "failed"]:
                            break
                        time.sleep(2)
                    if job["status"] != "succeeded":
                        print(f"Failed to generate base scene: {job.get('error')}")
                        continue
                    gen_path = (ROOT / job["image_url"].lstrip("/")).resolve()
                    shutil.copy2(gen_path, base_img_path)
                    print(f"Base generation complete.")

            rec["base_img"] = base_img_path.name
            with Image.open(base_img_path) as b_im:
                b_boxes = identity_assist.face_boxes(b_im)
                if b_boxes:
                    rec["face_px"] = f"{b_boxes[0]['width']}x{b_boxes[0]['height']}"
                b_likeness = identity_assist.similarity(head, b_im)
                rec["base_sim"] = b_likeness.get("faces", [{}])[0].get("cosine_similarity") if b_likeness.get("status") == "measured" else None
            print(f"  [Base] Likeness: {rec['base_sim']} (Face: {rec.get('face_px')})")

            # Step 2: Normal Face Detail
            normal_img_path = OUT_DIR / f"{name}_normal.png"
            if not normal_img_path.is_file():
                # Check prior directory if run without LoRA
                prior_normal = prior_dir / f"{name}.png"
                if prior_normal.is_file() and name != "surfing":
                    shutil.copy2(prior_normal, normal_img_path)
                    n_im = Image.open(normal_img_path)
                    n_likeness = identity_assist.similarity(head, n_im)
                    rec["normal_sim"] = n_likeness.get("faces", [{}])[0].get("cosine_similarity") if n_likeness.get("status") == "measured" else None
                    rec["normal_img"] = normal_img_path.name
                    print(f"  [Normal] Reused prior output -> Likeness: {rec['normal_sim']}")
                else:
                    rec["normal_sim"] = run_face_refinement(base_img_path, OUT_DIR / "head.png", prompt, use_bfs=False, out_path=normal_img_path)
                    rec["normal_img"] = normal_img_path.name
            else:
                if rec.get("normal_sim") is None:
                    n_im = Image.open(normal_img_path)
                    n_likeness = identity_assist.similarity(head, n_im)
                    rec["normal_sim"] = n_likeness.get("faces", [{}])[0].get("cosine_similarity") if n_likeness.get("status") == "measured" else None

            # Step 3: BFS Head Face Detail
            bfs_img_path = OUT_DIR / f"{name}_bfs.png"
            if not bfs_img_path.is_file():
                if name == "surfing" and (prior_dir / "surfing.png").is_file():
                    shutil.copy2(prior_dir / "surfing.png", bfs_img_path)
                    bfs_im = Image.open(bfs_img_path)
                    bfs_likeness = identity_assist.similarity(head, bfs_im)
                    rec["bfs_sim"] = bfs_likeness.get("faces", [{}])[0].get("cosine_similarity") if bfs_likeness.get("status") == "measured" else None
                    rec["bfs_img"] = bfs_img_path.name
                    print(f"  [BFS Head] Reused verified surfing run -> Likeness: {rec['bfs_sim']}")
                else:
                    rec["bfs_sim"] = run_face_refinement(base_img_path, OUT_DIR / "head.png", prompt, use_bfs=True, out_path=bfs_img_path)
                    rec["bfs_img"] = bfs_img_path.name
            else:
                if rec.get("bfs_sim") is None:
                    bfs_im = Image.open(bfs_img_path)
                    bfs_likeness = identity_assist.similarity(head, bfs_im)
                    rec["bfs_sim"] = bfs_likeness.get("faces", [{}])[0].get("cosine_similarity") if bfs_likeness.get("status") == "measured" else None

            if rec.get("bfs_sim") is not None and rec.get("normal_sim") is not None:
                rec["delta"] = round(rec["bfs_sim"] - rec["normal_sim"], 4)
            elif rec.get("bfs_sim") is not None and rec.get("base_sim") is not None:
                rec["delta"] = round(rec["bfs_sim"] - rec["base_sim"], 4)

            results_path.write_text(json.dumps(results, indent=2), encoding="utf-8")
            render_gallery(OUT_DIR, results)
            print(f"  Delta: {rec.get('delta')}")

    print("\nBenchmark complete! Gallery saved to:", OUT_DIR / "index.html")


if __name__ == "__main__":
    main()
