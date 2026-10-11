"""Detail planning for optional text enhancement; no lexical rejection layer."""
import re

import prompt_intent

SECTIONS = {
    "generate": {
        "scene_and_framing": "Medium, setting, framing, spatial depth and relative placement. About 40-70 words.",
        "subjects_and_action": "Distinct subjects, requested actor/recipient, pose and contact target. About 60-100 words; no narrative aftermath.",
        "appearance_and_materials": "Relevant clothing, objects, material surfaces and concrete visual detail. About 50-90 words, without repeating the action.",
        "lighting_and_composition": "Light direction/quality, shadows, reflections and overall balance. About 40-70 words, without generic praise.",
    },
    "edit": {
        "requested_changes": "Exact requested operations and the NEW appearance/construction, including appropriate attachment/placement. About 60-110 words. A replacement jacket uses jacket construction, not the old shirt's silhouette or seams.",
        "integration_and_preservation": "Source-matched perspective, scale, light and occlusion, then one preservation clause. About 30-70 words. Do not invent existing source appearance.",
    },
}


def section_schema(task):
    return {"type": "object", "properties": {name: {"type": "string", "description": description}
            for name, description in SECTIONS[task].items()}, "required": list(SECTIONS[task]), "additionalProperties": False}


def plan(prompt, task):
    minimal = bool(re.search(r"\b(?:minimal(?:ist)?|simple|plain|icon|logo|isolated|negative space)\b", prompt_intent.prose(prompt), re.I))
    if task == "edit":
        return {
            "level": "detailed_edit", "target_words": [90, 180],
            "coverage": ["Every requested operation and its exact target",
                         "Requested color, garment construction, material or style, where applicable",
                         "Placement, scale, attachment and occlusion of requested additions",
                         "Requested pose/contact and limb ownership, where applicable",
                         "Match source lighting and perspective unless explicitly changed",
                         "One concise preservation clause for unrequested content"],
            "priority": "Expand only the requested changes. You have not seen the image; never invent existing appearance or image contents. Target length is guidance, not a minimum.",
        }
    return {
        "level": "detailed_scene", "target_words": [40, 100] if minimal else [230, 360],
        "coverage": ["Rendered medium and main subjects",
                     "Relative subject placement, framing and foreground/background relationships",
                     "Exact action, actor/recipient and visible contact/limb ownership, where applicable",
                     "Relevant surface materials, clothing and object construction",
                     "Setting and spatial depth without extra subjects or distracting props",
                     "Light source/direction, quality, shadows and reflections where appropriate to the medium",
                     "Requested lettering and exclusions, then overall visual balance"],
        "priority": "Add useful observable detail, not filler. User-specified attributes and intentional unusual ideas remain authoritative. Keep explicitly simple or sparse scenes simple. Length is guidance, never grounds for rejection.",
    }
