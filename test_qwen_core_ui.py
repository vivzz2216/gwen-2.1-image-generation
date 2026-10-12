"""Guards the named identity section without introducing another generation flow."""
from html.parser import HTMLParser
from pathlib import Path
import unittest

ROOT = Path(__file__).resolve().parent


class Elements(HTMLParser):
    def __init__(self, html):
        super().__init__()
        self.ids = []
        self.attributes = {}
        self.feed(html)

    def handle_starttag(self, tag, attrs):
        attributes = dict(attrs)
        if 'id' in attributes:
            self.ids.append(attributes['id'])
            self.attributes[attributes['id']] = (tag, attributes)


class CoreStudioTests(unittest.TestCase):
    def setUp(self):
        self.html = (ROOT / 'web/index.html').read_text(encoding='utf-8')
        self.js = (ROOT / 'web/studio.js').read_text(encoding='utf-8')
        self.elements = Elements(self.html)

    def test_one_named_section_before_existing_prompt(self):
        self.assertIn('Qwen-Image-Core Studio', self.html)
        self.assertEqual(len(self.elements.ids), len(set(self.elements.ids)))
        self.assertLess(self.elements.ids.index('identityDetails'), self.elements.ids.index('prompt'))
        self.assertIn('open', self.elements.attributes['identityDetails'][1])
        self.assertEqual(self.html.count('id="runButton"'), 1)
        self.assertEqual(self.html.count('class="prompt-textarea"'), 1)

    def test_feature_stays_optional_and_approval_is_not_automatic(self):
        self.assertNotIn('checked', self.elements.attributes['identityEnabled'][1])
        self.assertIn('hidden', self.elements.attributes['identityControls'][1])
        self.assertIn('disabled', self.elements.attributes['identityContinue'][1])
        self.assertIn('$("identityReviewed").addEventListener("change", updateRun)', self.js)
        self.assertIn('getActivePromptTextarea().focus()', self.js)
        self.assertNotIn('$("identityReviewed").checked = true', self.js)

    def test_avif_normalizes_only_identity_reference(self):
        self.assertIn('image/avif', self.elements.attributes['identityInput'][1]['accept'])
        for name in ('sourceInput', 'referenceInput', 'poseInput'):
            self.assertNotIn('image/avif', self.elements.attributes[name][1]['accept'])
        self.assertIn('async function prepareIdentityPhoto(file)', self.js)
        self.assertIn('canvas.toBlob(resolve, "image/png")', self.js)
        self.assertIn('24000000', self.js)
        self.assertIn('8192', self.js)
        self.assertIn('AVIF converted locally; original unchanged.', self.js)


if __name__ == '__main__':
    unittest.main()
