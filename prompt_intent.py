"""Conservative lexical constraints; not vision or a semantic correctness score."""
import re

BODY_PARTS = {
    "neck": r"necks?", "chest": r"chests?", "torso": r"torsos?",
    "abdomen": r"abdomen|abdominal area|stomach|belly|midriff",
    "shoulder": r"shoulders?", "upper arm": r"upper arms?", "forearm": r"forearms?",
    "arm": r"arms?", "elbow": r"elbows?", "wrist": r"wrists?",
    "hand": r"hands?|palms?", "finger": r"fingers?", "back": r"back",
    "waist": r"waists?", "hip": r"hips?", "thigh": r"thighs?", "knee": r"knees?",
    "shin": r"shins?", "calf": r"calf|calves", "ankle": r"ankles?", "leg": r"legs?", "foot": r"foot|feet", "toe": r"toes?",
    "head": r"heads?", "face": r"faces?",
}
ACTIONS = {
    "push": r"push(?:es|ed|ing)?|shov(?:e|es|ed|ing)", "kick": r"kick(?:s|ed|ing)?",
    "punch": r"punch(?:es|ed|ing)?", "touch": r"touch(?:es|ed|ing)?|contact(?:s|ed|ing)?",
    "press": r"press(?:es|ed|ing)?", "grab": r"grab(?:s|bed|bing)?|grasp(?:s|ed|ing)?",
    "hold": r"hold(?:s|ing)?|held", "pat": r"pat(?:s|ted|ting)?",
    "rest": r"rest(?:s|ed|ing)?(?!\s+of\b)",
    "point": r"point(?:s|ed|ing)?", "raise": r"rais(?:e|es|ed|ing)|lift(?:s|ed|ing)?",
    "lower": r"lower(?:s|ed|ing)?", "bend": r"bend(?:s|ing)?|bent|flex(?:es|ed|ing)?",
    "turn": r"turn(?:s|ed|ing)?|rotat(?:e|es|ed|ing)",
    "hug": r"hug(?:s|ged|ging)?|embrac(?:e|es|ed|ing)",
    "lean": r"lean(?:s|ed|ing)?|leant", "kneel": r"kneel(?:s|ed|ing)?|knelt",
    "sit": r"sit(?:s|ting)?|sat|seated", "stand": r"stand(?:s|ing)?|stood",
    "run": r"run(?:s|ning)?|ran", "jump": r"jump(?:s|ed|ing)?|leap(?:s|ed|ing)?|leapt",
    "fall": r"fall(?:s|ing)?|fell|fallen",
}
CONTACT_ACTIONS = {"push", "kick", "punch", "touch", "press", "grab", "pat", "rest"}
MOVEMENT_ACTIONS = CONTACT_ACTIONS | {"raise", "lower", "bend", "turn", "hug", "lean", "kneel", "sit", "stand", "run", "jump"}
PERSON = r"\b(?:person|people|players?|man|men|woman|women|guy|girl|boy|fighters?|athletes?|adults?|opponents?|artists?|someone|him|her|me|my|their)\b"


def direction_pattern(side, part):
    # A descriptive modifier must not turn "right gloved hand" into a missing
    # direction. Never treat another side/body part as a modifier.
    modifiers = r"own|gloved|bare|open|extended|outstretched|raised|lowered|bent|prosthetic"
    return r"\b" + side + r"(?:[\s,-]+(?:(?:" + modifiers + r")[\s,-]+){0,2})(?:" + BODY_PARTS[part] + r")\b"


def prose(text):
    # Rendered lettering is not a request for a physical action.
    return re.sub(r'["“][^"“”]*["”]', "", text)


def contains(text, pattern):
    return bool(re.search(r"(?<![\w-])(?:" + pattern + r")(?![\w-])", text, re.I))


def anatomy(text):
    text = prose(text)
    # Avoid common object/idiomatic senses; this is deliberately not a full parser.
    text = re.sub(r"\b(?:treasure chest|chest of drawers|neck of (?:a |the )?bottle|back of (?:a |the )?(?:book|chair)|back to)\b", "", text, flags=re.I)
    return [name for name, pattern in BODY_PARTS.items() if contains(text, pattern)]


def negated(text, pattern):
    return bool(re.search(r"\b(?:not|never|no|without|avoid|stop|don['’]?t|do not)\s+(?:\w+\s+){0,2}(?:" + pattern + r")\b", text, re.I))


def body_part_excluded(text, pattern):
    # "Do not change my face" preserves a face; it does not exclude the face.
    # Normalize that narrow construction only for presence/exclusion checks.
    preserved = re.sub(r"\b(?:do\s+not|don['’]?t|never|avoid)\s+(?:change|alter|modify|edit|remove|replace)\b",
                       "preserve", text, flags=re.I)
    return negated(preserved, pattern)


def analyze(prompt):
    clauses = []
    for raw in re.split(r"[.!?;\n]+|\band (?=(?:raise|lower|bend|turn|push|kick|touch|grab|hug|lean|kneel|sit|stand|run|jump)\b)", prose(prompt), flags=re.I):
        raw = raw.strip()
        if not raw:
            continue
        parts = anatomy(raw)
        actions = [name for name, pattern in ACTIONS.items() if contains(raw, pattern)]
        # "turn my hair blue" is not a pose change; "lower clothes" is not a verb.
        if re.search(r"\bturn\s+(?:the\s+|my\s+)?(?:hair|shirt|clothes|top)\b", raw, re.I):
            actions = [a for a in actions if a != "turn"]
        if re.search(r"\blower\s+(?:clothes|body|torso|garment)\b", raw, re.I):
            actions = [a for a in actions if a != "lower"]
        if actions and (parts or re.search(PERSON, raw, re.I) or "fall" in actions):
            clauses.append({"instruction": raw, "actions": actions, "body_parts": parts,
                            "negated_actions": [a for a in actions if negated(raw, ACTIONS[a])]})
    directions = []
    for name, pattern in BODY_PARTS.items():
        for match in re.finditer(direction_pattern("(left|right)", name), prose(prompt), re.I):
            directions.append({"side": match.group(1).lower(), "part": name})
    movement = any(a in MOVEMENT_ACTIONS and a not in c["negated_actions"]
                   and not re.match(r"(?i)^(?:keep|preserve|retain|maintain|leave)\b", c["instruction"])
                   for c in clauses for a in c["actions"])
    return {"body_parts": anatomy(prompt), "directions": directions, "action_clauses": clauses,
            "changes_pose": movement, "validation_scope": "lexical constraints only; image not checked"}


def rewrite_errors(prompt, rewritten):
    intent, output, errors = analyze(prompt), prose(rewritten), []
    for part in intent["body_parts"]:
        if not contains(output, BODY_PARTS[part]):
            errors.append("Retain the requested body part: " + part + ".")
        elif contains(prose(prompt), BODY_PARTS[part]) and not body_part_excluded(prose(prompt), BODY_PARTS[part]):
            if body_part_excluded(output, BODY_PARTS[part]):
                errors.append("Do not exclude the requested body part: " + part + ".")
    for item in intent["directions"]:
        if not re.search(direction_pattern(item["side"], item["part"]), output, re.I):
            errors.append("Retain the exact side: " + item["side"] + " " + item["part"] + ".")
    for clause in intent["action_clauses"]:
        for action in clause["actions"]:
            if not contains(output, ACTIONS[action]):
                errors.append("Retain the requested action: " + action + ".")
            if action in clause["negated_actions"] and not negated(output, ACTIONS[action]):
                errors.append("Keep this action excluded: " + action + ".")
    if any(set(c["actions"]) & {"push", "kick", "punch"} and set(c["body_parts"]) & {"chest", "abdomen"}
           for c in intent["action_clauses"]) and not contains(prose(prompt), "behind"):
        if re.search(r"\b(?:approach(?:es|ed|ing)?|skates?(?:\s+up)?|stands?|standing|moves?|moving)\s+(?:up\s+)?behind\s+(?:the\s+|another\s+)?(?:player|man|person|opponent)\b", output, re.I):
            errors.append("Do not invent approaching from behind for the requested front-torso contact. Keep the approach unspecified and describe the actual chest/abdomen contact clearly.")
    return errors


def guidance(prompt, *, include_original=False):
    intent = analyze(prompt)
    if not intent["action_clauses"]:
        return ""
    notes = []
    if include_original:
        notes.extend("Required action (retain who acts on whom): " + c["instruction"] + "."
                     for c in intent["action_clauses"])
    if any(a in CONTACT_ACTIONS and a not in c["negated_actions"]
           for c in intent["action_clauses"] for a in c["actions"]):
        notes.append("Depict the requested moment of contact clearly at the specified target. Retain which subject acts, which limb or object acts, and who or what receives the action. Do not exchange the acting person and recipient or invent a different contact region.")
    definitions = {
        "neck": "The neck is between the head and shoulders.",
        "chest": "The chest is the front upper torso below the shoulders and above the abdomen.",
        "abdomen": "The abdomen (stomach/belly area) is below the chest and above the pelvis.",
    }
    notes.extend(definitions[p] for p in intent["body_parts"] if p in definitions)
    if intent["directions"]:
        if re.search(r"\b(?:viewer|camera|image)(?:'s|’s)?\s+(?:left|right)|\b(?:left|right)[^.;!?]{0,25}\b(?:viewer|camera)\b", prompt, re.I):
            notes.append("Use the viewpoint explicitly specified in the instruction for left/right directions.")
        else:
            notes.append("Left and right refer to the named person's anatomical side, not the viewer's side.")
    return " ".join(notes)


def anchor_rewrite(prompt, rewritten):
    """Retain relationship wording after checks, without claiming it was understood."""
    notes = guidance(prompt, include_original=True)
    return rewritten.rstrip() + (" " + notes if notes else "")
