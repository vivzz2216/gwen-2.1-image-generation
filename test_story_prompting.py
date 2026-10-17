"""Story rewrite contracts; these mocks never count as a real model test."""
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from fastapi import FastAPI
from fastapi.testclient import TestClient
from PIL import Image
import dolphin_prompting as dolphin
import story_prompting as prompting
import story_api
import studio_service as studio


def reply(prompts):
    return json.dumps({'scenes':[{'scene':i,'prompt':p} for i,p in enumerate(prompts,1)]})


class StoryPromptingTests(unittest.TestCase):
    def test_previous_outfit_can_carry_but_future_color_cannot_leak(self):
        original=['Maya wears a beige cardigan.','Maya cries.','Change her cardigan to red.']
        result=['Maya wears a beige cardigan.','Maya cries, with tears on her cheeks. She keeps her beige cardigan.','Her cardigan is changed to red.']
        self.assertEqual(prompting.validate_reply(reply(result),original,{}),result)
        result[0]='Maya wears a red cardigan.'
        with self.assertRaisesRegex(dolphin.EnhancementError,'color'):
            prompting.validate_reply(reply(result),original,{})

    def test_unrequested_watercolor_is_rejected(self):
        with self.assertRaisesRegex(dolphin.EnhancementError,'watercolor'):
            prompting.validate_reply(reply(['A watercolor apple falls from the sky.']),['An apple falls from the sky.'],{})

    def test_unrequested_default_standing_is_neutralized(self):
        result=prompting.validate_reply(reply(['Roy and Maya are standing holding hands.']),['Roy and Maya hold hands.'],{})
        self.assertNotIn('standing',result[0])

    def test_crying_needs_visible_cues_not_only_grammar(self):
        with self.assertRaisesRegex(dolphin.EnhancementError,'crying visible'):
            prompting.validate_reply(reply(['Maya cries in the car.']),['Maya crying in the car.'],{})
        text='Maya cries in the car, with visible tears on her cheeks.'
        self.assertEqual(prompting.validate_reply(reply([text]),['Maya crying in the car.'],{}),[text])

    def test_body_part_side_and_exact_literal_are_retained(self):
        with self.assertRaises(dolphin.EnhancementError):
            prompting.validate_reply(reply(['Maya pushes Roy in the stomach with her right hand.']),['Maya pushes Roy in the chest with her left hand.'],{})
        text='A sign reads "Roy  and  Maya".'
        self.assertEqual(prompting.validate_reply(reply([text]),[text],{}),[text])

    def test_tears_running_are_not_a_running_pose(self):
        for phrase in ('tears run down her cheeks', 'tears running down her cheeks', 'tears are running down her cheeks', 'wet tear trails running down her cheeks'):
            text='Maya cries inside the car, with '+phrase+'.'
            self.assertEqual(prompting.validate_reply(reply([text]),['Maya crying inside a car.'],{}),[text])
        with self.assertRaisesRegex(dolphin.EnhancementError,'run pose'):
            prompting.validate_reply(reply(['Maya runs while crying, with tears running down her cheeks.']),['Maya crying.'],{})

    def test_count_order_and_empty_response_are_not_applied(self):
        for malformed in ['{}',reply(['A ball.']),json.dumps({'scenes':[{'scene':2,'prompt':'A ball.'},{'scene':1,'prompt':'A cup.'}]}),reply(['','A cup.'])]:
            with self.assertRaises(dolphin.EnhancementError):
                prompting.validate_reply(malformed,['A ball.','A cup.'],{})

    def test_input_boundaries_and_model_are_fixed_for_local_4b(self):
        for data in [[],['a']*6,['a',' '],['x'*4001],['x'*3000]*4]:
            with self.assertRaises(dolphin.EnhancementError):prompting.validate_inputs(data)
        with self.assertRaises(dolphin.EnhancementError):prompting.enhance(['A ball.'],{},model='qwen27b')

    def test_one_repair_uses_whole_story_and_no_larger_fallback(self):
        with patch.object(dolphin,'chat',side_effect=['bad json',reply(['Maya holds a cup in her hands.','Maya smiles.'])]) as chat:
            result=prompting.enhance(['Maya holds a cup.','Maya smiles.'],{'cast':[{'name':'Maya','current_outfit':'red shirt'}]})
        self.assertEqual(result['model_calls'],2)
        self.assertTrue(result['story_based'])
        payload=chat.call_args.args[0]
        self.assertEqual(payload['model'],'qwen21-qwen4b')
        self.assertFalse(payload['think'])
        self.assertEqual(payload['keep_alive'],0)
        self.assertIn('red shirt',payload['messages'][-1]['content'])

    def test_error_does_not_fall_back_to_original_as_fake_enhancement(self):
        with patch.object(dolphin,'chat',return_value='{}') as chat:
            with self.assertRaises(dolphin.EnhancementError):prompting.enhance(['A ball.'],{})
        self.assertEqual(chat.call_count,2)


class StoryEnhancementApiTests(unittest.TestCase):
    def test_context_is_parent_specific_and_wrong_story_is_rejected(self):
        with tempfile.TemporaryDirectory() as tmp, patch.dict('os.environ',{'QWEN_STORY_DIR':tmp}):
            app=FastAPI()
            with patch.object(studio,'app',app):adapter=story_api.install(studio)
            story=adapter.memory.create_story('Story')['id']
            char=adapter.memory.character(story,{'name':'Maya','role':'woman','current_clothing':'red shirt'})
            plan={'parent_scene_id':None,'character_ids':[char['id']],'clothing':{char['id']:'blue shirt'},'user_prompt':'Maya sits in a car.'}
            scene=adapter.memory.reserve(story,plan,{})
            scene=adapter.memory.finish(story,scene['id'],Image.new('RGB',(64,64)),{}, {})
            adapter.memory.character(story,{'current_clothing':'green shirt'},char['id'])
            other=adapter.memory.create_story('Other')['id']
            with TestClient(app) as client, patch.object(studio,'enhance_for_studio',return_value={'scenes':['Maya smiles.'],'story_based':True}) as enhance:
                response=client.post('/api/stories/'+story+'/enhance',json={'scenes':['Maya smiles.'],'parent_scene_id':scene['id']})
                self.assertEqual(response.status_code,200)
                self.assertEqual(enhance.call_args.kwargs['model'],'qwen21-qwen4b')
                context=enhance.call_args.kwargs['story_context']
                self.assertEqual(context['cast'][0]['current_outfit'],'blue shirt')
                self.assertEqual(context['previous_moment'],'Maya sits in a car.')
                self.assertEqual(client.post('/api/stories/'+other+'/enhance',json={'scenes':['Maya smiles.'],'parent_scene_id':scene['id']}).status_code,404)
                self.assertEqual(client.post('/api/stories/'+story+'/enhance',json={'scenes':[' ']}).status_code,422)
                self.assertEqual(client.post('/api/stories/'+story+'/enhance',json={'scenes':['x'*3000]*4}).status_code,422)
                self.assertEqual(enhance.call_count,1)


if __name__=='__main__':unittest.main()
