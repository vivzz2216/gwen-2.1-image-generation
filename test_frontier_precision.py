"""Unit tests for Frontier Precision Architecture (Adaptive Gate, Lighting Harmonization, Adaptive CodeFormer)."""
import unittest
import numpy as np
from PIL import Image

import face_detail


class TestFrontierPrecision(unittest.TestCase):
    def test_should_inpaint_adaptive_gate(self):
        # 1. Medium/large face with high base likeness -> bypass inpainting
        self.assertFalse(face_detail.should_inpaint((150, 180), current_similarity=0.78))
        self.assertFalse(face_detail.should_inpaint((200, 200), current_similarity=0.82))

        # 2. Medium/large face with low base likeness -> inpaint
        self.assertTrue(face_detail.should_inpaint((150, 180), current_similarity=0.68))

        # 3. Small face (<120px) -> always inpaint to reconstruct latent loss
        self.assertTrue(face_detail.should_inpaint((45, 55), current_similarity=0.78))
        self.assertTrue(face_detail.should_inpaint((80, 90), current_similarity=0.85))
        self.assertTrue(face_detail.should_inpaint((100, 100), current_similarity=None))

        # 4. Tiny face (<20px) -> skipped as undetectable/out-of-range
        self.assertFalse(face_detail.should_inpaint((15, 18), current_similarity=0.50))

    def test_adaptive_codeformer_weight(self):
        # Micro faces (<64px) need structural reconstruction without excessive plastic gloss
        self.assertEqual(face_detail.adaptive_codeformer_weight((45, 50)), 0.55)

        # Small faces (64-119px) need gentle stabilization keeping 70% raw diffusion texture
        self.assertEqual(face_detail.adaptive_codeformer_weight((80, 95)), 0.30)
        self.assertEqual(face_detail.adaptive_codeformer_weight((110, 115)), 0.30)

        # Large faces (>=120px) disable CodeFormer completely for 100% natural skin pores
        self.assertEqual(face_detail.adaptive_codeformer_weight((150, 180)), 0.0)
        self.assertEqual(face_detail.adaptive_codeformer_weight((300, 300)), 0.0)

    def test_harmonize_lighting_reinhard(self):
        # Create a source crop with warm ambient lighting (higher R, lower B)
        src_arr = np.full((128, 128, 3), (220, 160, 100), dtype=np.uint8)
        src_img = Image.fromarray(src_arr, mode="RGB")

        # Create an inpainted crop with cool studio lighting (higher B, lower R)
        inp_arr = np.full((128, 128, 3), (120, 140, 210), dtype=np.uint8)
        # Add a subtle gradient so std dev is non-zero
        inp_arr[:, :, 0] += np.linspace(0, 20, 128, dtype=np.uint8)[:, None]
        inp_img = Image.fromarray(inp_arr, mode="RGB")

        harmonized = face_detail.harmonize_lighting(src_img, inp_img)
        self.assertEqual(harmonized.size, inp_img.size)
        self.assertEqual(harmonized.mode, "RGB")

        harm_arr = np.array(harmonized)
        # After harmonization toward warm source, R should increase and B should decrease
        self.assertGreater(harm_arr[:, :, 0].mean(), inp_arr[:, :, 0].mean())
        self.assertLess(harm_arr[:, :, 2].mean(), inp_arr[:, :, 2].mean())

    def test_frontier_detail_prompt_lighting_anchors(self):
        prompt = face_detail.detail_prompt("having coffee by window", use_bfs_head=True)
        # Verify ambient lighting and natural texture instructions
        self.assertIn("lighting", prompt.lower())
        self.assertIn("environment", prompt.lower())
        self.assertIn("natural", prompt.lower())

    def test_composite_seamless_preserves_dimensions(self):
        original = Image.new("RGB", (512, 512), (100, 120, 140))
        refined_work = Image.new("RGB", (768, 768), (110, 130, 150))
        mask = Image.new("L", (768, 768), 255)

        detail_plan = {
            "crop_box": (100, 100, 356, 356),
            "mask": mask,
        }
        result = face_detail.composite(original, refined_work, detail_plan)
        self.assertEqual(result.size, (512, 512))
        self.assertEqual(result.mode, "RGB")


if __name__ == "__main__":
    unittest.main()
