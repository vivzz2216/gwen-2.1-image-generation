"""Unit tests for CodeFormer Face Restoration module."""
import unittest
from pathlib import Path
from PIL import Image

import face_restore


class FaceRestoreTests(unittest.TestCase):
    def test_availability(self):
        self.assertTrue(face_restore.is_available())

    def test_restore_face_preserves_dimensions_and_mode(self):
        input_im = Image.new("RGB", (768, 768), (140, 120, 100))
        restored = face_restore.restore_face(input_im, fidelity=0.8)
        self.assertIsInstance(restored, Image.Image)
        self.assertEqual(restored.size, (768, 768))
        self.assertEqual(restored.mode, "RGB")

    def test_restore_face_fidelity_bounds(self):
        input_im = Image.new("RGB", (512, 512), (180, 150, 120))
        # Valid fidelity range should execute cleanly
        r_low = face_restore.restore_face(input_im, fidelity=0.2)
        r_high = face_restore.restore_face(input_im, fidelity=0.9)
        self.assertEqual(r_low.size, (512, 512))
        self.assertEqual(r_high.size, (512, 512))
