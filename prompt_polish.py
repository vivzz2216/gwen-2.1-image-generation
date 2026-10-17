"""Bounded medium/style checks for faithful polishing, not content filtering."""
import re
import prompt_intent

STYLES = {
    "watercolor": r"water[ -]?colou?rs?",
    "photographic": r"photorealis(?:tic|m)|photo.realistic|photograph(?:s|ic|y)?|photo style",
    "anime": r"anime|manga",
    "cartoon": r"cartoons?|cartoonish",
    "digital illustration": r"digital (?:illustration|painting|art)|rendered (?:digital )?illustration",
    "3D render": r"3[ -]?d (?:render(?:ing)?|art|illustration)|cgi|computer.generated render",
    "oil painting": r"oil (?:painting|paint)|oil.on.canvas",
    "pencil sketch": r"pencil (?:sketch|drawing|art)",
    "pixel art": r"pixel[ -]?art|pixelated art",
    "vector": r"vector (?:art|illustration|graphic)|flat[ -]?vector",
    "comic": r"comic[ -]?(?:book|style)|comic illustration",
    "cinematic": r"cinematic",
    "surreal": r"surreal(?:ist|ism)?",
    "acrylic": r"acrylic (?:paint(?:ing)?|art)",
    "gouache": r"gouache",
    "charcoal": r"charcoal (?:drawing|sketch|art)",
    "pastel": r"pastel (?:drawing|painting|art)",
    "ink drawing": r"ink (?:drawing|illustration)|pen.and.ink",
    "line art": r"line[ -]?art",
}


def styles(text):
    text = prompt_intent.prose(text).lower()
    found = {}
    for name, pattern in STYLES.items():
        for match in re.finditer(r"\b(?:" + pattern + r")\b", text):
            clause = re.split(r"[.;!?\n]", text[:match.start()])[-1]
            excluded = bool(re.search(r"\b(?:no|not|without|avoid|never|don't|do not)\s+(?:\w+\s+){0,3}$", clause))
            found.setdefault(name, set()).add("excluded" if excluded else "requested")
    return found


def rewrite_errors(original, rewritten):
    source, result = styles(original), styles(rewritten)
    errors = []
    for name, kinds in result.items():
        if name not in source:
            errors.append(f"Do not introduce {name}; the user did not request that medium/style. Polish only the given prompt.")
        elif "requested" in kinds and "requested" not in source[name]:
            errors.append(f"Keep {name} excluded, as requested by the user.")
    for name, kinds in source.items():
        if "requested" in kinds and "requested" not in result.get(name, set()):
            errors.append(f"Retain the explicitly requested {name} style.")
        if "excluded" in kinds and "excluded" not in result.get(name, set()):
            errors.append(f"Retain the user's explicit exclusion of {name}; do not omit it.")
    # A grammatical copula must not become a newly chosen pose/movement.
    # This is bounded lexical checking, not a general new-fact detector.
    original_prose, rewritten_prose = prompt_intent.prose(original), prompt_intent.prose(rewritten)
    for action in ("stand", "sit", "kneel", "lean", "run", "jump"):
        pattern = r"stand(?:s|ing)?(?!\s+out\b)|stood" if action == "stand" else prompt_intent.ACTIONS[action]
        source_action, result_action = original_prose, rewritten_prose
        if action == "run":
            # Flowing tears are not a newly invented locomotion pose. Remove
            # only this bounded phrase, not a person's separate running action.
            tear_flow = r"\b(?:tears?|(?:wet\s+)?tear\s+(?:trails?|tracks?))\s+(?:(?:are|were)\s+)?(?:visibly\s+)?(?:run(?:s|ning)?|ran)\s+down\b"
            source_action = re.sub(tear_flow, "tear flow down", source_action, flags=re.I)
            result_action = re.sub(tear_flow, "tear flow down", result_action, flags=re.I)
        if prompt_intent.contains(result_action, pattern) and not prompt_intent.contains(source_action, pattern):
            errors.append(f"Do not invent the {action} pose/action. Express the existing facts with neutral grammar instead.")
    return errors


def description_errors(original, rewritten):
    """Bounded checks for user-requested grounded elaboration, not full semantics."""
    source, result = prompt_intent.prose(original).lower(), prompt_intent.prose(rewritten).lower()
    errors = []
    if re.search(r"\buncolou?red\b|\bcolou?rless\b|\bunspecified (?:colou?r|lighting|background|style)\b", result) and not re.search(r"\buncolou?red\b|\bcolou?rless\b|\b(?:no|without) colou?r\b|\bunspecified\b", source):
        errors.append("Omit unknown visual attributes entirely. Do not turn unspecified colors into an uncolored/colorless image, or describe internal uncertainty as scene content.")
    categories = {
        "color": r"red|blue|green|yellow|orange|purple|pink|black|white|gr[ae]y|brown|gold(?:en)?|silver|cyan|magenta|navy|teal|beige|cream|crimson|scarlet|violet|indigo|maroon",
        "lighting/weather choice": r"sunlight|sunlit|moonlight|moonlit|backlit|backlighting|sunset|sunrise|dawn|storm|snowfall|lightning|overcast|studio lighting|soft lighting|dramatic lighting|warm light|cool light",
    }
    for category, pattern in categories.items():
        for match in re.finditer(r"\b(?:" + pattern + r")\b", result):
            if not re.search(r"\b" + re.escape(match.group()) + r"\b", source):
                errors.append(f"Do not choose an unspecified {category}: {match.group()}. Elaborate shape/action/relationships instead; retain unspecified colors and light.")
    return errors


def neutralize_default_stance(original, rewritten):
    """Remove only unrequested standing/sitting choices, keeping explicit actions."""
    fragments = re.split(r'(["“][^"“”]*["”])', rewritten)
    for index in range(0, len(fragments), 2):
        fragments[index] = _neutralize_prose(original, fragments[index])
    return "".join(fragments)


def _neutralize_prose(original, rewritten):
    text = rewritten
    for action, forms in (("stand", ("standing", "stands", "stand")), ("sit", ("sitting", "sits", "sit"))):
        if prompt_intent.contains(prompt_intent.prose(original), prompt_intent.ACTIONS[action]):
            continue
        # Do not rewrite a prohibition into broken grammar; let validation
        # request a fresh description in that case.
        if prompt_intent.negated(text, prompt_intent.ACTIONS[action]):
            continue
        for form, neutral in zip(forms, ("positioned", "is", "are")):
            text = re.sub(r"\b" + form + r"\b(?!\s+out\b)", neutral, text, flags=re.I)
    # A descriptive scene must not echo the instruction to leave attributes
    # unspecified. Drop only that trailing meta-clause, not scene sentences.
    if not re.search(r"\bunspecified\b", original, re.I):
        text = re.sub(r",?\s+(?:leaving|keeping)\s+(?:all|the|any)?\s*(?:colou?rs?|lighting|weather)[^.!?]*\bunspecified\b", "", text, flags=re.I)
    return text
