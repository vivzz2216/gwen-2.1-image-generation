"""Guards the explicitly geometry-only UI refinement."""
from pathlib import Path
import re
import unittest

from fastapi.testclient import TestClient
from studio_service import app

ROOT = Path(__file__).resolve().parent


class LayoutTests(unittest.TestCase):
    def test_refinement_changes_no_theme_effect_or_font_family(self):
        css = (ROOT / "web/layout.css").read_text(encoding="utf-8")
        css = re.sub(r"/\*.*?\*/", "", css, flags=re.S)
        properties = set(re.findall(r"([\w-]+)\s*:", css))
        forbidden = {"color", "background", "background-color", "background-image", "border-color",
                     "box-shadow", "text-shadow", "filter", "animation", "transition", "font-family"}
        self.assertFalse(properties & forbidden, properties & forbidden)

    def test_shared_layout_is_served_to_browser_and_desktop(self):
        client = TestClient(app)
        html = client.get("/").text
        self.assertRegex(html, r'href="/web/layout\.css(?:\?[^"\s]+)?"')
        self.assertEqual(client.get("/web/layout.css").status_code, 200)

    def test_primary_controls_remain_present(self):
        html = (ROOT / "web/index.html").read_text(encoding="utf-8")
        for control in ("createTab", "editTab", "prompt", "clearAllPromptsBtn", "enhanceAllBtn", "promptCount",
                        "editRegion", "maskOverlay", "runButton", "aspectRatio", "preview"):
            with self.subTest(control=control):
                self.assertIn('id="' + control + '"', html)
        self.assertNotIn('id="historySection"', html)

    def test_clear_all_prompts_button_and_controls_present(self):
        html = (ROOT / "web/index.html").read_text(encoding="utf-8")
        self.assertIn('id="clearAllPromptsBtn"', html)
        self.assertIn('Clear all', html)

    def test_prompts_are_never_persisted_in_browser_storage(self):
        js = (ROOT / "web/studio.js").read_text(encoding="utf-8")
        save_pref_match = re.search(r"function savePreference\(\)\s*\{([^}]+)\}", js)
        self.assertIsNotNone(save_pref_match)
        save_pref_body = save_pref_match.group(1)
        self.assertNotIn("prompts: getPromptValues()", save_pref_body)
        self.assertNotIn('prompt: $("prompt")', save_pref_body)

        self.assertNotIn("state.prompts = saved.prompts", js)
        self.assertNotIn("state.prompts = [saved.prompt]", js)
        self.assertIn("clearAllPrompts()", js)
        self.assertIn("clearPromptIndex(", js)
        self.assertIn("card-clear-btn", js)

    def test_enhancer_selector_controls_present(self):
        html = (ROOT / "web/index.html").read_text(encoding="utf-8")
        self.assertIn('id="enhancerSelector"', html)
        self.assertIn('id="enhancerDolphinBtn"', html)
        self.assertIn('id="enhancerQwenBtn"', html)
        self.assertIn('card-alt-enhance-btn', html)
        js = (ROOT / "web/studio.js").read_text(encoding="utf-8")
        self.assertIn("setEnhancer(", js)
        self.assertIn("card-alt-enhance-btn", js)

    def test_workflow_tabs_and_drop_isolation(self):
        html = (ROOT / "web/index.html").read_text(encoding="utf-8")
        for tab in ("createTab", "editTab", "restyleTab"):
            with self.subTest(tab=tab):
                self.assertIn(f'id="{tab}"', html)
        task_tabs = re.findall(r'id="(?:createTab|editTab|restyleTab)"', html)
        self.assertEqual(len(task_tabs), 3)

        js = (ROOT / "web/studio.js").read_text(encoding="utf-8")
        # Ensure per-box drop handler stops event propagation
        self.assertIn('event.stopPropagation();', js)
        # Ensure window drop handler handles restyle workflow by selecting identity without switching to edit
        self.assertRegex(
            js,
            r'if\s*\(\s*state\.workflow\s*===\s*["\']restyle["\']\s*\)\s*void\s+selectIdentity\('
        )
        self.assertIn('id="faceDetailEnabled"', html)
        self.assertIn('id="sharpenFaceBtn"', html)
        self.assertIn('"face_detail"', js)


if __name__ == "__main__":
    unittest.main()
