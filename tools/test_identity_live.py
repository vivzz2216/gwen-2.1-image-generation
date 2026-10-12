"""Resumable local-only Identity Assist tests. Generated != visually passed."""
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
from PIL import Image, ImageDraw
ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
import identity_assist

CASES = [
    ('walking', 'generate', 'fast', 'A full-body photograph of the same person walking naturally along a park path. Keep their identity and source clothing. One person. No text.'),
    ('raised_arm', 'generate', 'fast', 'A full-body photograph of the same person raising their right arm above their head. Both feet are on the ground. Keep their identity and source clothing. One person. No text.'),
    ('turn_smile', 'generate', 'balanced', 'A head-and-shoulders photograph of the same person turning their head slightly to the left and smiling naturally. Keep their recognizable identity and source clothing. One person. No text.'),
    ('shirt_mask', 'edit', 'fast', 'Change only the selected shirt area to dark green fabric. Keep my face, hat, hair, pose, hands, lighting and background exactly unchanged.'),
    ('pose_reference', 'generate', 'fast', 'A full-body photograph of the identity-reference person in the raised-arm pose shown by the pose reference. Keep the identity-reference person and their source clothing. One person. No text.'),
]

def gallery(dest, rows):
    body = ['<!doctype html><html lang="en"><meta charset="utf-8"><meta name="viewport" content="width=device-width"><title>Identity Assist local tests</title>',
        '<style>body{font:16px system-ui;margin:24px;background:#171717;color:#eee}a{color:#c1bcff}img{max-width:100%;max-height:480px;object-fit:contain}section{margin:36px 0}.pair{display:flex;gap:16px;flex-wrap:wrap}.pair a{max-width:45%}pre{white-space:pre-wrap}</style>',
        '<h1>Identity Assist local tests</h1><p>Local Qwen 2.1, no LoRA. Face scores are advisory, not proof. Test prompts are deliberately authored fixtures. Originals are unchanged.</p>',
        '<p><a href="original.png">Original</a> · <a href="head.png">Reviewed head crop</a> · <a href="mask.png">Manually authored shirt mask</a> · <a href="results.json">JSON report</a></p>']
    for row in rows:
        body.append('<section><h2>'+html.escape(row['case'])+'</h2><p>'+html.escape(row.get('visual_status', 'not_reviewed'))+'</p><p>'+html.escape(row['prompt'])+'</p>')
        body.append('<div class="pair"><a href="original.png"><img src="original.png" alt="Original reference"></a>')
        if row.get('image'): body.append('<a href="'+row['image']+'"><img src="'+row['image']+'" alt="'+html.escape(row['case'])+' result"></a>')
        body.append('</div><pre>'+html.escape(json.dumps({k:row.get(k) for k in ('mode','elapsed_sec','error','visual_notes','sampled_peak_device_vram_mib')},indent=2))+'</pre></section>')
    (dest/'index.html').write_text('\n'.join(body), encoding='utf-8')

def main():
    parser = argparse.ArgumentParser(); parser.add_argument('--resume', type=Path); parser.add_argument('--prepare', action='store_true')
    parser.add_argument('--approved-mask', action='store_true'); parser.add_argument('--approved-pose', action='store_true')
    parser.add_argument('--collect-job'); parser.add_argument('--name', choices=['browser_cup', 'face_repair']); args=parser.parse_args()
    dest=args.resume or ROOT/'outputs'/'identity_tests'/datetime.now().strftime('%Y%m%d_%H%M%S_%f'); dest.mkdir(parents=True,exist_ok=True)
    original = ROOT/'assets'/'example-38.jpg'
    source = Image.open(original).convert('RGB')
    source_hash = hashlib.sha256(original.read_bytes()).hexdigest()
    context=dest/'context.json'
    if not context.exists():
        detected=identity_assist.detect(source)
        if len(detected['candidates']) != 1: raise ValueError('This fixture needs exactly one detected primary face; review before proceeding.')
        box=detected['candidates'][0]['head_box']
        source.save(dest/'original.png'); identity_assist.crop_head(source, box).save(dest/'head.png')
        mask=Image.new('L',source.size,0)
        ImageDraw.Draw(mask).polygon([(1565,780),(1586,794),(1597,815),(1583,838),(1560,817)], fill=255)
        mask.save(dest/'mask.png')
        overlay=source.copy(); tint=Image.new('RGB',source.size,'red')
        overlay.paste(Image.blend(source,tint,.5),(0,0),mask); overlay.save(dest/'mask_review.png')
        context.write_text(json.dumps({'source':str(original),'source_sha256':source_hash,'head_box':box,
            'mask_manual':True,'mask_reviewed':False,'mask_scope':'small shirt sleeve patch only, not the whole shirt','detector':detected},indent=2),encoding='utf-8')
    config=json.loads(context.read_text())
    if config['source_sha256'] != source_hash: raise ValueError('Original fixture changed; start a new test rather than overwrite results.')
    if args.collect_job:
        if not args.name: raise ValueError('Name the browser test being collected')
        job=httpx.get('http://127.0.0.1:7860/api/jobs/'+args.collect_job).json()
        if job['status'] not in ('succeeded','failed','cancelled'): raise ValueError('Job is still running; collect later without submitting another')
        report=dest/'results.json'; rows=json.loads(report.read_text()) if report.exists() else []
        if any(row['case']==args.name for row in rows): print('Already collected; no overwrite.'); return
        prompts={'browser_cup':'Create a waist-up photograph of the same person holding a cup in the right hand at chest height. Replace the extended selfie arm with the requested cup-holding action. Keep their recognizable face and source clothing. One person. No text.',
                 'face_repair':'Refine facial likeness to the selected identity head crop. Keep the current head angle, neutral expression, hat, hairstyle and lighting. Change only the reviewed face region.'}
        row={'case':args.name,'job_id':args.collect_job,'task':job['request']['task'],'mode':job['request']['mode'],
            'prompt':prompts[args.name],'seed':job['request']['seed'],'steps_override':job['request']['num_steps'],
            'lora_enabled':False,'browser_submitted':True,'visual_status':'not_reviewed','metadata':job.get('metadata'),
            'elapsed_sec':job.get('elapsed_sec'),'vram_note':'No continuous total-device VRAM sampling for this browser case.'}
        if job['status']=='succeeded':
            shutil.copy2(ROOT/job['image_url'].lstrip('/'),dest/(args.name+'.png')); row['image']=args.name+'.png'
        else: row['error']=job.get('error') or job['status']
        rows.append(row); report.write_text(json.dumps(rows,indent=2),encoding='utf-8'); gallery(dest,rows); print(json.dumps(row),flush=True); return
    if args.prepare: print(dest,flush=True); return
    if not args.approved_mask: raise ValueError('View mask_review.png, then explicitly use --approved-mask.')
    config['mask_reviewed']=True; context.write_text(json.dumps(config,indent=2),encoding='utf-8')
    report=dest/'results.json'; rows=json.loads(report.read_text()) if report.exists() else []
    with httpx.Client(base_url='http://127.0.0.1:7860',timeout=240) as client:
        for index,(name,task,mode,prompt) in enumerate(CASES):
            if name == 'pose_reference' and not args.approved_pose:
                print('Review raised_arm.png before resuming with --approved-pose.', flush=True); break
            row=next((r for r in rows if r['case']==name),None)
            if row and (row.get('image') or row.get('error')): continue
            if not row:
                queue=httpx.get('http://127.0.0.1:8188/queue').json()
                if queue.get('queue_running') or queue.get('queue_pending'):
                    print('ComfyUI busy: stop without interrupting other work; resume later.',flush=True); break
                row={'case':name,'task':task,'mode':mode,'prompt':prompt,'seed':202610120+index,'lora_enabled':False,'visual_status':'not_reviewed'}; rows.append(row)
            samples=[]; stop=threading.Event()
            def monitor():
                while not stop.is_set():
                    try:
                        output=subprocess.run(['nvidia-smi','--query-gpu=memory.used','--format=csv,noheader,nounits'],capture_output=True,text=True,timeout=4,creationflags=subprocess.CREATE_NO_WINDOW)
                        samples.append(int(output.stdout.strip().splitlines()[0]))
                    except Exception: pass
                    stop.wait(1)
            thread=threading.Thread(target=monitor,daemon=True); thread.start()
            try:
                if not row.get('job_id'):
                    data={'prompt':prompt,'task':task,'mode':mode,'aspect_ratio':'3:4','seed':str(row['seed']),'lora_enabled':'false',
                        'identity_enabled':'true','identity_reviewed':'true','identity_box':json.dumps(config['head_box'])}
                    files={'identity_reference':('identity.png',(dest/'original.png').read_bytes(),'image/png')}
                    if task=='edit':
                        data.update(preserve='true',region='manual',mask_reviewed='true')
                        files.update(image=('source.png',(dest/'original.png').read_bytes(),'image/png'),mask=('mask.png',(dest/'mask.png').read_bytes(),'image/png'))
                    if name=='pose_reference':
                        pose=next((r for r in rows if r['case']=='raised_arm'),{})
                        if not pose.get('image'): raise ValueError('Reviewed raised-arm photo unavailable')
                        data.update(pose_enabled='true',pose_reviewed='true'); files['pose_reference']=('pose.png',(dest/pose['image']).read_bytes(),'image/png')
                    response=client.post('/api/jobs',data=data,files=files); response.raise_for_status(); row['job_id']=response.json()['job_id']
                    report.write_text(json.dumps(rows,indent=2),encoding='utf-8'); print('Started '+name+': '+row['job_id'],flush=True)
                deadline=time.monotonic()+1200; previous=None
                while time.monotonic()<deadline:
                    response=client.get('/api/jobs/'+row['job_id']); response.raise_for_status(); job=response.json()
                    if job['status'] in ('succeeded','failed','cancelled'): break
                    if job.get('phase')!=previous: previous=job.get('phase'); print(name+': '+str(previous),flush=True)
                    time.sleep(2)
                else: raise TimeoutError('Job active; resume to collect, do not duplicate')
                if job['status']!='succeeded': raise RuntimeError(job.get('error') or job['status'])
                row['metadata']=job['metadata']; row['elapsed_sec']=job.get('elapsed_sec')
                output=ROOT/job['image_url'].lstrip('/'); shutil.copy2(output,dest/(name+'.png')); row['image']=name+'.png'
            except Exception as exc:
                row['error']=str(exc); print(row['error'],flush=True)
            finally:
                stop.set(); thread.join(timeout=5)
                row['sampled_peak_device_vram_mib']=max(samples) if samples else None
                row['vram_note']='One-second total-device samples, not per-job allocation peaks.'
                report.write_text(json.dumps(rows,indent=2),encoding='utf-8'); gallery(dest,rows)
            if row.get('error'): break
            # Allow own Comfy job cleanup; never clear the global queue.
            time.sleep(2)
    print(dest,flush=True)

if __name__=='__main__': main()
