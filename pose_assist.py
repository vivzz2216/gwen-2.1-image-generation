"""Native visual pose roles and measurable repair checks, not anatomical guarantees."""
import re
from pathlib import Path

import numpy as np
from PIL import Image
import prompt_intent

ROOT = Path(__file__).resolve().parent
REPAIR_KINDS = {"none", "face", "hand", "leg", "contact"}


def interpretation(prompt):
    base = prompt_intent.analyze(prompt)
    clauses = re.split(r"[.;!?\n]+", prompt_intent.prose(prompt))
    exclusions = [clause.strip() for clause in clauses if re.search(r"\b(?:no|without|avoid|do not|don['’]?t)\b", clause, re.I)]
    interactions = []
    body_pattern = "|".join(prompt_intent.BODY_PARTS.values())
    for clause in base["action_clauses"]:
        raw = clause["instruction"]
        for action in clause["actions"]:
            verb = re.search(r"\b(?:" + prompt_intent.ACTIONS[action] + r")\b", raw, re.I)
            if not verb:
                continue
            before, after = raw[:verb.start()].strip(), raw[verb.end():]
            owner = re.search(r"((?:the|another|other|a|an)\s+(?:\w+\s+){0,3}\w+)['’]s\s+(?:(?:left|right)\s+)?(?:" + body_pattern + r")\b", after, re.I)
            if owner:
                recipient = owner.group(1)
            else:
                of = re.search(r"\b(?:" + body_pattern + r")\s+of\s+(.+?)(?:\s+(?:while|during|with|on|at)\b|$)", after, re.I)
                recipient = of.group(1).strip() if of else None
            passive = bool(re.search(r"\b(?:is|was|being|been)\s*$", before, re.I))
            ambiguous_past = verb.group().lower().endswith("ed") and recipient is None
            interactions.append({"action": action, "actor": None if passive or ambiguous_past else before or None,
                                 "recipient": before if passive else recipient,
                                 "target_body_regions": clause["body_parts"],
                                 "scope": "Candidate roles from explicit wording; null means unresolved. No inferred limb or side."})
    return {**base, "interaction_graph": interactions, "explicit_exclusions": exclusions,
            "focus_regions": [part for part in base["body_parts"] if re.search(r"\b(?:focus|detail|repair|fix)\b", prompt, re.I)],
            "relationship_rule": "Keep actor/recipient roles from the wording. Missing sides and visible-limb counts remain unspecified.",
            "scope": "Conservative text extraction; no image analysis or full relationship parser."}


def compile_pose(prompt, *, task, pose_slot=2, appearance_slots=(), order="same"):
    text = ("Create a new image from the instruction. <image1> is a blank canvas defining output dimensions, not a style or identity reference. "
            if task == "generate" else "Edit <image1>, retaining the source subjects' identity and unrequested appearance except for explicitly requested changes. ")
    text += (f"<image{pose_slot}> supplies only the target body pose, relative subject placement and occlusion. "
             "Follow its pose while keeping the instruction's actor, recipient, action and contact body region. "
             "Do not copy the pose reference's identity, clothes, background or rendering style. "
             "Do not reproduce skeleton lines, colored landmarks or annotations in the finished image. ")
    if task == "edit":
        text += "Change body pose and hand positions as needed; do not freeze the original pose. Do not change unrequested facial identity or clothing. "
    if order == "swap":
        text += "For the two-person pose reference, exchange the first and second subject assignments as explicitly selected by the user. "
    if appearance_slots:
        text += "Use " + ", ".join(f"<image{slot}>" for slot in appearance_slots) + " only for the requested appearance changes, not pose. "
    return text + "User instruction: " + prompt.strip()


def repair_guidance(kind):
    return {"face": "Review a mask covering the face and necessary head/neck context. Use an approved identity reference; preserve the requested expression and head angle. Pixels outside the mask stay protected, not the regenerated face.",
            "hand": "Review a mask covering the faulty hand, wrist and enough adjacent forearm/context to reconnect it. Do not enforce a visible finger count on hidden or overlapping fingers.",
            "leg": "Review the faulty leg/foot and enough adjoining knee/hip context. Include old and new limb silhouettes and exposed background if it moves.",
            "contact": "Review both interacting regions and their contact point. Include connected limbs and exposed background; a chest contact cannot be fixed with a finger-only mask.",
            "none": ""}[kind]


def preservation_check(original, result, mask):
    if original.size != result.size:
        return {"verified": False, "reason": "Result dimensions differ from source."}
    a, b = np.asarray(original.convert("RGBA")), np.asarray(result.convert("RGBA"))
    outside = np.asarray(mask.convert("L")) == 0
    changed = int(np.count_nonzero(np.any(a != b, axis=2) & outside))
    return {"verified": True, "outside_pixels": int(np.count_nonzero(outside)),
            "changed_outside_pixels": changed, "exact_outside_preservation": changed == 0,
            "scope": "Pixel comparison only; identity, anatomy and requested edit require visual review."}


def capabilities():
    models = ROOT / "ComfyUI_windows_portable" / "ComfyUI" / "models"
    checkpoint = any(models.glob("**/Qwen-Image-2.1-Fun-Controlnet-Union*.safetensors"))
    return {"native_reference": True, "requires_review": True,
            "skeleton_upload": True, "dwpose_cpu": {"available": False, "reason": "CPU DWPose extractor is not installed. Upload a reviewed photograph or skeleton PNG."},
            "controlnet": {"enabled": False, "checkpoint_present": checkpoint,
                           "status": "experimental_not_validated", "published_checkpoint_gb": 7.55,
                           "reason": "Extra control weights, compatible loader and 6 GB memory/speed validation are required. No fallback that silently ignores control."}}
