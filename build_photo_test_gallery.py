"""Build a local HTML gallery and JSON summary for the photo edit test run."""
import html
import json
from pathlib import Path

ROOT = Path(__file__).resolve().parent
SUITE = ROOT / "outputs" / "photo_tests"


def main():
    manifest = json.loads((SUITE / "report.json").read_text(encoding="utf-8"))
    cases = []
    for case in manifest:
        folder = ROOT / case["folder"]
        artifacts = {}
        for key, filename in (("original", "original.png"), ("mask", "mask.png"),
                              ("result", "result.png"), ("comparison", "comparison.jpg")):
            path = folder / filename
            if path.exists():
                artifacts[key] = path.relative_to(SUITE).as_posix()
        for key, filename in (("retry_result", "result_retry.png"),
                              ("retry_comparison", "comparison_retry.jpg"),
                              ("first_attempt", "result_attempt1.png"),
                              ("first_mask", "mask_attempt1.png")):
            path = folder / filename
            if path.exists():
                artifacts[key] = path.relative_to(SUITE).as_posix()
        job = case.get("job", {})
        cases.append({
            "index": case["index"], "label": case["label"], "source": case["filename"],
            "region": case["region"], "mode": case["mode"], "prompt": case["prompt"],
            "status": case.get("status", "pending"),
            "elapsed_sec": job.get("elapsed_sec"),
            "retry_elapsed_sec": case.get("retry_job", {}).get("elapsed_sec"),
            "protected_pixels_unchanged": case.get("protected_pixels_unchanged"),
            "selected_pixels_changed": case.get("selected_pixels_changed"),
            "mask_review_notes": case.get("mask_review_notes"),
            "error": case.get("error") or job.get("error"),
            "visual_review": case.get("visual_review", "pending"),
            "visual_notes": case.get("visual_notes"),
            "retry_status": case.get("retry_status"),
            "retry_protected_pixels_unchanged": case.get("retry_protected_pixels_unchanged"),
            "retry_selected_pixels_changed": case.get("retry_selected_pixels_changed"),
            "artifacts": artifacts,
        })
    summary = {"project": "Local Qwen Image 2.1 photo edit tests", "cases": cases,
               "counts": {
                   "job_status": {status: sum(1 for c in cases if c["status"] == status)
                                  for status in sorted({c["status"] for c in cases})},
                   "visual_review": {status: sum(1 for c in cases if c["visual_review"] == status)
                                     for status in sorted({c["visual_review"] for c in cases})},
               }}
    (SUITE / "gallery.json").write_text(json.dumps(summary, indent=2), encoding="utf-8")

    cards = []
    for c in cases:
        artifacts = c["artifacts"]
        image = artifacts.get("comparison") or artifacts.get("original")
        src = f'<img src="{html.escape(image)}" alt="Original and edited comparison">' if image else "<p>No image output yet.</p>"
        retry = (f'<p>Retry</p><img src="{html.escape(artifacts["retry_comparison"])}" alt="Retry comparison">'
                 if "retry_comparison" in artifacts else "")
        links = " · ".join(f'<a href="{html.escape(path)}">{html.escape(name)}</a>'
                           for name, path in artifacts.items())
        preserve = c["protected_pixels_unchanged"]
        pixel_text = ("Protected pixels exact" if preserve is True else
                      "Protected pixels changed" if preserve is False else "Pixel check: n/a")
        notes = c["visual_notes"] or c["mask_review_notes"] or ""
        elapsed = f'{c["elapsed_sec"]} s' if c["elapsed_sec"] is not None else "—"
        if c["retry_elapsed_sec"] is not None:
            elapsed += f' + retry {c["retry_elapsed_sec"]} s'
        cards.append(f'''<article class="card"><div class="preview">{src}{retry}</div>
<div class="details"><div class="eyebrow">{c['index']:02d} · {html.escape(c['mode'])} · {html.escape(c['region'])}</div>
<h2>{html.escape(c['label'].replace('_', ' ').title())}</h2>
<p class="meta">{html.escape(c['source'])} · {elapsed} · {html.escape(c['status'])}</p>
<p>{html.escape(pixel_text)} · visual review: {html.escape(c['visual_review'])}</p>
<details><summary>Prompt and notes</summary><p>{html.escape(c['prompt'])}</p><p>{html.escape(notes)}</p></details>
<nav>{links}</nav></div></article>''')
    document = '''<!doctype html><html lang="en"><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<title>Qwen Image 2.1 · Photo edit tests</title><style>
*{box-sizing:border-box}body{margin:0;background:#101217;color:#e9eaf0;font:15px/1.55 system-ui,Segoe UI,sans-serif}
header{padding:28px max(22px,calc((100vw - 1120px)/2));background:#191c24;border-bottom:1px solid #30333d}
h1{margin:0;font-size:25px}header p{margin:6px 0 0;color:#a9adba}.grid{max-width:1120px;margin:24px auto;padding:0 16px;display:grid;gap:16px}
.card{display:grid;grid-template-columns:minmax(260px,500px) 1fr;background:#191c24;border:1px solid #30333d;border-radius:14px;overflow:hidden}
.preview{background:#0e1015;display:flex;align-items:center}.preview img{display:block;width:100%;height:auto}.details{padding:18px 20px}
.eyebrow,.meta{color:#a9adba;font-size:13px}.eyebrow{text-transform:uppercase;letter-spacing:.08em}h2{margin:4px 0;font-size:21px}
nav{margin-top:12px}a{color:#9fc7ff;text-decoration:none}a:hover{text-decoration:underline}summary{cursor:pointer;color:#cbd8ed}
@media(max-width:700px){.card{grid-template-columns:1fr}.details{padding:16px}}
</style><header><h1>Qwen Image 2.1 · Photo edit tests</h1><p>Local ComfyUI run · generation status, preservation checks, masks and before/after results</p></header>
<main class="grid">''' + "\n".join(cards) + "</main></html>"
    (SUITE / "index.html").write_text(document, encoding="utf-8")
    print("Wrote", SUITE / "index.html")
    print("Wrote", SUITE / "gallery.json")


if __name__ == "__main__":
    main()
