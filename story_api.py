"""Visual Memory HTTP interface and adapter to Studio's existing serial GPU worker."""
import asyncio
import base64
import io
import json
import logging
import os
from pathlib import Path
import threading
import time
from typing import Literal

from fastapi import APIRouter, File, Form, HTTPException, UploadFile
from fastapi.responses import FileResponse
from PIL import Image, ImageOps
from pydantic import BaseModel, Field

import continuity_engine
from visual_memory import MemoryError, StoryMemory, text, uid
from story_sequences import StorySequences

LOG = logging.getLogger('qwen.visual_memory')


class StoryName(BaseModel):
    name: str = Field(min_length=1,max_length=150)


class Preservation(BaseModel):
    faces: bool = True
    clothing: bool = True
    environment: bool = True
    style: bool = True


class NextScene(BaseModel):
    prompt: str = Field(min_length=1,max_length=4000)
    parent_scene_id: str | None = None
    character_ids: list[str] | None = Field(default=None,max_length=12)
    location_id: str | None = None
    preserve: Preservation = Field(default_factory=Preservation)
    auto_references: bool = True
    reference_ids: list[str] = Field(default_factory=list,max_length=2)
    evaluate: bool = True
    mode: Literal['fast','balanced','ultra'] = 'balanced'
    aspect_ratio: Literal['1:1','4:3','3:4','16:9','9:16','3:2','2:3'] = '4:3'
    num_steps: int | None = Field(default=None,ge=1,le=100)
    seed: int = Field(default=42,ge=0,le=2**63-1)
    lora_enabled: bool = False
    camera_change: str = Field(default='',max_length=300)
    lighting_change: str = Field(default='',max_length=300)
    location_change: str = Field(default='',max_length=300)


class ConfirmDelete(BaseModel):
    confirmed: bool = False


class SceneSequence(BaseModel):
    scenes: list[NextScene] = Field(min_length=1,max_length=5)
    idempotency_key: str = Field(default_factory=uid,min_length=8,max_length=80,pattern=r'^[A-Za-z0-9_-]+$')


class EnhanceStory(BaseModel):
    scenes: list[str] = Field(min_length=1,max_length=5)
    parent_scene_id: str | None = None
    character_ids: list[str] | None = Field(default=None,max_length=12)


class AssignReference(BaseModel):
    scene_id: str
    head_box: list[float] | None = Field(default=None,min_length=4,max_length=4)


class SceneReview(BaseModel):
    status: Literal['pass','partial','fail','not_reviewed']
    notes: str = Field(default='',max_length=2000)


def read_photo(data):
    if not data or len(data)>25*1024*1024: raise MemoryError('Choose a photo smaller than 25 MB')
    try:
        with Image.open(io.BytesIO(data)) as im:
            if im.format not in {'PNG','JPEG','WEBP','AVIF'} or getattr(im,'n_frames',1)!=1:
                raise MemoryError('Choose a still PNG, JPEG, WebP or AVIF photo')
            if im.width*im.height>24000000 or max(im.size)>8192: raise MemoryError('Photo exceeds 24 megapixels or 8192 pixels per side')
            im.load()
            return ImageOps.exif_transpose(im).convert('RGB')
    except (OSError,ValueError,Image.DecompressionBombError) as exc:
        raise MemoryError(f'Cannot open this photo: {exc}') from exc


class StoryAdapter:
    def __init__(self, studio, memory):
        self.studio, self.memory = studio, memory

    def plan(self, story, req):
        prompt=text(req.prompt)
        if not prompt: raise MemoryError('Describe what happens next')
        for key, label in [('camera_change','Camera'),('lighting_change','Lighting'),('location_change','Move them to')]:
            if getattr(req,key).strip(): prompt += f'\n{label}{" " if key=="location_change" else ": "}{getattr(req,key).strip()}.'
        return continuity_engine.build_plan(self.memory,story,prompt,req.parent_scene_id,req.character_ids,req.location_id,
            req.preserve.model_dump(),req.auto_references,req.reference_ids,req.evaluate)

    def budget(self, req, refs):
        stats=self.studio.windows_system_stats()
        gpu=stats.get('gpu') or {}
        ram=stats.get('ram') or {}
        # A planning heuristic based on this app's tested 6-GB profiles, not a hard fit guarantee.
        estimated=round({'fast':4.5,'balanced':5.1,'ultra':5.5}[req.mode]+0.15*max(0,refs-1),2)
        warnings=[]
        total=gpu.get('vram_total_gb')
        if total and estimated>total: warnings.append('Estimated memory pressure is high. Try Fast with fewer references.')
        if ram.get('free_gb',3)<2: warnings.append('System RAM is low; reference encoding and CPU cache may page to disk. Close other large apps or use Fast.')
        return {'estimated_peak_gb':estimated,'estimate_only':True,'reference_count':refs,'gpu':gpu,'ram':ram,'warnings':warnings,
                'policy':'One GPU job at a time; images are loaded from disk only for this scene. Exact fit is not guaranteed.'}

    def submit(self, story, req, *, sequence_id=None, sequence_index=None):
        # An initial scene can register draft identities; assignment from the result is explicit.
        continuity_engine.discover_characters(self.memory,story,req.prompt)
        plan=self.plan(story,req)
        if sequence_id:
            plan.update(sequence_id=sequence_id,sequence_index=sequence_index)
        settings={key:getattr(req,key) for key in ['mode','aspect_ratio','num_steps','seed','lora_enabled']}
        settings.update(model='Qwen-Image-2.1 INT8', sampler='euler',scheduler='simple',cfg=1.0)
        plan['memory_budget']=self.budget(req,len(plan['references']))
        scene=self.memory.reserve(story,plan,settings)
        job_id=uid()
        source=plan['references'][0] if plan['references'] else None
        ref_paths=[str(self.memory.asset(story,ref['asset'])) for ref in plan['references'][1:]]
        request={'task':'edit' if source and source['role']=='previous_scene' else 'generate',
            'prompt':req.prompt,'mode':req.mode,'aspect_ratio':req.aspect_ratio,'seed':req.seed,'num_steps':req.num_steps,
            'transparent':False,'preserve':False,'region':'global',
            'source_path':str(self.memory.asset(story,source['asset'])) if source else None,'source_url':None,
            'mask_path':None,'mask_url':None,'reference_paths':ref_paths,
            'lora_enabled':req.lora_enabled,'lora_strength':0.8,'lora_name':'',
            'pose_enabled':False,'identity_enabled':False,'reference_only':bool(source and source['role']!='previous_scene'),
            'repair_kind':'none','repair_parent_id':'','face_detail':False,
            'story_id':story,'story_scene_id':scene['id']}
        job={'job_id':job_id,'status':'queued','phase':'Waiting for the local GPU','created_at':time.time(),
             'started_at':None,'finished_at':None,'progress':None,'error':None,'request':request,'result':None}
        event=threading.Event()
        try:
            self.studio.save_new_job(job)
            self.memory.update_scene(story,scene['id'],job_id=job_id)
            self.studio.CANCEL_EVENTS[job_id]=event
            self.studio.ACTIVE_PROMPTS[job_id]=req.prompt
            self.studio.EXECUTOR.submit(self.run,job_id,event)
        except Exception as exc:
            self.memory.update_scene(story,scene['id'],status='failed',error=str(exc))
            self.studio.CANCEL_EVENTS.pop(job_id,None); self.studio.ACTIVE_PROMPTS.pop(job_id,None)
            raise
        return self.memory.get('scenes',scene['id'],story)

    def compiled(self, request):
        scene=self.memory.get('scenes',request['story_scene_id'],request['story_id'])
        return scene['compiled_prompt']

    def cancel(self, story, key):
        scene=self.memory.get('scenes',key,story)
        if scene.get('job_id') and scene['status'] in {'queued','running'}:
            result=self.studio.cancel_job(scene['job_id'])
            if result['status']=='cancelled':
                self.memory.update_scene(story,key,status='cancelled',error='Generation cancelled. The previous scene is unchanged.',finished_at=time.time())
        return self.memory.get('scenes',key,story)

    def evaluate(self, scene, output):
        result={'status':'review_required','action':'not_automatically_verified','clothing':'not_automatically_verified',
                'environment':'not_automatically_verified','identity':[],
                'note':'CPU face comparisons are advisory. Review identity, clothing, action and background visually.'}
        if not scene.get('evaluate'): result['status']='disabled'; return result
        import identity_assist
        try:
            result['detected_face_count']=len(identity_assist.detect(output)['candidates'])
            result['expected_character_count']=len(scene['character_ids'])
            if result['detected_face_count']<len(scene['character_ids']):
                result['presence_warning']='Fewer clear faces were detected than expected characters. Check for missing people, occlusion or tiny/profile faces.'
        except Exception: result['detected_face_count']=None
        for key in scene['character_ids']:
            c=self.memory.get('characters',key,scene['story_id'])
            ref=c.get('face_reference')
            if not ref: continue
            try:
                with Image.open(self.memory.asset(scene['story_id'],ref)) as image:
                    score=identity_assist.similarity(image,output)
                result['identity'].append({'character_id':key,'name':c['name'],**score})
            except Exception:
                result['identity'].append({'character_id':key,'status':'not_measured'})
        return result

    def run(self, job_id, event):
        job=self.studio.get_job(job_id); req=job['request']
        story,key=req['story_id'],req['story_scene_id']
        samples=[]; stop=threading.Event()
        def monitor():
            while not stop.is_set():
                try:
                    used=(self.studio.windows_system_stats().get('gpu') or {}).get('vram_used_gb')
                    if used is not None: samples.append(used)
                except Exception: pass
                stop.wait(2)
        observer=threading.Thread(target=monitor,daemon=True); observer.start()
        try:
            if event.is_set():
                self.studio.process_job(job_id,event)
                self.memory.update_scene(story,key,status='cancelled',error='Generation cancelled. The previous scene is unchanged.',finished_at=time.time())
                return
            self.memory.update_scene(story,key,status='running')
            self.studio.process_job(job_id,event)
            job=self.studio.get_job(job_id)
            if job['status']!='succeeded':
                error=job.get('error') or 'Generation cancelled. The previous scene is unchanged.'
                if 'out of memory' in error.lower(): error += ' Try Fast, fewer references or fewer open applications.'
                self.memory.update_scene(story,key,status='cancelled' if event.is_set() else 'failed',error=error,finished_at=time.time())
                return
            path=(self.studio.PROJECT_DIR/job['result']['image_url'].lstrip('/')).resolve()
            if not path.is_relative_to(Path(self.studio.OUTPUT_DIR).resolve()): raise MemoryError('Invalid generation output')
            with Image.open(path) as image: output=image.copy()
            scene=self.memory.get('scenes',key,story)
            evaluation=self.evaluate(scene,output)
            metadata=dict(job['result']['metadata'],sampled_peak_device_vram_gb=max(samples) if samples else None,
                          vram_measurement='Two-second total-device samples; not per-model allocation or a guaranteed peak.')
            self.memory.finish(story,key,output,metadata,evaluation)
        except Exception as exc:
            LOG.exception('Story generation failed: %s',key)
            self.memory.update_scene(story,key,status='failed',error=str(exc),finished_at=time.time())
        finally:
            stop.set(); observer.join(timeout=3)


def install(studio):
    config_path=studio.PROJECT_DIR/'config'/'visual_memory.json'
    config=json.loads(config_path.read_text(encoding='utf-8'))
    root=Path(os.environ.get('QWEN_STORY_DIR') or config.get('storage_directory','stories'))
    if not root.is_absolute(): root=studio.PROJECT_DIR/root
    memory=StoryMemory(root)
    for key,attribute,folder in [('unet','UNET_NAME','diffusion_models'),('clip','CLIP_NAME','text_encoders'),('vae','VAE_NAME','vae')]:
        name=config.get('model_names',{}).get(key)
        if name:
            model=(studio.COMFY_DIR/'models'/folder/name).resolve()
            if not model.is_relative_to((studio.COMFY_DIR/'models'/folder).resolve()) or not model.is_file():
                raise ValueError(f'Configured {key} model must be an installed ComfyUI filename')
            setattr(studio.engine,attribute,name)
    adapter=StoryAdapter(studio,memory)
    adapter.sequences=StorySequences(memory,adapter,NextScene)
    router=APIRouter(prefix='/api/stories')

    @router.get('')
    def stories(): return {'stories':memory.list_stories()}

    @router.post('',status_code=201)
    def new_story(req: StoryName): return memory.create_story(req.name)

    @router.get('/config')
    def configuration(): return {'default_mode':config['default_mode'],'default_aspect_ratio':config['default_aspect_ratio'],
        'max_reference_images':3,'cfg':1.0,'sampler':'euler','scheduler':'simple','model_names':{
            'unet':studio.engine.UNET_NAME,'clip':studio.engine.CLIP_NAME,'vae':studio.engine.VAE_NAME},
        'sequence_max_scenes':5,'story_enhancer':{'profile':'qwen21-story-v1','model':'qwen21-qwen4b'},
        'storage':'Persistent local SQLite and image files. Story prompts and metadata are saved for continuity.'}

    @router.get('/{story_id}')
    def snapshot(story_id: str): return memory.snapshot(story_id)

    @router.get('/{story_id}/files/{relative:path}')
    def file(story_id: str,relative: str):
        memory.get('stories',story_id)
        path=memory.path(story_id,relative)
        if path.suffix.lower() not in {'.png','.jpg','.jpeg','.webp'} or not path.is_file(): raise MemoryError('Image not found',404)
        return FileResponse(path)

    @router.post('/{story_id}/characters',status_code=201)
    def character(story_id: str,metadata: dict): return memory.character(story_id,metadata)

    @router.patch('/{story_id}/characters/{character_id}')
    def edit_character(story_id: str,character_id: str,metadata: dict): return memory.character(story_id,metadata,character_id)

    @router.post('/{story_id}/characters/{character_id}/delete')
    def delete_character(story_id: str,character_id: str,req: ConfirmDelete):
        if not req.confirmed: raise MemoryError('Confirm character deletion')
        return memory.delete_character(story_id,character_id)

    @router.post('/{story_id}/characters/{character_id}/references')
    async def upload_reference(story_id: str,character_id: str,image: UploadFile=File(...),head_box: str=Form('')):
        im=read_photo(await image.read(25*1024*1024+1))
        try: box=json.loads(head_box) if head_box else None
        except json.JSONDecodeError as exc: raise MemoryError('Head crop must contain four numeric fractions') from exc
        return memory.add_reference(story_id,character_id,im,box)

    @router.post('/{story_id}/characters/{character_id}/assign')
    def assign(story_id: str,character_id: str,req: AssignReference):
        scene=memory.get('scenes',req.scene_id,story_id)
        if scene['status']!='succeeded' or scene['deleted']: raise MemoryError('Select a completed scene')
        with Image.open(memory.asset(story_id,scene['image'])) as im:
            return memory.add_reference(story_id,character_id,im,req.head_box,req.scene_id)

    @router.get('/{story_id}/scenes/{scene_id}/faces')
    def faces(story_id: str,scene_id: str):
        import identity_assist
        scene=memory.get('scenes',scene_id,story_id)
        if not scene.get('image'): raise MemoryError('This scene has no image')
        with Image.open(memory.asset(story_id,scene['image'])) as im:
            detected=identity_assist.detect(im)
            for candidate in detected['candidates']:
                preview=identity_assist.crop_head(im,candidate['head_box']); preview.thumbnail((160,160))
                buf=io.BytesIO(); preview.save(buf,'PNG')
                candidate['preview']='data:image/png;base64,'+base64.b64encode(buf.getvalue()).decode('ascii')
            return detected

    @router.post('/{story_id}/locations',status_code=201)
    async def location(story_id: str,name: str=Form(...),description: str=Form(''),image: UploadFile|None=File(None)):
        im=read_photo(await image.read(25*1024*1024+1)) if image else None
        return memory.location(story_id,name,description,im)

    @router.post('/{story_id}/plan')
    def plan(story_id: str,req: NextScene):
        result=adapter.plan(story_id,req)
        result['memory_budget']=adapter.budget(req,len(result['references']))
        return result

    @router.post('/{story_id}/enhance')
    def enhance_story(story_id: str,req: EnhanceStory):
        import story_prompting
        import dolphin_prompting
        try:
            prompts=story_prompting.validate_inputs(req.scenes)
        except dolphin_prompting.EnhancementError as exc:
            raise HTTPException(exc.status,str(exc)) from exc
        snapshot=memory.snapshot(story_id)
        if any(q['status'] in {'queued','running','cancelling'} for q in snapshot['sequences']) or any(s['status'] in {'queued','running'} for s in snapshot['scenes']):
            raise MemoryError('Wait for this story generation to finish before enhancing.',409)
        parent_id=snapshot['current_scene_id'] if req.parent_scene_id is None else req.parent_scene_id or None
        parent=memory.get('scenes',parent_id,story_id) if parent_id else None
        cast=[c for c in snapshot['characters'] if not c.get('deleted')]
        if req.character_ids is not None:
            if any(key not in {c['id'] for c in cast} for key in req.character_ids):
                raise MemoryError('Choose characters from this story')
            cast=[c for c in cast if c['id'] in req.character_ids]
        context={'cast':[{'name':c['name'],'aliases':c.get('aliases',[])[:4],'role':c.get('role',''),
                         'appearance':c.get('appearance','')[:180],
                         'current_outfit':((parent or {}).get('clothing',{}).get(c['id']) or c.get('current_clothing') or c.get('default_clothing',''))[:240]} for c in cast],
                 'previous_moment':parent['user_prompt'][:700] if parent else '',
                 'current_location':(parent or {}).get('environment','')[:300],
                 'facts_are':'intended story state, not verified image observations'}
        return studio.enhance_for_studio('',model=dolphin_prompting.QWEN_MODEL,story_scenes=prompts,story_context=context)

    @router.post('/{story_id}/scenes',status_code=202)
    def generate(story_id: str,req: NextScene): return adapter.submit(story_id,req)

    @router.post('/{story_id}/sequences',status_code=202)
    def generate_sequence(story_id: str,req: SceneSequence):
        return adapter.sequences.create(story_id,[r.model_dump() for r in req.scenes],req.idempotency_key)

    @router.get('/{story_id}/sequences/{sequence_id}')
    def sequence(story_id: str,sequence_id: str): return memory.get('sequences',sequence_id,story_id)

    @router.post('/{story_id}/sequences/{sequence_id}/cancel')
    def cancel_sequence(story_id: str,sequence_id: str): return adapter.sequences.cancel(story_id,sequence_id)

    @router.post('/{story_id}/sequences/{sequence_id}/resume',status_code=202)
    def resume_sequence(story_id: str,sequence_id: str): return adapter.sequences.resume(story_id,sequence_id)

    @router.post('/{story_id}/scenes/{scene_id}/continue')
    def activate(story_id: str,scene_id: str): return memory.activate(story_id,scene_id)

    @router.post('/{story_id}/scenes/{scene_id}/review')
    def review(story_id: str,scene_id: str,req: SceneReview):
        scene=memory.get('scenes',scene_id,story_id)
        if scene['status']!='succeeded': raise MemoryError('Review a completed scene')
        return memory.update_scene(story_id,scene_id,visual_review={'status':req.status,'notes':req.notes,'reviewed_at':time.time(),'source':'manual'})

    @router.post('/{story_id}/scenes/{scene_id}/delete')
    def delete_scene(story_id: str,scene_id: str,req: ConfirmDelete):
        if not req.confirmed: raise MemoryError('Confirm scene deletion')
        return memory.delete_scene(story_id,scene_id)

    @router.post('/{story_id}/scenes/{scene_id}/cancel')
    def cancel(story_id: str,scene_id: str):
        return adapter.cancel(story_id,scene_id)

    @studio.app.exception_handler(MemoryError)
    async def memory_error(request,exc):
        from fastapi.responses import JSONResponse
        return JSONResponse(status_code=exc.status,content={'detail':str(exc)})

    @studio.app.get('/story',response_class=FileResponse)
    def page(): return FileResponse(studio.PROJECT_DIR/'web'/'story.html')

    studio.app.include_router(router)
    return adapter
