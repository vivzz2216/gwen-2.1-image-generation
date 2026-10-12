"""Reviewed, resumable local Identity Assist tests for an uploaded photo (AVIF included)."""
import argparse
from datetime import datetime
import hashlib
import html
import json
from pathlib import Path
import shutil
import subprocess
import sys
import threading
import time

import httpx
from PIL import Image, ImageOps
ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
import identity_assist

CASES = [
    ('smile', 'balanced', 'A head-and-shoulders photograph of the person in the identity references. Change the expression to a natural smile. Keep the recognizable facial identity, hairstyle and source clothing. One person. No added text.'),
    ('holding_cup', 'balanced', 'A waist-up photograph of the person in the identity references holding a plain white cup in the right hand at chest height. The right elbow is bent, with the hand supporting the cup. Keep the recognizable facial identity, hairstyle and source clothing. One person. No added text.'),
    ('raised_arm', 'fast', 'A full-body photograph of the person in the identity references raising the right arm above the head, with both feet on the ground. Keep the recognizable facial identity, hairstyle and source clothing. One person. No added text.'),
    ('bench_red_swimsuit_juice', 'balanced', 'The person in the identity references is sitting on a bench, wearing a red swimsuit and drinking juice. Preserve the recognizable facial identity and hairstyle. Show a seated pose supported by the bench and the actual drinking moment, with the juice vessel at the lips rather than merely held. The outfit must be a red swimsuit, replacing the original outfit. One person.'),
    ('cycling', 'balanced', 'The person in the reference is riding a pedal bicycle along a path, seen from a front three-quarter view. Seated on the saddle, both hands gripping their own handlebar grips, each foot on its corresponding pedal, knees bent naturally in a pedaling position. Show the complete person and bicycle, including both wheels. Wearing a casual shirt and trousers. Preserve recognizable facial identity and hairstyle. One rider, one bicycle.'),
    ('motorcycle', 'balanced', 'The person in the reference is riding a motorcycle, viewed from a front three-quarter angle. Seated astride the seat, both hands on the handlebar grips, feet on the foot pegs, looking ahead. Wearing a jacket, trousers and an open-face helmet with the face visible. Show the complete rider and motorcycle. Preserve recognizable facial identity. One person, one motorcycle.'),
    ('sleeping', 'balanced', 'The person in the reference is sleeping on their side in a bed, eyes fully closed and face relaxed. The head rests on a pillow, one hand rests beside the cheek, the body lies horizontally under a blanket with shoulders visible in a plain sleep shirt. Preserve recognizable facial identity and hairstyle. One person. Show the head, pillow and reclining upper body.'),
    ('laughing', 'balanced', 'A head-and-shoulders photograph of the person in the reference laughing naturally: mouth open with teeth visible, cheeks lifted, eyes gently narrowed with joy. Preserve recognizable facial identity, hairstyle and source clothing. One person.'),
    ('surprised', 'balanced', 'A head-and-shoulders photograph of the person in the reference with a clearly surprised expression: eyebrows raised, eyes widened, lips slightly parted. Preserve recognizable facial identity, hairstyle and source clothing. One person.'),
    ('running', 'balanced', 'A full-body photograph of the person in the reference jogging along a path, seen in profile. Both feet captured in mid-stride, arms bent naturally. Casual athletic wear. Preserve recognizable facial identity and hairstyle. One person.'),
    ('skateboarding', 'balanced', 'The person in the reference is riding a skateboard on a street, balanced with knees slightly bent and arms out for balance. Full body view showing the skateboard and wheels. Casual streetwear. Preserve recognizable facial identity and hairstyle. One person.'),
    ('yoga_tree', 'balanced', 'A full-body photograph of the person in the reference practicing yoga tree pose: standing balanced on one leg with the opposite foot placed against the inner thigh, hands together in prayer position at chest height. Plain athletic clothes. Preserve recognizable facial identity. One person.'),
    ('cafe_reading', 'balanced', 'A medium shot of the person in the reference sitting at a wooden café table, reading a book. One hand holds the open book, relaxed posture, warm ambient café lighting. Preserve recognizable facial identity and hairstyle. One person.'),
    ('crying', 'balanced', 'A head-and-shoulders photograph of the person in the reference with an emotional, tearful expression: eyes glistening with visible tears, eyebrows drawn together with sadness, gentle vulnerable expression. Preserve recognizable facial identity and hairstyle. One person.'),
    ('winking', 'balanced', 'A head-and-shoulders photograph of the person in the reference giving a playful wink: one eye closed in a clear deliberate wink, the other eye open with a slight smile. Preserve recognizable facial identity, hairstyle and source clothing. One person.'),
    ('angry', 'balanced', 'A head-and-shoulders photograph of the person in the reference with a stern, angry expression: furrowed brows, intense focused gaze, tightened lips. Preserve recognizable facial identity and hairstyle. One person.'),
]
ACTION_CASES = {'cycling', 'motorcycle', 'sleeping', 'laughing', 'surprised'}
POSES2_CASES = {'cycling', 'motorcycle', 'running', 'skateboarding', 'yoga_tree', 'cafe_reading', 'crying', 'winking', 'angry'}

def gallery(dest, rows, source_name):
    body = ['<!doctype html><html lang="en"><meta charset="utf-8"><meta name="viewport" content="width=device-width"><title>Local identity photo tests</title>',
        '<style>body{font:16px system-ui;margin:24px;background:#171717;color:#eee}a{color:#c1bcff}img{max-width:100%;max-height:520px;object-fit:contain}.pair{display:flex;gap:16px;flex-wrap:wrap}.pair a{max-width:32%}section{margin:32px 0}pre{white-space:pre-wrap}</style>',
        '<h1>'+html.escape(source_name)+' — local Identity Assist tests</h1>',
        '<p>Original file unchanged. AVIF is decoded into a separate PNG for Studio. All generation is local Qwen 2.1/ComfyUI, no LoRA. Face similarity is advisory, not proof of identity or action correctness.</p>',
        '<p><a href="original.png">Normalized original</a> · <a href="head.png">Reviewed head crop</a> · <a href="context.json">Input provenance</a> · <a href="results.json">JSON results</a></p>']
    for row in rows:
        body.append('<section><h2>'+html.escape(row['case'])+'</h2><p>'+html.escape(row.get('visual_status','not_reviewed'))+'</p>')
        body.append('<div class="pair"><a href="original.png"><img src="original.png" alt="Normalized original reference"></a>')
        if row.get('base_image'): body.append('<a href="'+row['base_image']+'"><img src="'+row['base_image']+'" alt="'+html.escape(row['case'])+' unsharpened base"></a>')
        if row.get('image'): body.append('<a href="'+row['image']+'"><img src="'+row['image']+'" alt="'+html.escape(row['case'])+' output"></a>')
        body.append('</div><p>'+html.escape(row['prompt'])+'</p><pre>'+html.escape(json.dumps({k:row.get(k) for k in ['mode','elapsed_sec','sampled_peak_device_vram_mib','visual_notes','error','face_detail']},indent=2))+'</pre></section>')
    body.append('</html>')
    (dest/'index.html').write_text('\n'.join(body),encoding='utf-8')

def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--input',type=Path); parser.add_argument('--resume',type=Path)
    parser.add_argument('--prepare',action='store_true'); parser.add_argument('--approved-head',action='store_true')
    parser.add_argument('--case',choices=[row[0] for row in CASES]); parser.add_argument('--render-report',action='store_true')
    parser.add_argument('--suite', choices=['actions', 'poses2'])
    parser.add_argument('--quiet', action='store_true')
    parser.add_argument('--face-detail', action='store_true', default=True)
    args=parser.parse_args()
    if not args.resume and not args.input: parser.error('Provide an input photo or existing run to resume')
    dest=args.resume.resolve() if args.resume else ROOT/'outputs'/'identity_photo_tests'/datetime.now().strftime('%Y%m%d_%H%M%S_%f')
    if not dest.is_relative_to((ROOT/'outputs'/'identity_photo_tests').resolve()): raise ValueError('Run directory must remain inside outputs/identity_photo_tests')
    dest.mkdir(parents=True,exist_ok=True); context=dest/'context.json'
    if not context.exists():
        original=args.input.resolve(); source_hash=hashlib.sha256(original.read_bytes()).hexdigest()
        with Image.open(original) as image:
            if getattr(image,'n_frames',1)!=1: raise ValueError('Choose one still photo; animated AVIF needs explicit frame selection')
            source=ImageOps.exif_transpose(image).convert('RGB'); source_format=image.format
        detected=identity_assist.detect(source)
        if len(detected['candidates'])!=1: raise ValueError('Select the intended face explicitly; this single-face test runner will not guess')
        box=detected['candidates'][0]['head_box']; source.save(dest/'original.png'); identity_assist.crop_head(source,box).save(dest/'head.png')
        context.write_text(json.dumps({'source':str(original),'source_name':original.name,'source_format':source_format,
            'source_sha256':source_hash,'normalization':'EXIF orientation applied; RGB PNG copy; source file never modified',
            'dimensions':list(source.size),'head_box':box,'head_reviewed':False,'detector':detected},indent=2),encoding='utf-8')
    config=json.loads(context.read_text()); report=dest/'results.json'; rows=json.loads(report.read_text()) if report.exists() else []
    if hashlib.sha256(Path(config['source']).read_bytes()).hexdigest()!=config['source_sha256']: raise ValueError('Original file changed; start a new run rather than overwrite results')
    if args.render_report: gallery(dest,rows,config['source_name']); print(dest); return
    if args.prepare: print(dest,flush=True); return
    if not args.approved_head: raise ValueError('Inspect original.png and head.png, then use --approved-head')
    config['head_reviewed']=True; context.write_text(json.dumps(config,indent=2),encoding='utf-8')
    with httpx.Client(base_url='http://127.0.0.1:7860',timeout=240) as client:
        for index,(name,mode,prompt) in enumerate(CASES):
            if args.case and args.case!=name: continue
            if args.suite == 'actions' and name not in ACTION_CASES: continue
            if args.suite == 'poses2' and name not in POSES2_CASES: continue
            row=next((row for row in rows if row['case']==name),None)
            if row and (row.get('image') or row.get('error')): continue
            if not row:
                queue=httpx.get('http://127.0.0.1:8188/queue',timeout=10).json()
                if queue.get('queue_running') or queue.get('queue_pending'):
                    print('ComfyUI is busy; resume later without interrupting other jobs.',flush=True); break
                row={'case':name,'mode':mode,'prompt':prompt,'seed':202610127+index,'lora_enabled':False,
                     'aspect_ratio':'4:3' if name in {'cycling','motorcycle','sleeping','running','skateboarding','cafe_reading'} else '3:4','visual_status':'not_reviewed'}; rows.append(row)
            samples=[]; stop=threading.Event()
            def monitor():
                while not stop.is_set():
                    try:
                        process=subprocess.run(['nvidia-smi','--query-gpu=memory.used','--format=csv,noheader,nounits'],capture_output=True,text=True,timeout=4,creationflags=getattr(subprocess,'CREATE_NO_WINDOW',0))
                        samples.append(int(process.stdout.strip().splitlines()[0]))
                    except Exception: pass
                    stop.wait(1)
            thread=threading.Thread(target=monitor,daemon=True); thread.start()
            try:
                if not row.get('job_id'):
                    response=client.post('/api/jobs',data={'task':'generate','prompt':prompt,'mode':mode,'aspect_ratio':row.get('aspect_ratio','3:4'),
                        'seed':str(row['seed']),'lora_enabled':'false','identity_enabled':'true','identity_reviewed':'true',
                        'identity_box':json.dumps(config['head_box']),'face_detail':'true' if args.face_detail else 'false'},
                        files={'identity_reference':('original.png',(dest/'original.png').read_bytes(),'image/png')})
                    response.raise_for_status(); row['job_id']=response.json()['job_id']; report.write_text(json.dumps(rows,indent=2),encoding='utf-8')
                    print('Started '+name+': '+row['job_id'],flush=True)
                deadline=time.monotonic()+1200; previous=None
                while time.monotonic()<deadline:
                    response=client.get('/api/jobs/'+row['job_id']); response.raise_for_status(); job=response.json()
                    if job['status'] in ['succeeded','failed','cancelled']: break
                    if not args.quiet:
                        if job.get('phase')!=previous: previous=job.get('phase'); print(name+': '+str(previous),flush=True)
                    else:
                        if job.get('phase') in ['Sharpening face', 'Checking face likeness on CPU'] and job.get('phase') != previous:
                            previous = job.get('phase'); print(name+': '+str(previous), flush=True)
                    time.sleep(2)
                else: raise TimeoutError('Job is still active; resume to collect it, do not submit a duplicate')
                if job['status']!='succeeded': raise RuntimeError(job.get('error') or job['status'])
                row['metadata']=job.get('metadata',{}); row['elapsed_sec']=job.get('elapsed_sec')
                fd = row['metadata'].get('face_detail', {})
                row['face_detail'] = fd
                if fd.get('base_image_url'):
                    base_src = (ROOT / fd['base_image_url'].lstrip('/')).resolve()
                    if base_src.is_file():
                        shutil.copy2(base_src, dest / f"{name}_base.png")
                        row['base_image'] = f"{name}_base.png"
                output=(ROOT/job['image_url'].lstrip('/')).resolve()
                if not output.is_relative_to((ROOT/'outputs').resolve()): raise ValueError('Unexpected result path')
                shutil.copy2(output,dest/(name+'.png')); row['image']=name+'.png'
                likeness = row['metadata'].get('identity_assist', {}).get('likeness', {})
                sim = likeness.get('faces', [{}])[0].get('cosine_similarity', 'N/A') if likeness.get('status') == 'measured' else 'N/A'
                fd_info = f"sharpened {fd.get('face_px')}" if fd.get('applied') else 'no sharpening needed'
                print(f"Done {name} in {row['elapsed_sec']}s (Likeness: {sim}, {fd_info})", flush=True)
            except Exception as exc: row['error']=str(exc); print(str(exc),flush=True)
            finally:
                stop.set(); thread.join(timeout=5); row['sampled_peak_device_vram_mib']=max(samples) if samples else None
                row['vram_note']='One-second total-device samples, not per-job allocation peaks.'
                report.write_text(json.dumps(rows,indent=2),encoding='utf-8'); gallery(dest,rows,config['source_name'])
            if row.get('error'): break
            time.sleep(2)
    print(dest,flush=True)

if __name__=='__main__': main()
