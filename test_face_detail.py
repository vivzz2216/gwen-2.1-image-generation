"""Unit tests for Face Detail planning and compositing."""
import unittest
from PIL import Image, ImageChops

import face_detail


class FaceDetailTests(unittest.TestCase):
    def test_plan_skips_when_no_face_or_multiple_faces(self):
        self.assertIsNone(face_detail.plan((896, 672), []))
        self.assertIsNone(face_detail.plan((896, 672), [
            {'box': (100, 100, 50, 60), 'width': 50, 'height': 60},
            {'box': (300, 100, 50, 60), 'width': 50, 'height': 60},
        ]))

    def test_plan_skips_when_face_is_already_large(self):
        faces = [{'box': (200, 150, 280, 320), 'width': 280, 'height': 320}]
        self.assertIsNone(face_detail.plan((896, 672), faces))

    def test_plan_skips_when_face_is_too_tiny(self):
        faces = [{'box': (200, 150, 12, 14), 'width': 12, 'height': 14}]
        self.assertIsNone(face_detail.plan((896, 672), faces))

    def test_plan_returns_valid_crop_and_mask_for_small_face(self):
        # A face like in the cycling case (57 x 72)
        faces = [{'box': (425.0, 61.0, 57.0, 72.0), 'width': 57, 'height': 72}]
        plan = face_detail.plan((896, 672), faces)
        self.assertIsNotNone(plan)
        x0, y0, x1, y1 = plan['crop_box']
        self.assertGreaterEqual(x0, 0)
        self.assertGreaterEqual(y0, 0)
        self.assertLessEqual(x1, 896)
        self.assertLessEqual(y1, 672)
        # Crop should be square
        self.assertEqual(x1 - x0, y1 - y0)
        # Face center should be inside crop
        fcx = 425.0 + 57.0 / 2
        fcy = 61.0 + 72.0 / 2
        self.assertLess(x0, fcx)
        self.assertGreater(x1, fcx)
        self.assertLess(y0, fcy)
        self.assertGreater(y1, fcy)

        # Check mask properties
        mask = plan['mask']
        self.assertIsInstance(mask, Image.Image)
        self.assertEqual(mask.size, (768, 768))
        self.assertEqual(mask.mode, 'L')
        # Mask center should be bright, edges should be black
        self.assertEqual(mask.getpixel((0, 0)), 0)
        self.assertEqual(mask.getpixel((767, 767)), 0)

    def test_plan_creates_landmark_contour_mask_for_tilted_face(self):
        # Face with roll tilt and 5 landmarks: [r_eye, l_eye, nose, r_mouth, l_mouth]
        faces = [{
            'box': (400.0, 100.0, 80.0, 100.0),
            'width': 80,
            'height': 100,
            'landmarks': [420.0, 130.0, 460.0, 140.0, 440.0, 155.0, 425.0, 175.0, 455.0, 180.0]
        }]
        plan = face_detail.plan((896, 672), faces)
        self.assertIsNotNone(plan)
        mask = plan['mask']
        self.assertEqual(mask.size, (768, 768))
        self.assertTrue(plan.get('landmark_aligned', False))
        # Mask around face center should be high confidence
        cx, cy = int(plan['work_face_center'][0]), int(plan['work_face_center'][1])
        self.assertGreater(mask.getpixel((cx, cy)), 200)

    def test_prompt_includes_expected_references(self):
        text = face_detail.prompt("Riding a bicycle")
        self.assertIn("<image1>", text)
        self.assertIn("<image2>", text)
        self.assertIn("expression", text.lower())

        bfs_text = face_detail.prompt("Riding a bicycle", use_bfs_head=True)
        self.assertIn("head_swap:", bfs_text)
        self.assertIn("<image1>", bfs_text)
        self.assertIn("<image2>", bfs_text)
        self.assertIn("strictly preserving", bfs_text)

    def test_composite_preserves_unmasked_pixels(self):
        original = Image.new('RGB', (896, 672), (100, 150, 200))
        faces = [{'box': (425.0, 61.0, 57.0, 72.0), 'width': 57, 'height': 72}]
        plan = face_detail.plan(original.size, faces)
        self.assertIsNotNone(plan)

        # Refined crop filled with bright red
        refined_work = Image.new('RGB', (768, 768), (255, 0, 0))
        result = face_detail.composite(original, refined_work, plan)

        self.assertEqual(result.size, original.size)
        # Outside crop region, pixel must be bit-exact to original
        x0, y0, x1, y1 = plan['crop_box']
        if x0 > 10:
            self.assertEqual(result.getpixel((5, 5)), original.getpixel((5, 5)))
        self.assertEqual(result.getpixel((10, 600)), original.getpixel((10, 600)))


    def test_bfs_head_workflow_structure(self):
        import qwen_engine as engine
        bfs_prompt = face_detail.prompt("Riding a bicycle", use_bfs_head=True)
        wf = engine.build_workflow(
            bfs_prompt,
            mode="balanced",
            steps=20,
            source_name="crop.png",
            source_size=(768, 768),
            reference_names=("head.png",),
            mask_name="mask.png",
            lora_name="bfs_head_v1.1_qwen_2.1.safetensors",
            lora_strength=1.0,
            denoise=0.65,
        )
        self.assertIn("1b", wf)
        self.assertEqual(wf["1b"]["class_type"], "LoraLoaderModelOnly")
        self.assertEqual(wf["1b"]["inputs"]["lora_name"], "bfs_head_v1.1_qwen_2.1.safetensors")
        self.assertEqual(wf["1b"]["inputs"]["strength_model"], 1.0)
        self.assertEqual(wf["7"]["inputs"]["denoise"], 0.65)
        self.assertIn("images.image_1", wf["5"]["inputs"])
        self.assertIn("images.image_2", wf["5"]["inputs"])
        self.assertIn("head_swap:", wf["5"]["inputs"]["prompt"])

    def test_adaptive_denoise_scales_with_face_size(self):
        # Micro face (<64px)
        self.assertAlmostEqual(face_detail.adaptive_denoise((50, 60)), 0.66, places=2)
        # Medium face (64 - 160px)
        self.assertAlmostEqual(face_detail.adaptive_denoise((100, 120)), 0.62, places=2)
        # Large face (>160px)
        self.assertAlmostEqual(face_detail.adaptive_denoise((200, 220)), 0.52, places=2)

    # --- Pillar 2: Expression-Aware Prompting ---

    def test_prompt_with_action_context_bfs(self):
        """BFS head prompt should include action-matching expression guidance."""
        text = face_detail.prompt("skydiving from a plane", use_bfs_head=True, action_context="exhilarated expression, wind-distorted features, wide eyes")
        self.assertIn("head_swap:", text)
        self.assertIn("exhilarated", text)
        self.assertIn("<image1>", text)
        self.assertIn("<image2>", text)

    def test_prompt_with_action_context_standard(self):
        """Standard prompt should include action-matching expression guidance."""
        text = face_detail.prompt("running in a park", use_bfs_head=False, action_context="determined expression, focused gaze forward")
        self.assertIn("determined expression", text)
        self.assertIn("<image1>", text)
        self.assertIn("<image2>", text)

    def test_prompt_detects_action_from_user_prompt(self):
        """Prompt should auto-detect actions and inject expression guidance."""
        text = face_detail.prompt("crying at a funeral", use_bfs_head=True)
        self.assertIn("tears", text.lower())

    def test_prompt_no_action_context_is_backward_compatible(self):
        """Without action_context, prompt should be identical to old behavior."""
        text = face_detail.prompt("some random prompt", use_bfs_head=True)
        self.assertIn("head_swap:", text)
        self.assertIn("strictly preserving", text)
        # No expression guidance injected for unknown actions
        self.assertNotIn("expression, matching", text)

    # --- Pillar 1: Two-Pass Inpainting Config ---

    def test_two_pass_config_returns_both_passes(self):
        """Two-pass config should return structure and detail pass configurations."""
        config = face_detail.two_pass_config((57, 72))
        self.assertIn("pass_1", config)
        self.assertIn("pass_2", config)
        # Pass 1 (structure) should have higher denoise than pass 2 (detail)
        self.assertGreater(config["pass_1"]["denoise"], config["pass_2"]["denoise"])
        # Pass 2 detail denoise should be identity-safe (< 0.35)
        self.assertLess(config["pass_2"]["denoise"], 0.35)
        # Both should specify steps
        self.assertGreater(config["pass_1"]["steps"], 0)
        self.assertGreater(config["pass_2"]["steps"], 0)

    def test_two_pass_config_adapts_to_face_size(self):
        """Micro faces need more reconstruction freedom, large faces need less."""
        micro = face_detail.two_pass_config((30, 40))
        medium = face_detail.two_pass_config((100, 120))
        large = face_detail.two_pass_config((200, 220))
        # Structure denoise: micro > medium > large
        self.assertGreater(micro["pass_1"]["denoise"], medium["pass_1"]["denoise"])
        self.assertGreater(medium["pass_1"]["denoise"], large["pass_1"]["denoise"])
        # Detail denoise follows same pattern
        self.assertGreater(micro["pass_2"]["denoise"], medium["pass_2"]["denoise"])
        self.assertGreater(medium["pass_2"]["denoise"], large["pass_2"]["denoise"])

    def test_two_pass_config_lora_strengths(self):
        """Pass 1 should use full LoRA strength, pass 2 should be reduced."""
        config = face_detail.two_pass_config((80, 100))
        self.assertGreaterEqual(config["pass_1"]["lora_strength"], 0.9)
        self.assertLessEqual(config["pass_2"]["lora_strength"], 0.7)

    # --- Pillar 5: Smart Restoration Blending ---

    def test_smart_restore_skips_tiny_faces(self):
        """Smart restore should skip restoration for tiny faces."""
        crop = Image.new("RGB", (768, 768), (140, 120, 100))
        result = face_detail.smart_restore(crop, crop, (40, 50))
        # For tiny faces, output should be identical to input
        self.assertEqual(result.size, crop.size)

    def test_smart_restore_blends_for_medium_faces(self):
        """Smart restore should apply partial blending for medium faces."""
        crop = Image.new("RGB", (768, 768), (140, 120, 100))
        result = face_detail.smart_restore(crop, crop, (120, 150))
        self.assertEqual(result.size, crop.size)
        self.assertEqual(result.mode, "RGB")

    # --- Pillar 4: Precision Denoise Scheduling ---

    def test_precision_denoise_schedule_structure_pass(self):
        """Structure pass should have moderate denoise."""
        sched = face_detail.precision_denoise_schedule((80, 100), "structure")
        self.assertGreater(sched["denoise"], 0.45)
        self.assertLess(sched["denoise"], 0.65)
        self.assertGreater(sched["steps"], 10)

    def test_precision_denoise_schedule_detail_pass(self):
        """Detail pass should have low, identity-safe denoise."""
        sched = face_detail.precision_denoise_schedule((80, 100), "detail")
        self.assertLess(sched["denoise"], 0.35)
        self.assertGreater(sched["steps"], 5)


if __name__ == '__main__':
    unittest.main()
