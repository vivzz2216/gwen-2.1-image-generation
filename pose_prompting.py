"""Soft human-pose guidance, not a rejection filter or anatomical detector."""
import os
import re

import prompt_intent

MARKER = "Keep each person's body and limb ownership distinct."
HUMAN = r"\b(?:people|persons?|men|women|man|woman|guys?|girls?|boys?|players?|athletes?|dancers?|fighters?|adults?|couple|crowd|someone)\b"
POSE = r"\b(?:poses?|posing|hands?|arms?|legs?|feet|foot|knees?|elbows?|crouch(?:es|ing)?|squat(?:s|ting)?|wrestl(?:e|es|ing)|shak(?:e|es|ing) hands|high.fiv(?:e|ing))\b"
# Respect requested fantasy anatomy and intentional distortion, rather than
# normalizing them into a generic human body. This is a conservative fallback.
UNUSUAL = r"\b(?:alien|monster|centaur|tentacles?|multi.armed|multi.legged|surreal|impossible|contortion(?:ist)?|distorted anatomy)\b|\b(?:extra|additional|multiple|three|four|six|eight|\d+)\s+(?:(?:human|robotic|mechanical|long|short|left|right)\s+)?(?:arms|legs|hands|heads)\b"
SEQUENCE = r"\b(?:sequence|storyboard|panels?|triptych|diptych|split.screen|multiple exposure|motion trails?|before and after)\b"


def enabled():
    return os.getenv("QWEN_POSE_GUIDANCE", "1").lower() not in {"0", "false", "off"}


def guidance(prompt):
    if not enabled():
        return ""
    text = prompt_intent.prose(prompt)
    if MARKER in text or re.search(UNUSUAL, text, re.I):
        return ""
    intent = prompt_intent.analyze(prompt)
    human = bool(re.search(HUMAN, text, re.I))
    # First-person editing instructions can identify a human without naming one.
    human = human or bool(re.search(r"\b(?:my|me|his|her|their)\b", text, re.I)
                          and (intent["changes_pose"] or re.search(POSE, text, re.I)))
    if not human:
        return ""
    multi = bool(re.search(r"\b(?:people|men|women|players|athletes|dancers|fighters|adults|couple|crowd)\b", text, re.I)
                 or len(re.findall(HUMAN, text, re.I)) > 1)
    if not (multi or intent["changes_pose"] or re.search(POSE, text, re.I)):
        return ""
    notes = [MARKER,
             "Render only the requested anatomy and number of subjects. Each visible hand or foot connects through its own limb to the correct person's body; overlapping bodies remain separate.",
             "Use coherent joint bends, perspective, balance and support for the requested pose. Respect explicitly requested missing limbs, prostheses, cropping and occlusion; hidden limbs stay hidden rather than appearing as extra visible limbs."]
    if not re.search(SEQUENCE, text, re.I):
        notes.append("Depict one instant of the requested action, not several successive limb positions superimposed on the same body.")
    if any(set(c["actions"]) & prompt_intent.CONTACT_ACTIONS - set(c["negated_actions"])
           for c in intent["action_clauses"]):
        notes.append("At each requested contact, keep the acting limb attached to its owner and the recipient's body separate. Preserve the specified contact target and side without inventing unspecified left/right sides.")
    return " ".join(notes)


def apply(prompt):
    notes = guidance(prompt)
    return prompt.rstrip() + ("\n" + notes if notes else "")
