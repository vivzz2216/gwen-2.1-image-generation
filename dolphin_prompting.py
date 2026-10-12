"""Task-specific Dolphin prompting for Qwen; no weight training or prompt logging."""
import json
import os
from pathlib import Path
import re
import time
import urllib.error
import urllib.request
import prompt_intent
import enhancement_detail
import prompt_polish
import pose_assist as pose_controls

ROOT = Path(__file__).resolve().parent
OLLAMA_URL = os.getenv("OLLAMA_URL", "http://127.0.0.1:11434").rstrip("/")
DOLPHIN_MODEL = "qwen21-dolphin"
QWEN_MODEL = "qwen21-qwen4b"
SUPPORTED_MODELS = {
    "qwen21-dolphin",
    "qwen21-qwen4b",
    "qwen3.5:4b-q4_K_M",
    "qwen27b",
}


def resolve_model(alias=None):
    if not alias:
        return MODEL
    clean = str(alias).strip().lower()
    if clean in ("dolphin", "qwen21-dolphin", "dolphin-llama3"):
        return DOLPHIN_MODEL
    if clean in ("qwen", "qwen4b", "qwen21-qwen4b"):
        return QWEN_MODEL
    if clean in ("qwen27b", "qwen-27b", "qwen3.8-27b"):
        return "qwen27b"
    if clean == "qwen3.5:4b-q4_k_m":
        return "qwen3.5:4b-q4_K_M"
    return str(alias)


def configured_model(path=None):
    override = os.getenv("QWEN_ENHANCER_MODEL")
    if override:
        return override
    try:
        settings = json.loads((path or ROOT / "enhancer_model.json").read_text(encoding="utf-8"))
        selected = settings.get("model")
        if selected in {"qwen21-dolphin", "qwen21-qwen4b", "qwen27b"}:
            return selected
    except (OSError, ValueError, AttributeError):
        pass
    return "qwen21-dolphin"


MODEL = configured_model()
RATIOS = ("1:1", "16:9", "9:16", "4:3", "3:4", "3:2", "2:3")
PROFILE_VERSION = "qwen21-actions-v2"
DETAIL_PROFILE = "qwen21-grounded-description-v3"


class EnhancementError(RuntimeError):
    def __init__(self, message, status=502):
        super().__init__(message)
        self.status = status


def english_script(text):
    return all(not character.isalpha() or character.isascii() for character in text)


def output_schema(task, ratio=None):
    properties = {
        "rewritten_prompt": {"type": "string"},
        "wh_ratio": {"type": "string", "enum": ([ratio] if ratio else list(RATIOS)) if task == "generate" else [""]},
    }
    if task == "edit":
        properties["ratio_follow"] = {"type": "string", "enum": ["<image1>"]}
    return {"type": "object", "properties": properties, "required": list(properties),
            "additionalProperties": False}


def selected_ratio(prompt, requested):
    prose = re.sub(r'["“][^"“”]+["”]', "", prompt)
    explicit = re.search(r"(?<!\d)(\d{1,2}:\d{1,2})(?!\d)", prose)
    ratio = explicit.group(1) if explicit else requested
    if ratio not in RATIOS:
        raise EnhancementError("Choose a supported aspect ratio: " + ", ".join(RATIOS), 422)
    return ratio


def requested_operations(prompt):
    chunks = re.split(r"[,.;\n]|\bbut\b|\band (?=(?:make|change|replace|add|remove|raise|lower|turn|recolor|keep|preserve)\b)", prompt, flags=re.I)
    return [chunk.strip() for chunk in chunks if chunk.strip()
            and not re.match(r"(?i)^(?:keep|preserve|retain|maintain|leave|do not|don't)\b", chunk.strip())]


def validate_output(content, prompt, task, ratio, reference_count=0):
    try:
        result = json.loads(content)
    except (TypeError, ValueError) as exc:
        raise EnhancementError("Dolphin returned invalid structured output. Your original prompt is unchanged.") from exc
    required = set(output_schema(task)["required"])
    if not isinstance(result, dict) or set(result) != required or not all(isinstance(v, str) for v in result.values()):
        raise EnhancementError("Dolphin returned an incomplete prompt. Your original prompt is unchanged.")
    rewritten = result["rewritten_prompt"].strip()
    if not rewritten or len(rewritten) > 4000:
        raise EnhancementError("Dolphin returned an empty or oversized prompt. Your original prompt is unchanged.")
    if not english_script(rewritten):
        raise EnhancementError("Write the entire enhanced prompt in English, including readable image text. Translate non-English wording into English.")
    if re.match(r"(?i)^(?:sure\b|here (?:is|are)\b|as an ai\b|enhanced prompt\s*:)", rewritten):
        raise EnhancementError("Dolphin returned commentary instead of an image prompt. Try again.")
    # English literal text is exact; non-English lettering is translated under
    # the user's English-only preference.
    for literal in re.findall(r'["“]([^"“”]+)["”]', prompt):
        if english_script(literal) and literal not in rewritten:
            raise EnhancementError("Dolphin changed quoted text. Your original prompt is unchanged.")
    prose = re.sub(r'"[^"\n]*"', "", rewritten)
    if re.search(r"\baspect ratio\b|\b(?:" + "|".join(re.escape(r) for r in RATIOS) + r")\b|\b\d{2,5}\s*[x×]\s*\d{2,5}\b", prose, flags=re.I):
        raise EnhancementError("Keep aspect ratios and pixel dimensions in the size fields, not in the visual prompt.")
    instruction_prose = re.sub(r'["“][^"“”]+["”]', "", prompt)
    intent_errors = prompt_intent.rewrite_errors(prompt, rewritten) + prompt_polish.rewrite_errors(prompt, rewritten)
    if intent_errors:
        raise EnhancementError(" ".join(intent_errors))
    if re.search(r"\b(?:no|without)\s+(?:readable\s+)?text\b", instruction_prose, flags=re.I):
        if not re.search(r"\b(?:no|without)\s+(?:any\s+|readable\s+)?text\b|\b(?:devoid|free) of (?:any )?text\b|\btext.free\b", rewritten, flags=re.I):
            # This is an explicit user constraint, so retain it deterministically
            # rather than trusting the model to repeat it during a second pass.
            rewritten = rewritten.rstrip() + " The image contains no readable text."
    if task == "edit":
        reference_terms = r"\breference (?:image|photo(?:graph)?|picture)\b|<image[2-9]\d*>"
        if not reference_count and not re.search(reference_terms, prompt, re.I) and re.search(reference_terms, rewritten, re.I):
            raise EnhancementError("No additional reference image was supplied. Describe the requested new appearance from the instruction, not an invented reference.")
        tags = set(re.findall(r"<image\d+>", prompt))
        if reference_count:
            tags.update(f"<image{i}>" for i in range(1, reference_count + 2))
        if any(tag not in rewritten for tag in tags):
            raise EnhancementError("Retain every numbered image reference, using the exact <image1>, <image2> tags.")
        edits = " ".join(requested_operations(prompt)).lower()
        smile_requested = re.search(r"\b(?:make|give)\b[^.;]*\bsmil(?:e|ing)\b|\b(?:to|into|add|a)\s+(?:clear\s+)?smile\b", edits)
        smile_removed = re.search(r"\b(?:stop|remove|without|no)\b[^.;]*\bsmil(?:e|ing)\b", edits)
        if smile_requested and not smile_removed:
            if "smil" not in rewritten.lower():
                raise EnhancementError("The requested smile is missing. Include every requested operation.")
            if re.search(r"\b(?:maintain|keep|preserve)\b.{0,45}\b(?:current|original|existing)\s+(?:facial\s+)?expression\b", rewritten, flags=re.I):
                raise EnhancementError("Keeping the original expression contradicts the requested smile.")
        for direction, part in re.findall(r"\b(left|right)\s+(arm|hand|leg|foot)\b", edits):
            if not re.search(prompt_intent.direction_pattern(direction, part), rewritten, flags=re.I):
                raise EnhancementError("Retain the requested body part and its left/right direction.")
    if task == "generate" and result["wh_ratio"] != ratio:
        raise EnhancementError("Dolphin changed the requested aspect ratio. Try again.")
    if task == "edit" and (result["wh_ratio"] != "" or result["ratio_follow"] != "<image1>"):
        raise EnhancementError("Dolphin changed the source canvas settings. Try again.")
    # Collapse prose whitespace without changing spacing inside rendered text.
    fragments = re.split(r'("[^"\n]*")', rewritten)
    result["rewritten_prompt"] = "".join(part if i % 2 else re.sub(r"\s+", " ", part)
                                        for i, part in enumerate(fragments))
    if len(result["rewritten_prompt"]) > 4000:
        raise EnhancementError("The enhanced prompt is too long. Shorten the brief and try again.")
    for literal in re.findall(r'["“]([^"“”]+)["”]', prompt):
        if english_script(literal) and literal not in result["rewritten_prompt"]:
            raise EnhancementError("Quoted text requires exact spacing; use your original prompt.")
    return result


def chat(payload):
    request = urllib.request.Request(OLLAMA_URL + "/api/chat", method="POST",
        data=json.dumps(payload).encode("utf-8"), headers={"Content-Type": "application/json"})
    try:
        with urllib.request.urlopen(request, timeout=240) as response:
            data = json.loads(response.read().decode("utf-8"))
    except (urllib.error.URLError, OSError, ValueError) as exc:
        raise EnhancementError("The local prompt enhancer is unavailable or timed out. Start Ollama and install the configured enhancer profile. Your prompt is unchanged.", 503) from exc
    if not isinstance(data, dict) or data.get("error"):
        raise EnhancementError("The local prompt enhancer could not complete this request. Your prompt is unchanged.", 503)
    if not data.get("done") or data.get("done_reason") == "length":
        raise EnhancementError("Dolphin's response was cut short. Shorten the brief and try again.")
    return data.get("message", {}).get("content")


def parsed_reply(content, prompt, task, ratio, reference_count, descriptive=False):
    try:
        raw = json.loads(content)
    except (TypeError, ValueError) as exc:
        raise EnhancementError("Return a valid JSON object containing rewritten_prompt.") from exc
    if not isinstance(raw, dict):
        raise EnhancementError("Return the required rewritten_prompt field.")
    if "rewritten_prompt" in raw:
        prose = raw["rewritten_prompt"]  # Compatibility with existing aliases.
    else:
        sections = enhancement_detail.SECTIONS[task]
        if set(raw) != set(sections) or not all(isinstance(raw[name], str) and raw[name].strip() for name in sections):
            raise EnhancementError("Return every descriptive section required by the supplied schema.")
        prose = " ".join(raw[name].strip() for name in sections)
    if descriptive and isinstance(prose, str):
        prose = prompt_polish.neutralize_default_stance(prompt, prose)
    result = {"rewritten_prompt": prose, "wh_ratio": ratio}
    if task == "edit":
        result["ratio_follow"] = "<image1>"
    return validate_output(json.dumps(result), prompt, task, ratio, reference_count)


def enhance(prompt, *, task="generate", aspect_ratio="1:1", region="auto",
            preserve=True, reference_count=0, is_transparent=False, keep_alive=0, model=None,
            enhancement_style="describe", pose_assist=False, identity_assist=False):
    prompt = prompt.strip()
    if not prompt or len(prompt) > 4000 or task not in ("generate", "edit"):
        raise EnhancementError("Enter a prompt up to 4000 characters and a valid task.", 422)
    ratio = selected_ratio(prompt, aspect_ratio) if task == "generate" else ""
    selected_model = resolve_model(model) if model else MODEL
    if enhancement_style not in {"describe", "polish"}:
        raise EnhancementError("Choose describe or polish enhancement.", 422)
    descriptive = enhancement_style == "describe"
    system = (ROOT / "prompts" / f"qwen_{'describe' if descriptive else 'polish'}_{task}.txt").read_text(encoding="utf-8")
    # Frame metadata belongs to the app, not the text model. Keep literal image
    # text intact while removing numerical frame hints from descriptive input.
    fragments = re.split(r'(["“][^"“”]+["”])', prompt)
    visual_instruction = "".join(part if i % 2 else re.sub(
        r"(?<!\d)(?:" + "|".join(re.escape(r) for r in RATIOS) + r")(?!\d)", "", part)
        for i, part in enumerate(fragments))
    brief = {"instruction": visual_instruction, "task": task, "is_transparent": is_transparent,
             "rewrite_mode": "grounded_description" if descriptive else "polish_only",
             "priority": "Expand existing subjects/actions into useful visual prose without selecting an unrequested style or adding unrelated scene content." if descriptive else "Actively clarify language without adding visual details."}
    if descriptive:
        brief["detail_budget"] = {"soft_words": [25, 70] if task == "edit" or len(prompt.split()) < 15 else [60, 140],
                                  "rule": "Several useful sentences; develop existing concepts, not new scene facts. No padding or fixed minimum."}
        brief["unspecified_constraints"] = {
            "style": "Keep unmentioned styles unspecified.", "colors_and_lighting": "Do not choose any unmentioned color, lighting setup or weather.",
            "pose": "Keep an unmentioned pose or movement unspecified. Describe recognizable subject form and the stated spatial relationship instead."}
    intent = pose_controls.interpretation(visual_instruction)
    if intent["action_clauses"] or intent["body_parts"]:
        brief.update(required_intent=intent,
                     relationship_rule="Keep actor, recipient, acting limb, target body region, side and exclusions exactly as requested. Do not invent missing sides or claim to see the source image.")
    if task == "edit":
        brief.update(region=region, preserve_unrequested_details=preserve,
                     reference_count=reference_count, requested_operations=requested_operations(prompt))
        brief["reference_policy"] = (
            "The source canvas is the only image. No additional reference image was supplied. Specify the new requested appearance from the instruction; do not refer to an extra reference or invent its contents."
            if not reference_count else "Use the supplied numbered image tags for their requested roles; never invent unseen reference appearance.")
    if pose_assist:
        if task == "edit" and reference_count:
            brief["reference_policy"] = "Image 1 is the original source. Image 2 is pose guidance only." + (" Image 3 is the separate appearance reference." if reference_count > 1 else "") + " Never invent unseen image contents."
        brief["pose_policy"] = "Pose guidance is supplied separately by the app. Do not invent a pose, lock the old pose or guess unseen reference contents. Retain requested interaction/contact details."
    if identity_assist:
        brief["reference_policy"] = ("Image 1 supplies the identity photograph for creation or original scene for editing. Image 2 is the selected identity head crop." + (" Image 3 supplies body pose only." if pose_assist else "") + " Do not describe unseen facial features or add changes; retain the user's action. Do not freeze old pose, expression or head angle when a change is requested.")
    payload = {
        "model": selected_model, "messages": [{"role": "system", "content": system},
            {"role": "user", "content": json.dumps(brief, ensure_ascii=False)}],
        "stream": False, "format": {"type": "object", "properties": {"rewritten_prompt": {"type": "string"}},
                                    "required": ["rewritten_prompt"], "additionalProperties": False}, "keep_alive": keep_alive,
        "options": {"temperature": .2 if descriptive else .15, "top_p": .8, "repeat_penalty": 1.0,
                    "seed": 42, "num_ctx": 4096, "num_predict": min(1400, max(1000 if descriptive else 512, len(prompt) // 3 + 256))},
    }
    if selected_model.startswith("qwen3.5:") or selected_model == "qwen21-qwen4b" or selected_model == "qwen27b":
        # Native Ollama thinking control; no hidden reasoning consumes the
        # small context/output budget. Low-variance settings suit text polishing.
        payload["think"] = False
        payload["options"].update(top_k=20, min_p=0.0, presence_penalty=0.0)
    # A compact worked example teaches elaboration without selecting an
    # activity, palette or lighting for a location-only request. The actual
    # instruction is always the final user turn, never a template fallback.
    example_messages = []
    if descriptive and task == "generate":
        example_messages = [
            {"role": "user", "content": json.dumps({"instruction": "two foxes beneath an umbrella", "task": "generate"})},
            {"role": "assistant", "content": json.dumps({"rewritten_prompt": "Two foxes are beneath an umbrella, with the umbrella above their bodies. Their recognizable fox forms include pointed ears and bushy tails. The scene emphasizes the relationship between the two animals and the umbrella overhead."})},
        ]
        payload["messages"] = [payload["messages"][0], *example_messages, payload["messages"][1]]
    started = time.monotonic()
    for attempt in range(2):
        content = chat(payload)
        try:
            # The output size is locked by the Studio rather than predicted by
            # Dolphin. Ignore legacy size fields if an older model emits them.
            result = parsed_reply(content, prompt, task, ratio, reference_count, descriptive=descriptive)
            if descriptive:
                errors = prompt_polish.description_errors(prompt, result["rewritten_prompt"])
                if errors:
                    raise EnhancementError(" ".join(errors))
            descriptive_word_count = len(result["rewritten_prompt"].split())
            break
        except EnhancementError as exc:
            if attempt:
                raise
            repair = {**brief, "validation_feedback": str(exc),
                      "correction": "Rewrite the original brief again. Retain EVERY requested operation, exclusion and literal text; put size metadata only in its separate fields. Return valid JSON only."}
            # Keep the original brief in a fresh turn; avoid stuffing a failed
            # long response into the 4096-token context on the 6 GB setup.
            payload["messages"] = [{"role": "system", "content": system}, *example_messages,
                {"role": "user", "content": json.dumps(repair, ensure_ascii=False)}]
    expansion_attempted = expansion_applied = False
    expansion_warning = ""
    # Never expand a valid reply. Only one validation repair is allowed.
    return {"original": prompt, "enhanced": result["rewritten_prompt"],
            "wh_ratio": result["wh_ratio"], "ratio_follow": result.get("ratio_follow", ""),
            "model": selected_model, "profile": PROFILE_VERSION, "task": task,
            "elapsed_sec": round(time.monotonic() - started, 2),
            "attempts": attempt + 1,
            "model_calls": attempt + 1 + int(expansion_attempted),
            "image_understanding": False, "intent": intent,
            "detail": {"profile": DETAIL_PROFILE if descriptive else "qwen21-polish-v2", "level": "grounded_description" if descriptive else "polish_only",
                       "target_words": brief.get("detail_budget", {}).get("soft_words", []), "descriptive_word_count": descriptive_word_count,
                       "expansion_attempted": expansion_attempted, "expansion_applied": expansion_applied,
                       "warning": expansion_warning,
                       "scope": "text description only; image not verified" if descriptive else "text polish only; image not verified"}}


def unload(model_name=None):
    """Release only Studio's configured enhancer, never a different model tag."""
    target = resolve_model(model_name) if model_name else MODEL
    try:
        with urllib.request.urlopen(OLLAMA_URL + "/api/ps", timeout=3) as response:
            loaded = json.loads(response.read().decode("utf-8")).get("models", [])
    except (OSError, ValueError):
        return  # Offline Ollama cannot hold an active model through this API.
    def owned(models):
        return any(str(m.get("name") or m.get("model", "")).removesuffix(":latest") == target.removesuffix(":latest") for m in models)
    if not owned(loaded):
        return
    request = urllib.request.Request(OLLAMA_URL + "/api/generate", method="POST",
        data=json.dumps({"model": target, "keep_alive": 0, "stream": False}).encode(),
        headers={"Content-Type": "application/json"})
    try:
        with urllib.request.urlopen(request, timeout=15) as response:
            response.read()
        deadline = time.monotonic() + 10
        while True:
            with urllib.request.urlopen(OLLAMA_URL + "/api/ps", timeout=3) as response:
                models = json.loads(response.read().decode()).get("models", [])
            if not owned(models):
                return
            if time.monotonic() >= deadline:
                raise EnhancementError(f"{target} is still releasing GPU memory. Try the image again shortly.", 503)
            time.sleep(.25)
    except (OSError, ValueError) as exc:
        raise EnhancementError(f"Cannot release {target} GPU memory before image generation.", 503) from exc

