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


if __name__ == '__main__':
    unittest.main()
