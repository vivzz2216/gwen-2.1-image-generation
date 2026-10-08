import unittest

import numpy as np
from PIL import Image

from editing_regions import (MaskError, composite_preserving, crop_bounds,
                             detect_edit_target, inner_feather,
                             normalize_manual_mask, prepare_segmentation_input,
                             proposal_from_labels)


class EditRegionTests(unittest.TestCase):
    def test_oversized_segmentation_input_bounds_memory_preserves_aspect_and_source(self):
        source = Image.new("RGBA", (2400, 1600), (12, 34, 56, 0))
        source.paste((10, 20, 30, 255), (800, 400, 1600, 1200))
        bounded = prepare_segmentation_input(source)
        self.assertEqual(bounded.size, (1024, 683))
        self.assertEqual(bounded.mode, "RGB")
        self.assertEqual(bounded.getpixel((0, 0)), (255, 255, 255))
        self.assertEqual(bounded.getpixel((512, 341)), (10, 20, 30))
        self.assertEqual(source.size, (2400, 1600))
        self.assertEqual(source.getpixel((0, 0)), (12, 34, 56, 0))

    def test_preserved_clauses_do_not_select_background(self):
        self.assertEqual(detect_edit_target("Keep me and the background exactly the same, but change my clothes."), "outfit")
        self.assertEqual(detect_edit_target("Keep background and change my shirt"), "upper_clothes")
        self.assertEqual(detect_edit_target("Change my shirt and keep the background same"), "upper_clothes")
        self.assertEqual(detect_edit_target("Change my outfit while preserving the background"), "outfit")
        self.assertEqual(detect_edit_target("Change clothes without touching the background"), "outfit")
        self.assertEqual(detect_edit_target("Replace this with red", "top"), "upper_clothes")
        self.assertEqual(detect_edit_target("Add shades", "eyewear"), "sunglasses")
        self.assertEqual(detect_edit_target("Add sunglasses to me"), "sunglasses")
        self.assertEqual(detect_edit_target("Replace my shoes"), "shoes")
        with self.assertRaises(MaskError):
            detect_edit_target("Make this wonderful")
        with self.assertRaises(MaskError):
            detect_edit_target("Change my clothes and add sunglasses")

    def test_feather_and_composite_never_change_protected_rgba_pixels(self):
        rng = np.random.default_rng(12)
        pixels = rng.integers(0, 256, (60, 80, 4), dtype=np.uint8)
        source = Image.fromarray(pixels)
        mask = Image.new("L", source.size)
        mask.paste(255, (20, 20, 60, 45))
        feather = np.asarray(inner_feather(mask, 6))
        protected = np.asarray(mask) == 0
        self.assertTrue(np.all(feather[protected] == 0))
        result = np.asarray(composite_preserving(source, Image.new("RGB", (40, 40), "red"), mask, 6))
        np.testing.assert_array_equal(result[protected], pixels[protected])
        np.testing.assert_array_equal(result[:, :, 3], pixels[:, :, 3])
        self.assertTrue(np.any(result[~protected, :3] != pixels[~protected, :3]))

    def test_crop_preserves_source_dimensions_and_exterior(self):
        source = Image.new("RGB", (100, 80), "blue")
        mask = Image.new("L", source.size)
        mask.paste(255, (30, 20, 45, 40))
        crop = crop_bounds(mask, min_size=30)
        result = composite_preserving(source, Image.new("RGB", (10, 10), "red"), mask, feather=0, crop_box=crop)
        self.assertEqual(result.size, source.size)
        self.assertEqual(result.getpixel((35, 30)), (255, 0, 0))
        self.assertEqual(result.getpixel((0, 0)), (0, 0, 255))
        self.assertEqual(result.getpixel((46, 30)), (0, 0, 255))

    def test_transparent_brush_and_empty_mask(self):
        brush = Image.new("RGBA", (20, 20))
        brush.paste((255, 0, 0, 255), (5, 5, 10, 10))
        self.assertEqual(normalize_manual_mask(brush, (40, 40)).getbbox(), (10, 10, 20, 20))
        with self.assertRaises(MaskError):
            normalize_manual_mask(Image.new("L", (10, 10)), (10, 10))

    def test_semantic_masks_protect_face_hands_and_shoes(self):
        labels = np.zeros((100, 80), dtype=np.uint8)
        labels[5:30, 30:50] = 11
        labels[30:65, 20:60] = 4
        labels[45:55, 20:25] = 14
        labels[65:88, 25:55] = 6
        labels[88:98, 25:55] = 9
        scores = np.full(labels.shape, .95)
        proposal = proposal_from_labels((80, 100), labels, scores, "outfit", grow=3)
        selected = np.asarray(proposal.mask) > 0
        self.assertFalse(np.any(selected[labels == 11]))
        self.assertFalse(np.any(selected[labels == 14]))
        self.assertFalse(np.any(selected[labels == 9]))
        self.assertTrue(np.all(selected[labels == 4]))

    def test_absent_accessory_is_marked_estimated_and_requires_review(self):
        labels = np.zeros((100, 80), dtype=np.uint8)
        labels[5:35, 25:55] = 11
        labels[35:90, 20:60] = 4
        scores = np.full(labels.shape, .95)
        for target in ("sunglasses", "necklace"):
            proposal = proposal_from_labels((80, 100), labels, scores, target)
            self.assertEqual(proposal.method, "estimated_face_anchor")
            self.assertTrue(proposal.requires_review)
            self.assertTrue(proposal.mask.getbbox())
            self.assertTrue(any("not a segmented" in item for item in proposal.warnings))

    def test_missing_subject_and_multiple_people_require_manual_selection(self):
        labels = np.zeros((60, 60), dtype=np.uint8)
        scores = np.full(labels.shape, .95)
        with self.assertRaises(MaskError):
            proposal_from_labels((60, 60), labels, scores, "outfit")
        labels[10:30, 10:30] = 4
        with self.assertRaises(MaskError):
            proposal_from_labels((60, 60), labels, scores, "outfit", info={"person_count": 0})
        with self.assertRaises(MaskError):
            proposal_from_labels((60, 60), labels, scores, "outfit", info={"face_count": 2})


if __name__ == "__main__":
    unittest.main()
