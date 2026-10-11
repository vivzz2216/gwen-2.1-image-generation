# Local prompt enhancement for Qwen Image 2.1

The Studio now defaults to `qwen21-qwen4b`, an app-owned Ollama alias of the downloaded `qwen3.5:4b-q4_K_M` model. Dolphin-Llama3 8B remains installed as `qwen21-dolphin` for rollback. Both use the same Qwen-adapted task instructions. These aliases/configuration do not train or fine-tune weights. Selection is saved in `enhancer_model.json`; an explicit `QWEN_ENHANCER_MODEL` environment variable overrides it.

Qwen 4B was selected after three non-explicit text rewrite comparisons and measured 6 GB GPU fit. See `ENHANCER_AUDIT.md` for results, differences from the official guide, and limitations. It is a general Qwen3.5 model, not the specialized official Qwen-Image 9B prompt enhancer. Image inputs are not forwarded by this text-enhancement integration.

## Current behavior: faithful polish (`qwen21-polish-v2`)

The user's latest preference overrides the older expansion profiles documented below. Enhance actively improves grammar, spelling, punctuation, sentence structure and action/target clarity while preserving meaning, not copying rough wording. The v1 instructions were overly conservative and returned many rough prompts unchanged; v2 distinguishes useful rephrasing from invented content. It must leave unspecified details unspecified: no invented watercolor or other medium, camera angles, props, lighting, clothing materials, anatomy notes or padding. Explicit requested styles and existing action/body-part, exclusion and quoted-text checks are retained. Bounded checks reject recognized unsolicited styles and standing/sitting/kneeling/leaning/running/jumping poses, with at most one repair; they are not a complete semantic proof of every detail.

Active instructions: `prompts/qwen_polish_generate.txt` and `prompts/qwen_polish_edit.txt`. Both providers receive a single `rewritten_prompt` schema and `rewrite_mode=polish_only`. No detail plan, expansion call, pose notes or appended action anchors are used in the enhanced text. The API retains compatibility metadata (`profile=qwen21-actions-v2`), while `detail.profile=qwen21-polish-v2` identifies the new behavior. Legacy section parsing is retained for compatibility, not requested by the active app. Correcting `dont change my face` to grammatical preservation wording no longer falsely triggers a face-exclusion error. Already fluent, unambiguous prompts may legitimately remain unchanged.

Runtime settings override older alias defaults: temperature 0.15, top_p 0.8, repeat penalty 1.0, seed 42, context 4096, and a bounded output budget. Qwen uses `think=false`, top_k 20, min_p 0 and presence penalty 0. No new model or weight training is required. Existing GPU resource locking, tab residency, generation modes, Undo and direct-generation behavior are unchanged. The production endpoint does not save prompt text. Synthetic regression checks are in `test_prompt_polish.py`; `tools/verify_prompt_polish.py` saves only its deliberately supplied text examples for review.

## Historical behavior (superseded for Enhance)

The creative/detailed profiles below describe previous implementations and saved benchmarks, not current Enhance instructions. Direct generation's existing pose guidance is unaffected.

To recreate the selected alias, run `ollama create qwen21-qwen4b -f Modelfile.qwen21-qwen4b`. To roll back, set `model` to `qwen21-dolphin` in `enhancer_model.json` and restart Studio after active jobs finish. No model deletion is required.

### Previous creative prompting

The active application profile is restored to `qwen21-actions-v2`. The newer strict count/color/style rejection layer and strict expansion instructions have been removed. Dolphin can again elaborate compatible scene details using the previous temperature of 0.25. Existing English-only, structured-output, quoted-text and action/body-part checks remain; no content keyword blocklist is added by this integration.

Generate sends the entered prompt directly unless the user chooses Enhance; the existing anatomy guidance still applies to recognized actions. This adds no automatic LLM call to each generation. Fast, Balanced and Ultra retain their existing sampling settings. Explicit editing changes take priority over preservation defaults. These text checks do not inspect or certify generated pixels. Model weights are unchanged, so perfect adherence to every possible instruction is not guaranteed.

Regression checks in `test_dolphin_prompting.py` cover restored creative expansion and removal of strict request metadata. Prior diagnostic outputs remain saved; they are not evidence that every generated image follows every instruction.

## Sources and adaptation

### Previous detailed enhancement (v1)

Optional Enhance now receives a task-specific `detail_plan`. Generation asks for 230-360 words of relevant placement, interaction, surface, light and composition detail; explicitly minimal/isolated scenes use a shorter 40-100 word target. These are soft targets, not rejection thresholds. Editing asks for 90-180 words about requested operations, construction/attachment, scale, occlusion and source-matched illumination, while remaining text-only and avoiding invented source appearance. The old single watercolor example was removed to reduce example-style leakage.

The internal output schema separates generation into framing, subjects/action, appearance/materials, and lighting/composition. Editing separates requested changes from integration/preservation. These prose sections are joined in a fixed order into the same single editable prompt returned to the app. No analysis or headings are shown. Older aliases emitting `rewritten_prompt` are still accepted.

Editing also receives an explicit reference policy. If no extra reference was supplied or mentioned by the user, a rewrite must not invent one. Such an invalid rewrite uses the existing one-repair budget; this is input/reference consistency, not a content or word-count filter.

The memory-sensitive context remains 4096 and temperature remains 0.25. Output budgets are now 1400 tokens for generation and 800 for editing. A longer enhancement can take longer to compute and encode; it does not change sampling steps or guarantee better pixels. The response's `detail` metadata includes profile `qwen21-detailed-v1`, the target and the actual description word count before action anchors. No strict color/count/style gate or minimum-length rejection is added. Direct generation and Undo remain unchanged.

When a first valid draft is substantially shorter than its soft target, one detail-expansion pass may be used. Total calls are limited to two: a validation repair OR an expansion, never both. An unavailable/invalid expansion retains the validated first draft rather than failing the enhancement. `model_calls` counts all calls; `attempts` retains its earlier validation-attempt meaning. `detail` reports whether expansion was attempted/applied and any fallback warning.

`tools/verify_detail_enhancement.py` saves synthetic live text checks to a unique outputs folder; `test_enhancement_detail.py` tests the planning and preserves acceptance of short valid output. These checks do not generate or visually certify images.

- [Qwen's official generation rewriting prompt](https://github.com/QwenLM/Qwen-Image-2.1/blob/main/prompt_rewrite/prompts/system_prompt_t2i.txt): concrete scene descriptions, fixed user constraints, literal image text, and aspect ratio as a separate field.
- [Qwen's official editing rewriting prompt](https://github.com/QwenLM/Qwen-Image-2.1/blob/main/prompt_rewrite/prompts/system_prompt_edit.txt): strong requested changes, concise preservation, image-reference roles, and no invented edits.
- [Dolphin model information](https://ollama.com/library/dolphin-llama3): the installed model is text-only. It cannot inspect uploaded photographs. This adaptation does not claim the quality of Qwen's separately trained vision prompt-enhancer models.
- [Ollama structured outputs](https://docs.ollama.com/capabilities/structured-outputs) and [Chat API](https://docs.ollama.com/api/chat): system/user messages, JSON schema, and model unloading through keep_alive.

The adapted instructions are in `prompts/dolphin_qwen_generate.txt` and `prompts/dolphin_qwen_edit.txt`. They retain the user's requested tone and direct wording, with no keyword censorship added by this integration. They also tell the model to avoid introducing unrelated content. This does not guarantee any model's behavior on every request.

Output is English only, including quoted image lettering. Non-English input is translated; English quoted strings retain exact spelling and spacing. This overrides the official rewriter's rule about preserving non-English lettering. An output script check rejects non-English letters and requests one correction.

Generation instructs Dolphin to retain subject, counts, action, medium and quoted text; it does not force illustration or graphic design into photorealism. Editing clarifies the requested operation without inventing details from an unseen photo. Numbered references use Qwen's `<image1>`, `<image2>` convention. Prompt compilation treats requested pose/expression changes as exceptions to preservation, avoiding contradictory instructions.

## Configuration and use

### Windows desktop app

The desktop shortcut launches `QwenImageStudio.exe`, which runs this project's `desktop_app.py`; there is no second frozen copy of the Studio code to rebuild. The launcher verifies the running backend's enhancer profile before reusing it. An older backend is reported explicitly rather than silently presented as updated. Close/stop an old server only after its jobs finish, then reopen the shortcut.

The same Create/Edit interface now displays whether action/body-part checks are active. After Enhance, each prompt shows detected actions, body regions and explicit anatomical sides, with a reminder that these are text checks rather than a visual score. Undo clears the checked-status message. Newly typed wording is retained if the enhancement response arrives after an edit. Prompt assets are versioned to avoid reusing an older cached JavaScript file in WebView2.

### Action and anatomy constraints (v2)

`prompt_intent.py` supplies a lightweight, deterministic constraint brief to the existing local Dolphin profile. It recognizes common contact/movement verbs, neck/chest/abdomen and limb regions, abdominal synonyms, and explicit anatomical left/right. Literal lettering and several common object senses are excluded. This is a conservative lexical helper, not a general language parser or an anatomical detector.

Rewrites that omit a recognized action, substitute a different body region, lose a requested side or turn an excluded action into a positive instruction are rejected and retried once. Original interaction clauses are appended to the accepted rewrite so actor/recipient wording is not silently lost. The profile adds explicit contact-moment and anatomical-location guidance. No new weights, LoRA, database or online image provider is used.

Direct generation also receives the contact/anatomy guidance, without requiring Enhance. Auto-mode movement edits use global native Qwen conditioning rather than a clothes-only mask. Explicit regional movement requests without a reviewed mask return a clear error; a manually supplied movement mask remains the user's responsibility. Pose and hand-position preservation are removed when a recognized movement is requested. Global edits cannot promise exact unchanged outside pixels.

The enhancer response includes an `intent` field with the checks' scope and `image_understanding=false`. These checks do **not** prove actor ownership, correct anatomy or contact in rendered pixels. No automatic visual judge or official 9B PE model has been installed. Difficult interactions may still require a pose reference and visual review. The action regression suite is `test_prompt_intent.py`; run it along with the existing suites.

To recreate the local profile:

```powershell
ollama create qwen21-dolphin -f Modelfile.qwen21-dolphin
```

`QWEN_ENHANCER_MODEL` can select a different already installed Ollama model. `OLLAMA_URL` changes the endpoint; the default is localhost port 11434. Restart Studio after changing these variables.

Studio uses temperature 0.25, top_p 0.9, repeat penalty 1.05, seed 42, context 4096, and bounded output tokens (1400 for generation, 800 for editing). Internal descriptive sections are joined into one rewritten prompt. The app supplies the authoritative size fields independently, adapting the official rewriter contract to this text-only model. Further validation rejects missing fields, truncated output, altered quoted text, oversized descriptions, size metadata in visual prose, missing numbered references, omitted explicit left/right body parts, and a missing or contradicted requested smile. A failed validation triggers at most one correction request; `attempts` records the count. An explicit user no-text constraint is appended when Dolphin omits it. Model errors are shown explicitly and preserve the current prompt; there is no fake template fallback. These targeted checks cannot prove that every possible instruction was retained.

Enhancement runs under the same resource lock as Qwen. Idle ComfyUI model memory is released before Dolphin loads. External active ComfyUI jobs cause a busy response instead of being interrupted. Dolphin uses keep_alive=0 to release its memory afterwards. Models are reloaded when needed; this can add loading time.

The Enhance button works in Create and Edit. Review its output, especially an edit that Dolphin cannot see. Undo enhancement restores the previous wording and selected ratio. A prompt typed while enhancement is in progress is retained. Enhancement invalidates an accepted mask so its region can be reviewed against the rewritten request.

The endpoint accepts `prompt`, `task`, `aspect_ratio`, `region`, `preserve`, `reference_count`, and `is_transparent`. It returns the enhanced text and separate size metadata. Supported explicit generation ratios update the selection; image edits retain the source canvas dimensions. This feature does not add outpainting or arbitrary resizing.

The production endpoint does not write prompt text to files or retain a chat conversation history. The existing browser preference behavior is unchanged. The synthetic examples in `tools/verify_action_prompting.py` can be rerun against a test server; their outputs are deliberately saved to `outputs/action_tests/rewrite_results.json` for inspection. Keyword checks are limited diagnostics, not a complete semantic quality score. A busy response is reported as blocked, not a model failure.

Run regression checks with:

```powershell
.venv\Scripts\python.exe -m unittest test_prompt_intent test_dolphin_prompting test_studio_app test_qwen_engine test_editing_regions test_studio_jobs
.venv\Scripts\python.exe tools/verify_action_prompting.py --base http://127.0.0.1:7860
```
