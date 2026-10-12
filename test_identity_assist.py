import io
import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from fastapi.testclient import TestClient
from PIL import Image
import identity_assist as identity
import studio_service as studio
import qwen_engine as engine
import dolphin_prompting as dolphin

def bitmap(color='blue', mask=False):
    image = Image.new('RGB', (256, 256), color)
    if mask:
        image.paste('white', (30, 120, 220, 230))
    data = io.BytesIO(); image.save(data, 'PNG'); return data.getvalue()

class IdentityTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        root = Path(self.temp.name)
        self.patches = [patch.object(studio, 'OUTPUT_DIR', str(root)), patch.object(studio, 'STATE_DIR', root),
            patch.object(studio, 'DB_PATH', root/'jobs.sqlite3'), patch.object(studio, 'COMFY_INPUT_DIR', root/'input'),
            patch.object(studio.EXECUTOR, 'submit')]
        for item in self.patches: item.start()
        studio.COMFY_INPUT_DIR.mkdir(); self.client = TestClient(studio.app)

    def tearDown(self):
        for item in reversed(self.patches): item.stop()
        self.temp.cleanup()

    def submit(self, task='generate', **kwargs):
        data = {'task': task, 'prompt': 'The person raises the right hand.', 'identity_enabled': 'true',
            'identity_reviewed': 'true', 'identity_box': '[0.2,0.1,0.5,0.5]', 'lora_enabled': 'false', **kwargs}
        files = {'identity_reference': ('identity.png', bitmap(), 'image/png')}
        if task == 'edit': files['image'] = ('source.png', bitmap(), 'image/png')
        if data.get('pose_enabled') == 'true': files['pose_reference'] = ('pose.png', bitmap('red'), 'image/png')
        return self.client.post('/api/jobs', data=data, files=files)

    def test_missing_photo_and_approval_rejected(self):
        self.assertEqual(self.client.post('/api/jobs', data={'prompt': 'A person', 'identity_enabled': 'true'}).status_code, 422)
        self.assertEqual(self.submit(identity_reviewed='false').status_code, 422)

    def test_invalid_box_not_clamped_or_accepted(self):
        for box in ('[]', '[0,0,2,1]', '[NaN,0,1,1]', '[true,0,1,1]', '[0,0,0.01,0.01]', '{}', 'null'):
            with self.subTest(box=box): self.assertEqual(self.submit(identity_box=box).status_code, 422)

    def test_create_uses_reference_only_and_keeps_paths_private(self):
        response = self.submit(); self.assertEqual(response.status_code, 202, response.text)
        public = response.json()['request']; self.assertNotIn('identity_head_path', public)
        job = studio.get_job(response.json()['job_id'])['request']
        self.assertTrue(job['reference_only']); self.assertFalse(job['preserve']); self.assertIsNone(job['source_url'])
        self.assertEqual(len(job['reference_paths']), 1)
        with Image.open(job['identity_head_path']) as head: self.assertEqual(head.size, (128, 128))
        self.assertEqual(job['prompt'], '[Protected]')

    def test_pose_slot_follows_identity_crop(self):
        response = self.submit(pose_enabled='true', pose_reviewed='true')
        self.assertEqual(response.status_code, 202, response.text)
        req = studio.get_job(response.json()['job_id'])['request']
        self.assertEqual(req['reference_paths'][0], req['identity_head_path'])
        self.assertIn('pose_reference', req['reference_paths'][1])
        compiled = studio.compile_prompt('Raise the right arm', identity_enabled=True, pose_enabled=True, task='generate')
        self.assertIn('<image2> is the reviewed identity head crop', compiled)
        self.assertIn('<image3> supplies only body pose', compiled)
        self.assertNotIn('preserve pose', compiled.lower())

    def test_edit_movement_does_not_use_garment_mask(self):
        response = self.submit(task='edit', preserve='true', region='auto')
        self.assertEqual(response.status_code, 202, response.text)
        req = studio.get_job(response.json()['job_id'])['request']
        self.assertEqual(req['region'], 'global'); self.assertIsNone(req['mask_path'])

    def test_appearance_reference_conflict_is_explicit(self):
        response = self.client.post('/api/jobs', data={'prompt': 'A person', 'identity_enabled': 'true', 'identity_reviewed': 'true'},
            files={'identity_reference': ('i.png', bitmap(), 'image/png'), 'reference': ('r.png', bitmap(), 'image/png')})
        self.assertEqual(response.status_code, 422); self.assertIn('reserves reference slots', response.text)

    def test_batch_passes_shared_identity(self):
        response = self.client.post('/api/jobs/batch', data={'prompts': '["Walk", "Sit"]', 'identity_enabled': 'true',
            'identity_reviewed': 'true', 'identity_box': '[0,0,0.5,0.5]', 'lora_enabled': 'false'},
            files={'identity_reference': ('i.png', bitmap(), 'image/png')})
        self.assertEqual(response.status_code, 202, response.text)
        for job_id in response.json()['job_ids']:
            self.assertTrue(studio.get_job(job_id)['request']['identity_enabled'])

    @patch.object(identity, 'detect', return_value={'candidates': [], 'message': 'No clear face'})
    def test_analysis_does_not_persist_photos(self, detect):
        root = Path(studio.OUTPUT_DIR); before = set(root.iterdir())
        response = self.client.post('/api/identity/analyze', files={'image': ('i.png', bitmap(), 'image/png')})
        self.assertEqual(response.status_code, 200); self.assertEqual(set(root.iterdir()), before)

    def test_reference_only_respects_selected_canvas_not_photo_ratio(self):
        flow = engine.build_workflow('Walk', source_name='identity.png', reference_names=['head.png'], reference_only=True, mode='fast', aspect_ratio='3:4')
        self.assertEqual(flow['7']['inputs']['latent_image'], ['6', 0])
        self.assertEqual((flow['6']['inputs']['width'], flow['6']['inputs']['height']), engine.generation_dimensions('fast', '3:4'))
        self.assertIn('images.image_2', flow['5']['inputs'])
        with self.assertRaises(ValueError): engine.build_workflow('Walk', reference_only=True)
        with self.assertRaises(ValueError): engine.build_workflow('Walk', source_name='i.png', mask_name='m.png', reference_only=True)

    @patch.object(dolphin, 'chat', return_value='{"rewritten_prompt":"The person in <image1>, with identity from <image2>, raises the right arm above the head using the pose in <image3>."}')
    def test_enhance_receives_correct_identity_pose_roles(self, chat):
        dolphin.enhance('The person raises the right arm above the head', task='edit', reference_count=2,
                       identity_assist=True, pose_assist=True, enhancement_style='polish')
        brief = json.loads(chat.call_args.args[0]['messages'][-1]['content'])
        self.assertIn('Image 2 is the selected identity head crop', brief['reference_policy'])
        self.assertIn('Image 3 supplies body pose only', brief['reference_policy'])

    def test_head_crop_and_advisory_unavailable(self):
        image = Image.new('RGB', (256, 256))
        self.assertEqual(identity.crop_head(image, [0, 0, .5, .5]).size, (128, 128))
        with patch.object(identity, 'capabilities', return_value={'cpu_similarity': False}):
            self.assertTrue(identity.similarity(image, image)['advisory'])

    def test_face_repair_requires_reviewed_identity(self):
        response = self.client.post('/api/jobs', data={'task': 'edit', 'prompt': 'Repair likeness', 'repair_kind': 'face'},
            files={'image': ('source.png', bitmap(), 'image/png')})
        self.assertEqual(response.status_code, 422)
        self.assertIn('approved Identity Assist', response.text)

if __name__ == '__main__': unittest.main()
