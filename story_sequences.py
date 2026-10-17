"""Durable serial story sequences. Only the existing adapter performs GPU inference."""
import hashlib
import json
import logging
import threading
import time

from visual_memory import MemoryError, uid

LOG = logging.getLogger('qwen.story_sequences')
ACTIVE = {'queued', 'running', 'cancelling'}


class StorySequences:
    def __init__(self, memory, adapter, request_type, *, autostart=True, poll_seconds=.5):
        self.memory, self.adapter, self.request_type = memory, adapter, request_type
        self.autostart, self.poll_seconds = autostart, poll_seconds
        self.workers = {}
        self.worker_lock = threading.RLock()

    def create(self, story, requests, key):
        if not 1 <= len(requests) <= 5:
            raise MemoryError('Enter one to five scene prompts')
        rows = [self.request_type.model_validate(r).model_dump() for r in requests]
        for index, row in enumerate(rows):
            row['prompt'] = row['prompt'].strip()
            if not row['prompt']:
                raise MemoryError(f'Scene {index+1} needs a prompt')
            if index and row.get('parent_scene_id'):
                raise MemoryError('Only the first scene can choose a parent; later scenes follow their preceding result')
        fingerprint = hashlib.sha256(json.dumps(rows, sort_keys=True).encode()).hexdigest()
        with self.memory.lock, self.memory.connect() as db:
            db.execute('BEGIN IMMEDIATE')
            story_data = self.memory._get(db, 'stories', story)
            for row in db.execute('SELECT data FROM sequences WHERE story=?', (story,)):
                existing = json.loads(row[0])
                if existing['idempotency_key'] == key:
                    if existing['fingerprint'] != fingerprint:
                        raise MemoryError('This sequence request key already belongs to different prompts. Start a new sequence.',409)
                    return existing
            self.memory.guard_sequence(db, story)
            if any(json.loads(r[0])['status'] in {'queued','running'} for r in db.execute('SELECT data FROM scenes WHERE story=?',(story,))):
                raise MemoryError('Wait for the current scene before starting a sequence',409)
            parent = rows[0]['parent_scene_id']
            parent = story_data['current_scene_id'] if parent is None else parent or None
            if parent:
                scene = self.memory._get(db,'scenes',parent,story)
                if scene['status']!='succeeded' or scene.get('deleted'):
                    raise MemoryError('Choose a completed starting scene',409)
            sequence = {'id':uid(), 'story_id':story, 'created_at':time.time(), 'updated_at':time.time(),
                        'status':'queued', 'error':None, 'cancel_requested':False, 'initial_parent_scene_id':parent,
                        'idempotency_key':key, 'fingerprint':fingerprint,
                        'items':[{'index':i, 'request':r, 'status':'waiting', 'scene_id':None, 'attempts':[], 'error':None} for i,r in enumerate(rows)]}
            self.memory._put(db,'sequences',sequence)
        if self.autostart:
            self._launch(story,sequence['id'])
        return self.memory.get('sequences',sequence['id'],story)

    def update(self, story, key, **changes):
        with self.memory.lock, self.memory.connect() as db:
            db.execute('BEGIN IMMEDIATE')
            sequence = self.memory._get(db,'sequences',key,story)
            sequence.update(changes,updated_at=time.time())
            self.memory._put(db,'sequences',sequence)
            return sequence

    def cancel(self, story, key):
        with self.memory.lock, self.memory.connect() as db:
            sequence = self.memory._get(db,'sequences',key,story)
            if sequence['status'] not in ACTIVE:
                return sequence
            sequence.update(cancel_requested=True,status='cancelling',updated_at=time.time())
            self.memory._put(db,'sequences',sequence)
            active = [item['scene_id'] for item in sequence['items'] if item['status']=='running' and item['scene_id']]
        for scene_id in active:
            self.adapter.cancel(story,scene_id)
        return self.memory.get('sequences',key,story)

    def resume(self, story, key):
        with self.worker_lock, self.memory.lock, self.memory.connect() as db:
            db.execute('BEGIN IMMEDIATE')
            sequence = self.memory._get(db,'sequences',key,story)
            if sequence['status'] in ACTIVE or sequence['status']=='succeeded':
                return sequence
            if self.workers.get(key) and self.workers[key].is_alive():
                raise MemoryError('The previous worker is finishing. Try Resume again in a moment.',409)
            self.memory.guard_sequence(db,story)
            if any(json.loads(r[0])['status'] in {'queued','running'} for r in db.execute('SELECT data FROM scenes WHERE story=?',(story,))):
                raise MemoryError('Wait for the current scene to finish before resuming',409)
            sequence.update(status='queued',cancel_requested=False,error=None,updated_at=time.time())
            self.memory._put(db,'sequences',sequence)
        if self.autostart:
            self._launch(story,key)
        return self.memory.get('sequences',key,story)

    def _launch(self, story, key):
        with self.worker_lock:
            if self.workers.get(key) and self.workers[key].is_alive():
                return
            worker=threading.Thread(target=self._work,args=(story,key),daemon=True,name='story-sequence-'+key[:8])
            self.workers[key]=worker
            worker.start()

    def _work(self, story, key):
        try:
            sequence=self.update(story,key,status='running')
            parent=sequence['initial_parent_scene_id']
            for index in range(len(sequence['items'])):
                with self.memory.lock:
                    sequence=self.memory.get('sequences',key,story)
                    item=sequence['items'][index]
                    # Scene tagging closes the crash window between reservation and recording its ID.
                    related=[s for s in self.memory.snapshot(story)['scenes'] if s.get('sequence_id')==key and s.get('sequence_index')==index and not s.get('deleted')]
                    scene=self.memory.get('scenes',item['scene_id'],story) if item['scene_id'] else (related[-1] if related else None)
                    if scene and scene.get('deleted'):
                        raise MemoryError('A sequence scene was deleted. Start a new sequence from an available scene.',409)
                    if scene and scene['status']=='succeeded':
                        item.update(status='succeeded',scene_id=scene['id'],error=None)
                        self.update(story,key,items=sequence['items'])
                        parent=scene['id']
                        continue
                    if sequence['cancel_requested'] and not (scene and scene['status'] in {'queued','running'}):
                        self.update(story,key,status='cancelled',error='Sequence stopped. Completed scenes are retained.')
                        return
                    if not scene or scene['status'] not in {'queued','running'}:
                        request=self.request_type.model_validate(dict(item['request'],parent_scene_id=parent or ''))
                        scene=self.adapter.submit(story,request,sequence_id=key,sequence_index=index)
                        item['attempts'].append(scene['id'])
                    item.update(status='running',scene_id=scene['id'],error=None)
                    self.update(story,key,items=sequence['items'])
                cancellation_sent=False
                while scene['status'] in {'queued','running'}:
                    sequence=self.memory.get('sequences',key,story)
                    if sequence['cancel_requested'] and not cancellation_sent:
                        self.adapter.cancel(story,scene['id'])
                        cancellation_sent=True
                    threading.Event().wait(self.poll_seconds)
                    scene=self.memory.get('scenes',scene['id'],story)
                sequence=self.memory.get('sequences',key,story)
                item=sequence['items'][index]
                if scene['status']!='succeeded':
                    item.update(status=scene['status'],error=scene.get('error') or 'Scene did not finish')
                    cancelled=sequence['cancel_requested'] or scene['status']=='cancelled'
                    self.update(story,key,items=sequence['items'],status='cancelled' if cancelled else 'failed',
                                error=f'Stopped at scene {index+1}: {item["error"]}. Later scenes were not generated; completed scenes are retained.')
                    return
                item.update(status='succeeded',error=None)
                self.update(story,key,items=sequence['items'])
                parent=scene['id']
            sequence=self.memory.get('sequences',key,story)
            self.update(story,key,status='cancelled' if sequence['cancel_requested'] else 'succeeded',
                        error='Sequence stopped. Completed scenes are retained.' if sequence['cancel_requested'] else None)
        except Exception as exc:
            LOG.exception('Sequence %s stopped',key)
            sequence=self.memory.get('sequences',key,story)
            if 'index' in locals() and sequence['items'][index]['status']!='succeeded':
                sequence['items'][index].update(status='failed',error=str(exc))
            self.update(story,key,items=sequence['items'],status='failed',error=str(exc))
        finally:
            with self.worker_lock:
                self.workers.pop(key,None)
