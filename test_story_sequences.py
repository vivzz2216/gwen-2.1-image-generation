"""Sequence control-flow tests. Bitmap fixtures below are NOT model generations."""
import json
from pathlib import Path
import tempfile
import threading
import time
import unittest
from unittest.mock import patch

from fastapi import FastAPI
from fastapi.testclient import TestClient
from PIL import Image
from pydantic import ValidationError

import continuity_engine
from story_api import NextScene, SceneSequence, install
from story_sequences import StorySequences
from visual_memory import MemoryError, StoryMemory
import studio_service


class FixtureAdapter:
    def __init__(self,memory):
        self.memory=memory
        self.calls=[]
        self.fail_calls=set()
        self.hold=False
        self.on_finish=None
        self.cancelled=[]

    def submit(self,story,req,*,sequence_id=None,sequence_index=None):
        plan=continuity_engine.build_plan(self.memory,story,req.prompt,req.parent_scene_id)
        plan.update(sequence_id=sequence_id,sequence_index=sequence_index)
        scene=self.memory.reserve(story,plan,req.model_dump())
        self.calls.append(scene)
        if len(self.calls) in self.fail_calls:
            return self.memory.update_scene(story,scene['id'],status='failed',error='Simulated CUDA out of memory')
        if self.hold:return scene
        result=self.memory.finish(story,scene['id'],Image.new('RGB',(32,32),'green'),{'unit_fixture':True},{'status':'not_measured'})
        if self.on_finish:self.on_finish(result)
        return result

    def cancel(self,story,key):
        self.cancelled.append(key)
        return self.memory.update_scene(story,key,status='cancelled',error='Fixture cancellation')


class SequenceTests(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory()
        self.memory=StoryMemory(Path(self.temp.name)/'stories')
        self.story=self.memory.create_story('Synthetic queue fixture')['id']
        self.adapter=FixtureAdapter(self.memory)
        self.runner=StorySequences(self.memory,self.adapter,NextScene,autostart=False,poll_seconds=.001)

    def tearDown(self):self.temp.cleanup()

    def create(self,count=5,key='fixture-key-0001',**changes):
        requests=[NextScene(prompt=f'The ball moves to position {i+1}.',parent_scene_id='' if i==0 else None,mode='fast',**changes).model_dump() for i in range(count)]
        return self.runner.create(self.story,requests,key)

    def run_sequence(self,sequence):
        self.runner._work(self.story,sequence['id'])
        return self.memory.get('sequences',sequence['id'],self.story)

    def test_five_serial_scenes_chain_actual_saved_outputs(self):
        sequence=self.run_sequence(self.create())
        self.assertEqual(sequence['status'],'succeeded')
        self.assertEqual(len(self.adapter.calls),5)
        self.assertIsNone(self.adapter.calls[0]['parent_scene_id'])
        for before,after in zip(self.adapter.calls,self.adapter.calls[1:]):
            self.assertEqual(after['parent_scene_id'],before['id'])
            self.assertEqual(after['references'][0]['asset'],self.memory.get('scenes',before['id'])['image'])
        self.assertEqual(self.memory.snapshot(self.story)['current_scene_id'],sequence['items'][-1]['scene_id'])
        self.assertTrue(all(item['status']=='succeeded' for item in sequence['items']))

    def test_identical_request_key_does_not_duplicate_after_completion(self):
        sequence=self.run_sequence(self.create())
        duplicate=self.create()
        self.assertEqual(duplicate['id'],sequence['id'])
        self.assertEqual(len(self.memory.snapshot(self.story)['sequences']),1)
        self.assertEqual(len(self.adapter.calls),5)

    def test_key_cannot_be_reused_for_different_prompts(self):
        self.create()
        with self.assertRaisesRegex(MemoryError,'different prompts'):self.create(count=2)

    def test_single_generation_and_history_changes_blocked_while_sequence_active(self):
        self.create()
        plan=continuity_engine.build_plan(self.memory,self.story,'Another scene.','')
        with self.assertRaisesRegex(MemoryError,'sequence running'):self.memory.reserve(self.story,plan,{})

    def test_two_concurrent_submissions_allow_only_one_sequence(self):
        outcomes=[]
        def create(key):
            try:outcomes.append(self.create(key=key)['id'])
            except MemoryError:outcomes.append('blocked')
        workers=[threading.Thread(target=create,args=(f'concurrent-{i}',)) for i in range(2)]
        for worker in workers:worker.start()
        for worker in workers:worker.join()
        self.assertEqual(outcomes.count('blocked'),1)

    def test_failure_stops_remaining_and_resume_skips_successes(self):
        self.adapter.fail_calls={2}
        failed=self.run_sequence(self.create())
        self.assertEqual(failed['status'],'failed')
        self.assertEqual(len(self.adapter.calls),2)
        first=failed['items'][0]['scene_id']
        old_failed=failed['items'][1]['scene_id']
        self.assertEqual(self.memory.snapshot(self.story)['current_scene_id'],first)
        self.assertIn('out of memory',failed['error'])
        resumed=self.run_sequence(self.runner.resume(self.story,failed['id']))
        self.assertEqual(resumed['status'],'succeeded')
        self.assertEqual(resumed['items'][0]['scene_id'],first)
        self.assertEqual(len(self.adapter.calls),6)
        self.assertEqual(len(resumed['items'][1]['attempts']),2)
        self.assertEqual(self.memory.get('scenes',old_failed)['status'],'failed')

    def test_cancel_at_scene_boundary_prevents_next_submission(self):
        sequence=self.create()
        self.adapter.on_finish=lambda scene:self.runner.cancel(self.story,sequence['id'])
        stopped=self.run_sequence(sequence)
        self.assertEqual(stopped['status'],'cancelled')
        self.assertEqual(len(self.adapter.calls),1)
        self.assertEqual(stopped['items'][0]['status'],'succeeded')

    def test_cancel_running_job_targets_only_own_scene(self):
        self.adapter.hold=True
        sequence=self.create()
        worker=threading.Thread(target=self.runner._work,args=(self.story,sequence['id']))
        worker.start()
        deadline=time.monotonic()+3
        while time.monotonic()<deadline and not self.adapter.calls:time.sleep(.005)
        self.assertTrue(self.adapter.calls)
        self.runner.cancel(self.story,sequence['id'])
        worker.join(timeout=3)
        self.assertFalse(worker.is_alive())
        self.assertEqual(set(self.adapter.cancelled),{self.adapter.calls[0]['id']})
        self.assertEqual(self.memory.get('sequences',sequence['id'])['status'],'cancelled')

    def test_restart_pauses_then_resume_preserves_completed_frames(self):
        sequence=self.create(count=3)
        first=self.adapter.submit(self.story,NextScene.model_validate(sequence['items'][0]['request']),sequence_id=sequence['id'],sequence_index=0)
        sequence['items'][0].update(status='succeeded',scene_id=first['id'])
        self.runner.update(self.story,sequence['id'],items=sequence['items'],status='running')
        restored=StoryMemory(self.memory.root)
        restored.recover()
        self.assertEqual(restored.get('sequences',sequence['id'])['status'],'paused')
        resumed=self.run_sequence(self.runner.resume(self.story,sequence['id']))
        self.assertEqual(resumed['status'],'succeeded')
        self.assertEqual(len(self.adapter.calls),3)
        self.assertEqual(resumed['items'][0]['scene_id'],first['id'])

    def test_crash_window_reuses_tagged_success_without_duplicate(self):
        sequence=self.create(count=2)
        first=self.adapter.submit(self.story,NextScene.model_validate(sequence['items'][0]['request']),sequence_id=sequence['id'],sequence_index=0)
        # No sequence-item update: imitate a crash after scene commit but before ID association.
        self.memory.recover()
        resumed=self.run_sequence(self.runner.resume(self.story,sequence['id']))
        self.assertEqual(resumed['status'],'succeeded')
        self.assertEqual(resumed['items'][0]['scene_id'],first['id'])
        self.assertEqual(len(self.adapter.calls),2)

    def test_blank_and_over_limit_validation(self):
        with self.assertRaises(MemoryError):self.runner.create(self.story,[{'prompt':'   '}],'blank-key')
        with self.assertRaises(ValidationError):SceneSequence(scenes=[{'prompt':'x'}]*6)
        with self.assertRaises(ValidationError):SceneSequence(scenes=[])
        with self.assertRaises(ValidationError):SceneSequence(scenes=[{'prompt':'x'}],idempotency_key='../escape')

    def test_later_scene_parent_override_is_rejected(self):
        with self.assertRaisesRegex(MemoryError,'Only the first'):
            self.runner.create(self.story,[{'prompt':'one'},{'prompt':'two','parent_scene_id':'a'*32}],'parent-key')

    def test_original_references_unchanged_across_sequence(self):
        char=self.memory.character(self.story,{'name':'Maya','role':'woman','aliases':['woman']})
        char=self.memory.add_reference(self.story,char['id'],Image.new('RGB',(256,256),'blue'),[0,0,.5,.5])
        self.run_sequence(self.create())
        after=self.memory.get('characters',char['id'])
        self.assertEqual(after['original_reference'],char['original_reference'])
        self.assertEqual(after['face_reference'],char['face_reference'])
        self.memory.asset(self.story,after['original_reference'])

    def test_completed_parent_can_start_a_new_branch(self):
        first=self.run_sequence(self.create(count=1))['items'][0]['scene_id']
        requests=[NextScene(prompt='A new branch.',parent_scene_id=first).model_dump()]
        new=self.run_sequence(self.runner.create(self.story,requests,'new-branch-key'))
        self.assertEqual(self.memory.get('scenes',new['items'][0]['scene_id'])['parent_scene_id'],first)

    def test_history_mutation_blocked_during_queue_gap(self):
        first=self.run_sequence(self.create(count=1))['items'][0]['scene_id']
        self.create(count=1,key='other-sequence-key')
        with self.assertRaises(MemoryError):self.memory.activate(self.story,first)
        with self.assertRaises(MemoryError):self.memory.delete_scene(self.story,first)

    def test_five_scene_sequence_prompt_continuity_and_distinct_seeds(self):
        prompts = [
            'A red ball sits in the center of a wooden tabletop.',
            'Add a small blue cup beside the red ball.',
            'Move the red ball in front of the blue cup.',
            'Place a closed green book next to the ball and cup.',
            'Show the same ball, blue cup and green book on the wooden tabletop from an overhead camera view.'
        ]
        requests = [NextScene(prompt=p, parent_scene_id='' if i==0 else None, seed=42+i, mode='fast').model_dump() for i, p in enumerate(prompts)]
        sequence = self.run_sequence(self.runner.create(self.story, requests, 'five-scene-prompt-check'))
        self.assertEqual(sequence['status'], 'succeeded')
        self.assertEqual(len(self.adapter.calls), 5)
        scene2_plan = continuity_engine.build_plan(self.memory, self.story, prompts[1], self.adapter.calls[0]['id'], character_ids=[])
        self.assertIn(prompts[1], scene2_plan['compiled_prompt'])
        self.assertNotIn('Preserve recognizable identities', scene2_plan['compiled_prompt'])
        self.assertNotIn('Selected cast:', scene2_plan['compiled_prompt'])
        scene5_plan = continuity_engine.build_plan(self.memory, self.story, prompts[4], self.adapter.calls[3]['id'], character_ids=[])
        self.assertIn(prompts[4], scene5_plan['compiled_prompt'])
        self.assertNotIn('Keep the existing camera composition', scene5_plan['compiled_prompt'])
        seeds = [call['settings']['seed'] for call in self.adapter.calls]
        self.assertEqual(len(set(seeds)), 5)
        for before, after in zip(self.adapter.calls, self.adapter.calls[1:]):
            self.assertEqual(after['parent_scene_id'], before['id'])



class SequenceApiTests(unittest.TestCase):
    def test_api_validation_persistence_resume_and_cross_story_access(self):
        with tempfile.TemporaryDirectory() as tmp, patch.dict('os.environ',{'QWEN_STORY_DIR':tmp}):
            # Fresh FastAPI surface only; no Studio lifespan, GPU worker or model inference.
            app=FastAPI()
            with patch.object(studio_service,'app',app):adapter=install(studio_service)
            adapter.sequences.autostart=False
            with TestClient(app) as client:
                self.assertEqual(client.get('/api/stories/config').json()['sequence_max_scenes'],5)
                story=client.post('/api/stories',json={'name':'API queue fixture'}).json()['id']
                other=client.post('/api/stories',json={'name':'Other story'}).json()['id']
                root='/api/stories/'+story
                body={'scenes':[{'prompt':'A ball.'},{'prompt':'The ball rolls.'}],'idempotency_key':'api-fixture-key'}
                response=client.post(root+'/sequences',json=body)
                self.assertEqual(response.status_code,202)
                sequence=response.json()
                self.assertEqual(client.post(root+'/sequences',json=body).json()['id'],sequence['id'])
                self.assertEqual(len(client.get(root).json()['sequences']),1)
                self.assertEqual(client.get(f'/api/stories/{other}/sequences/{sequence["id"]}').status_code,404)
                self.assertEqual(client.post(root+'/scenes',json={'prompt':'Concurrent scene.'}).status_code,409)
                self.assertEqual(client.post(root+'/sequences',json={'scenes':[{'prompt':'x'}]*6}).status_code,422)
                adapter.memory.recover()
                response=client.post(root+'/sequences/'+sequence['id']+'/resume',json={})
                self.assertEqual(response.status_code,202)
                self.assertEqual(response.json()['status'],'queued')


if __name__=='__main__':unittest.main()
