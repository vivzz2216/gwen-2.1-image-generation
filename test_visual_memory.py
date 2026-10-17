"""Persistence/control-flow tests use labeled bitmap fixtures; real inference is tested separately."""
import copy
import io
from pathlib import Path
import tempfile
import threading
import unittest
from unittest.mock import patch

from fastapi.testclient import TestClient
from PIL import Image
import continuity_engine as continuity
from visual_memory import MemoryError, StoryMemory
import studio_service as studio
import story_api


class MemoryTests(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory()
        self.memory=StoryMemory(Path(self.temp.name)/'stories')
        self.story=self.memory.create_story('Fixture story')['id']
        continuity.discover_characters(self.memory,self.story,'A woman in a red dress talks to a man in a black shirt inside an apartment.')
        self.characters=self.memory.snapshot(self.story)['characters']
        self.woman=next(c for c in self.characters if c['role']=='woman')['id']
        self.man=next(c for c in self.characters if c['role']=='man')['id']
        for key in (self.woman,self.man):
            self.memory.add_reference(self.story,key,Image.new('RGB',(256,256),'blue'),[0,0,.5,.5])

    def tearDown(self): self.temp.cleanup()

    def scene(self,prompt='A woman in a red dress talks to a man in a black shirt inside an apartment.',parent=None):
        plan=continuity.build_plan(self.memory,self.story,prompt,parent)
        reserved=self.memory.reserve(self.story,plan,{'mode':'fast'})
        return self.memory.finish(self.story,reserved['id'],Image.new('RGB',(256,256),'green'),{'unit_fixture':True},{'status':'not_measured'})

    def test_required_initial_action_continuation_and_rain(self):
        first=self.scene()
        self.assertEqual(first['clothing'][self.man],'black shirt')
        slap=self.scene('The woman slaps the man.')
        self.assertEqual(slap['parent_scene_id'],first['id'])
        self.assertEqual(set(slap['character_ids']),{self.woman,self.man})
        self.assertEqual(slap['clothing'][self.woman],'red dress')
        self.assertEqual(slap['location_id'],first['location_id'])
        self.assertLessEqual(len(slap['references']),3)
        self.assertEqual(slap['references'][1]['role'],'character_group')
        shock=self.scene('The man looks shocked and steps backward.')
        self.assertEqual(shock['parent_scene_id'],slap['id'])
        rain=self.scene('The woman runs outside into the rain.')
        self.assertEqual(rain['character_ids'],[self.woman])
        self.assertNotEqual(rain['location_id'],first['location_id'])
        self.assertIn('Do not force the previous location',rain['compiled_prompt'])
        self.assertNotIn('previous_scene',[r['role'] for r in rain['references']])

    def test_restart_and_branch_keep_original_images_and_parent_state(self):
        first=self.scene()
        changed=self.scene('Change her dress to blue.')
        self.assertEqual(changed['clothing'][self.woman],'blue dress')
        restored=StoryMemory(self.memory.root)
        self.assertEqual(restored.snapshot(self.story)['current_scene_id'],changed['id'])
        plan=continuity.build_plan(restored,self.story,'The woman waves.',first['id'])
        self.assertEqual(plan['clothing'][self.woman],'red dress')
        branch=restored.reserve(self.story,plan,{})
        restored.finish(self.story,branch['id'],Image.new('RGB',(128,128)),{}, {})
        self.assertEqual(len(restored.snapshot(self.story)['scenes']),3)
        self.assertTrue(restored.asset(self.story,first['image']).exists())

    def test_failure_and_recovery_leave_head_unchanged(self):
        first=self.scene()
        plan=continuity.build_plan(self.memory,self.story,'The man shouts.')
        pending=self.memory.reserve(self.story,plan,{})
        self.memory.recover()
        self.assertEqual(self.memory.get('scenes',pending['id'])['status'],'failed')
        self.assertEqual(self.memory.snapshot(self.story)['current_scene_id'],first['id'])
        self.memory.reserve(self.story,plan,{})

    def test_reference_original_is_immutable_and_additional_views_are_distinct(self):
        original=copy.deepcopy(self.memory.get('characters',self.woman))
        self.memory.add_reference(self.story,self.woman,Image.new('RGB',(256,256),'red'),[0,0,.5,.5])
        updated=self.memory.get('characters',self.woman)
        self.assertEqual(updated['original_reference'],original['original_reference'])
        self.assertEqual(updated['face_reference'],original['face_reference'])
        self.assertEqual(len(updated['references']),2)
        self.memory.asset(self.story,original['original_reference'])

    def test_new_person_does_not_reuse_old_identity_or_scene(self):
        self.scene()
        fresh=continuity.discover_characters(self.memory,self.story,'Create a completely new scene with a different girl.')
        plan=continuity.build_plan(self.memory,self.story,'Create a completely new scene with a different girl.')
        self.assertEqual(plan['character_ids'],fresh)
        self.assertFalse(plan['references'])
        self.assertNotIn(self.woman,plan['character_ids'])

    def test_ambiguous_alias_needs_selection_and_never_merges(self):
        other=self.memory.character(self.story,{'name':'Another woman','role':'woman','aliases':['woman','girl']})
        with self.assertRaisesRegex(MemoryError,'several characters'):
            continuity.build_plan(self.memory,self.story,'The woman smiles.')
        plan=continuity.build_plan(self.memory,self.story,'The woman smiles.',character_ids=[self.woman])
        self.assertEqual(plan['character_ids'],[self.woman])
        self.assertNotEqual(other['id'],self.woman)

    def test_missing_asset_fails_without_mutating_current_scene(self):
        first=self.scene()
        asset=self.memory.get('characters',self.woman)['face_reference']
        path=self.memory.asset(self.story,asset)
        path.rename(path.with_suffix('.saved'))
        with self.assertRaises(MemoryError): continuity.build_plan(self.memory,self.story,'The woman smiles.')
        self.assertEqual(self.memory.snapshot(self.story)['current_scene_id'],first['id'])

    def test_concurrent_reservation_accepts_one_job(self):
        plan=continuity.build_plan(self.memory,self.story,'The woman smiles.')
        results=[]
        def reserve():
            try: self.memory.reserve(self.story,plan,{}); results.append('accepted')
            except MemoryError: results.append('busy')
        threads=[threading.Thread(target=reserve) for _ in range(2)]
        for thread in threads: thread.start()
        for thread in threads: thread.join()
        self.assertCountEqual(results,['accepted','busy'])

    def test_scene_delete_preserves_branches_and_reference_files(self):
        first=self.scene(); second=self.scene('The man smiles.')
        with self.assertRaises(MemoryError): self.memory.delete_scene(self.story,first['id'])
        self.memory.delete_scene(self.story,second['id'])
        self.assertEqual(self.memory.snapshot(self.story)['current_scene_id'],first['id'])
        original=self.memory.get('characters',self.man)['original_reference']
        self.memory.delete_character(self.story,self.man)
        self.assertTrue(self.memory.asset(self.story,original).exists())

    def test_path_traversal_and_cross_story_rejected(self):
        for path in ('../memory.sqlite3','C:/Windows/win.ini','/Windows/win.ini'):
            with self.assertRaises(MemoryError): self.memory.path(self.story,path)
        other=self.memory.create_story('Other')['id']
        with self.assertRaises(MemoryError): self.memory.get('characters',self.man,other)

    def test_hair_change_is_carried_to_following_scene(self):
        self.scene()
        self.scene('Give the woman short hair.')
        following=continuity.build_plan(self.memory,self.story,'The woman smiles.')
        self.assertIn('Give the woman short hair.',following['compiled_prompt'])

    def test_shared_confirmed_scene_uses_head_board_not_old_composition(self):
        first=self.scene()
        for key in (self.woman,self.man):
            # A fresh character assignment from the same original scene is the real multi-person path.
            c=self.memory.get('characters',key)
            c['original_reference']=None; c['face_reference']=None; c['references']=[]
            with self.memory.connect() as db:self.memory._put(db,'characters',c)
            with Image.open(self.memory.asset(self.story,first['image'])) as image:
                self.memory.add_reference(self.story,key,image,[0,0,.5,.5],first['id'])
        plan=continuity.build_plan(self.memory,self.story,'The woman slaps the man.')
        self.assertEqual(len(plan['references']),2)
        group=plan['references'][1]
        self.assertEqual(set(group['character_ids']),{self.woman,self.man})
        self.assertFalse(group['shared_original_scene'])
        self.assertNotEqual(group['asset']['sha256'],first['image']['sha256'])

    def test_clothing_owner_is_not_the_other_actor(self):
        self.scene()
        plan=continuity.build_plan(self.memory,self.story,"The woman changes the man's shirt to blue.")
        self.assertEqual(plan['clothing'][self.woman],'red dress')
        self.assertEqual(plan['clothing'][self.man],'blue shirt')

    def test_explicit_location_change_overrides_old_location_selection(self):
        first=self.scene()
        plan=continuity.build_plan(self.memory,self.story,'Move them to a beach.',location_id=first['location_id'])
        self.assertTrue(plan['interpretation']['environment_change'])
        self.assertEqual(plan['new_location']['name'],'beach')

    def test_reference_board_is_reused_without_repeated_files(self):
        self.scene()
        plan=continuity.build_plan(self.memory,self.story,'The woman smiles.')
        second=continuity.build_plan(self.memory,self.story,'The woman smiles.')
        self.assertEqual(plan['references'][1]['asset'],second['references'][1]['asset'])

    def test_contact_roles_and_body_part_are_explicit(self):
        self.scene()
        slap=continuity.build_plan(self.memory,self.story,'The woman slaps the man.')
        self.assertEqual(slap['interpretation']['actor'],'woman')
        self.assertEqual(slap['interpretation']['recipient'],'man')
        self.assertEqual(slap['interpretation']['target_region'],'cheek')
        self.assertIn('palm-to-cheek',slap['compiled_prompt'])
        kick=continuity.build_plan(self.memory,self.story,'The man kicks the woman in the chest.')
        self.assertEqual(kick['interpretation']['target_region'],'chest')

    def test_clothing_only_change_does_not_request_new_pose_or_expression(self):
        self.scene()
        plan=continuity.build_plan(self.memory,self.story,'Change her dress to blue.')
        self.assertFalse(plan['interpretation']['change']['pose'])
        self.assertFalse(plan['interpretation']['change']['facial_expression'])
        self.assertNotIn('Keep the existing body pose',plan['compiled_prompt'])

    def test_unlisted_action_cannot_freeze_hands_or_expression(self):
        self.scene()
        plan=continuity.build_plan(self.memory,self.story,'The woman checks her phone for a notification.')
        self.assertTrue(plan['interpretation']['change']['pose'])
        self.assertNotIn('Keep the existing body pose',plan['compiled_prompt'])
        self.assertNotIn('Keep the existing facial expressions',plan['compiled_prompt'])
        self.assertIn('checks her phone',plan['compiled_prompt'])

    def test_car_change_drops_room_and_other_cast(self):
        self.scene()
        plan=continuity.build_plan(self.memory,self.story,'The woman is crying inside the car.')
        self.assertTrue(plan['interpretation']['environment_change'])
        self.assertEqual(plan['character_ids'],[self.woman])
        self.assertEqual(plan['new_location']['name'],'car')
        self.assertNotIn('previous_scene',[r['role'] for r in plan['references']])
        self.assertNotIn('Preserve the location',plan['compiled_prompt'])

    def test_outfit_change_keeps_unmentioned_garments_and_other_person(self):
        self.memory.character(self.story,{'default_clothing':'red dress, black boots','current_clothing':'red dress, black boots'},self.woman)
        first=self.scene('The woman talks to the man.')
        changed=self.scene('Change her dress to blue.')
        self.assertIn('blue dress',changed['clothing'][self.woman])
        self.assertIn('black boots',changed['clothing'][self.woman])
        self.assertEqual(changed['clothing'][self.man],first['clothing'][self.man])
        following=continuity.build_plan(self.memory,self.story,'The woman reads a book.')
        self.assertEqual(following['clothing'],changed['clothing'])
        branch=continuity.build_plan(self.memory,self.story,'The woman waves.',first['id'])
        self.assertIn('red dress',branch['clothing'][self.woman])

    def test_remove_jacket_persists_without_freezing_pose(self):
        self.memory.character(self.story,{'default_clothing':'blue shirt, black jacket, black trousers','current_clothing':'blue shirt, black jacket, black trousers'},self.man)
        self.scene('The man is standing beside the woman.')
        changed=self.scene('The man takes off his jacket.')
        self.assertIn('no jacket (removed)',changed['clothing'][self.man])
        self.assertIn('blue shirt',changed['clothing'][self.man])
        following=continuity.build_plan(self.memory,self.story,'The man sits on a bench.')
        self.assertEqual(following['clothing'][self.man],changed['clothing'][self.man])

    def test_same_location_does_not_unnecessarily_reset_reference(self):
        first=self.scene()
        plan=continuity.build_plan(self.memory,self.story,'The woman looks angry in the apartment.')
        self.assertFalse(plan['interpretation']['environment_change'])
        self.assertEqual(plan['location_id'],first['location_id'])
        self.assertEqual(plan['references'][0]['role'],'previous_scene')

    def test_explicit_solo_scene_does_not_force_the_old_partner_into_cast(self):
        self.scene()
        plan=continuity.build_plan(self.memory,self.story,'The woman is alone, reading a book.')
        self.assertEqual(plan['character_ids'],[self.woman])

    def test_major_pose_change_uses_confirmed_faces_on_new_composition(self):
        first=self.scene()
        plan=continuity.build_plan(self.memory,self.story,'The woman pushes the man in the chest.')
        self.assertEqual(plan['parent_scene_id'],first['id'])
        self.assertEqual(plan['transition_route'],'reference_recompose')
        self.assertNotIn('previous_scene',[r['role'] for r in plan['references']])
        self.assertEqual(plan['references'][0]['role'],'character_group')
        self.assertEqual(plan['clothing'],first['clothing'])

    def test_unconfirmed_faces_keep_source_instead_of_discarding_identity(self):
        story=self.memory.create_story('No face assignments')['id']
        continuity.discover_characters(self.memory,story,'A man in a gray shirt.')
        plan=continuity.build_plan(self.memory,story,'A man in a gray shirt.')
        first=self.memory.reserve(story,plan,{})
        self.memory.finish(story,first['id'],Image.new('RGB',(128,128)),{}, {})
        changed=continuity.build_plan(self.memory,story,'The man is riding a bike.')
        self.assertEqual(changed['transition_route'],'edit_previous')
        self.assertEqual(changed['references'][0]['role'],'previous_scene')

    def test_phone_action_can_reframe_portrait_and_keep_changed_outfit(self):
        first=self.scene('The woman is crying inside a parked car. Close-up of her face.')
        plan=continuity.build_plan(self.memory,self.story,'The woman looks down at a smartphone held in both hands. Change her dress to blue.',first['id'])
        self.assertEqual(plan['transition_route'],'reference_recompose')
        self.assertEqual(plan['references'][0]['role'],'character_identity')
        self.assertEqual(plan['clothing'][self.woman],'blue dress')

    def test_unrecorded_outfit_is_not_discarded_for_pose_recomposition(self):
        story=self.memory.create_story('Unknown outfit')['id']
        char=self.memory.character(story,{'name':'Maya','role':'woman','aliases':['woman']})
        self.memory.add_reference(story,char['id'],Image.new('RGB',(256,256),'blue'),[0,0,.5,.5])
        plan=continuity.build_plan(self.memory,story,'Maya stands in the park.')
        scene=self.memory.reserve(story,plan,{})
        self.memory.finish(story,scene['id'],Image.new('RGB',(256,256)),{}, {})
        plan=continuity.build_plan(self.memory,story,'Maya is riding a bike.')
        self.assertEqual(plan['transition_route'],'edit_previous')
        self.assertTrue(any('outfit is not recorded' in warning for warning in plan['warnings']))

    def test_named_cast_and_initial_outfits_are_discovered_independently(self):
        story=self.memory.create_story('Named cast')['id']
        prompt='Maya, an adult woman wearing a red jacket and blue jeans, stands beside Roy, an adult man wearing a black shirt and gray trousers.'
        continuity.discover_characters(self.memory,story,prompt)
        plan=continuity.build_plan(self.memory,story,prompt)
        cast={c['name']:c for c in self.memory.snapshot(story)['characters']}
        self.assertEqual(set(cast),{'Maya','Roy'})
        self.assertEqual(plan['clothing'][cast['Maya']['id']],'red jacket, blue jeans')
        self.assertEqual(plan['clothing'][cast['Roy']['id']],'black shirt, gray trousers')
        continuity.discover_characters(self.memory,story,prompt)
        self.assertEqual(len(self.memory.snapshot(story)['characters']),2)

    def test_two_outfit_changes_do_not_transfer_colors_between_people(self):
        self.scene()
        plan=continuity.build_plan(self.memory,self.story,'The woman wears a blue dress, and the man wears a green shirt.')
        self.assertEqual(plan['clothing'][self.woman],'blue dress')
        self.assertEqual(plan['clothing'][self.man],'green shirt')
        self.assertEqual(continuity.garment_parts('gray shirt and dark blue jeans and white shoes'),
                         {'shirt':'gray shirt','jeans':'dark blue jeans','shoes':'white shoes'})

    def test_deleted_character_is_not_retrieved_from_parent_cast(self):
        self.scene()
        self.memory.delete_character(self.story,self.man)
        plan=continuity.build_plan(self.memory,self.story,'The woman smiles.')
        self.assertEqual(plan['character_ids'],[self.woman])

    def test_object_and_camera_prompt_compilation_without_characters(self):
        first=self.scene('A red ball sits in the center of a wooden tabletop.')
        plan_add=continuity.build_plan(self.memory,self.story,'Add a small blue cup beside the red ball.',first['id'],character_ids=[])
        self.assertNotIn('Preserve recognizable identities',plan_add['compiled_prompt'])
        self.assertNotIn('Preserve the current clothes',plan_add['compiled_prompt'])
        self.assertNotIn('Keep the existing body pose',plan_add['compiled_prompt'])
        self.assertNotIn('Keep the existing facial expressions',plan_add['compiled_prompt'])
        self.assertIn('Add a small blue cup',plan_add['compiled_prompt'])

        plan_cam=continuity.build_plan(self.memory,self.story,'Show the ball from an overhead camera view.',first['id'],character_ids=[])
        self.assertNotIn('Keep the existing camera composition',plan_cam['compiled_prompt'])
        self.assertIn('overhead camera view',plan_cam['compiled_prompt'])


class StoryAdapterTests(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory()
        self.root=Path(self.temp.name)
        self.memory=StoryMemory(self.root/'stories')
        self.adapter=story_api.StoryAdapter(studio,self.memory)
        self.story=self.memory.create_story('API fixture')['id']
        self.patches=[patch.object(studio,'DB_PATH',self.root/'jobs.sqlite3'),
            patch.object(studio,'OUTPUT_DIR',str(self.root)),patch.object(studio,'PROJECT_DIR',self.root),
            patch.object(studio,'STORY_ADAPTER',self.adapter),patch.object(studio,'windows_system_stats',return_value={}),
            patch.object(studio.EXECUTOR,'submit')]
        for p in self.patches: p.start()

    def tearDown(self):
        for p in reversed(self.patches): p.stop()
        self.temp.cleanup()

    def test_simulated_oom_retains_previous_scene_and_all_originals(self):
        initial=self.adapter.submit(self.story,story_api.NextScene(prompt='A woman in a red dress.'))
        self.memory.finish(self.story,initial['id'],Image.new('RGB',(128,128)),{}, {})
        next_scene=self.adapter.submit(self.story,story_api.NextScene(prompt='The woman smiles.'))
        def fail(job_id,event): studio.update_job(job_id,status='failed',error='CUDA out of memory')
        with patch.object(studio,'process_job',side_effect=fail): self.adapter.run(next_scene['job_id'],threading.Event())
        self.assertEqual(self.memory.snapshot(self.story)['current_scene_id'],initial['id'])
        failure=self.memory.get('scenes',next_scene['id'])
        self.assertIn('Try Fast',failure['error'])
        self.assertEqual(failure['status'],'failed')
        retry=self.adapter.submit(self.story,story_api.NextScene(prompt='The woman smiles.',mode='fast'))
        self.assertEqual(retry['parent_scene_id'],initial['id'])

    def test_job_prompt_privacy_and_native_reference_mapping(self):
        first=self.adapter.submit(self.story,story_api.NextScene(prompt='A woman in a red dress.'))
        self.memory.finish(self.story,first['id'],Image.new('RGB',(128,128)),{}, {})
        second=self.adapter.submit(self.story,story_api.NextScene(prompt='The woman smiles.'))
        job=studio.get_job(second['job_id'])
        self.assertEqual(job['request']['prompt'],'[Protected]')
        self.assertNotIn('compiled_prompt',job['request'])
        self.assertEqual(job['request']['task'],'edit')
        self.assertEqual(self.adapter.compiled(job['request']),second['compiled_prompt'])


if __name__=='__main__': unittest.main()
