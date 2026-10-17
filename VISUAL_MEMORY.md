# Qwen Visual Memory

A working local story workspace at **http://127.0.0.1:7860/story**, also linked as **Visual Memory** in the existing Image Studio and desktop app. It stores character identities, original image references, locations, scene settings and parent links, and constructs each next instruction using the selected scene. Image guidance improves continuity but does not guarantee identical faces, garments or correct actions.

## Run

```powershell
cd "C:\Users\pilla\OneDrive\Desktop\gwen2.1image_generation"
.\start_studio.bat
```

For a browser window, use `.\start_studio.bat --browser`, then open `/story` on port 7860. The launcher uses the existing `.venv`, starts or reuses ComfyUI on port 8188 and the existing prompt services. No new dependencies or models are required by Visual Memory. The story engine does not call an online image service or download/retrain weights.

If Studio is already running from before code edits, close its running server normally and launch it again to load the latest source. The five-scene queue update was loaded by restarting the idle Studio server on port 7860; ComfyUI was not restarted. Earlier blocked-restart notes refer to the previous build, not the current running queue implementation.

## Use

1. Create a named story and describe its opening scene. Choose Fast/Balanced/Ultra and Generate next scene. Successful images and metadata become persistent scene records.
2. For common woman/girl and man/guy descriptions, the opening creates draft character IDs and clothing descriptions. These are text-derived drafts, not automatic face identification. Other characters can be added manually.
3. Open Character manager and select a character. Choose **Select a face from the displayed scene**, inspect the numbered head previews, select the correct face and save it. Assign each person separately. This is done once; later scenes retrieve the stored references automatically. Uploaded PNG/JPEG/WebP/AVIF references and additional views are also supported. An uploaded photograph without a detectable face can be saved as a whole-photo reference; a face cannot be assigned from a multi-person scene without selecting its crop.
4. Type a short next instruction, such as “The man looks shocked and steps backward.” The current scene supplies clothing and location context. Original references remain available. The new frame becomes a child of the selected parent.
5. Open an earlier thumbnail to inspect it. **Continue from here** changes the continuation point. **Branch from here** stages an alternative child of that frame without deleting existing children. **Retry with adjusted settings** copies the instruction and its original parent back into the form; generation remains an explicit action and the old result stays available.
6. Use Compare with for a two-scene view, Previous/Next for chronological navigation, Download PNG, and metadata/reference inspection. Review continuity saves a manual pass/partial/fail result separately from generation status. The timeline distinguishes “generated” from the visual review.
7. Character metadata, aliases, default/current clothes, facial features, hair, skin tone, approximate age, accessories, relationships and lock preference are editable. Unknown visible attributes stay unspecified. Deletion removes a character from active retrieval but retains historical scene records and original files. Adding a view never replaces the original reference.
8. Location manager stores named environment descriptions and optional reference images. Continuity controls preserve faces/clothes/environment/style by default; explicit changes in the prompt take priority. Optional camera, lighting and location changes, cast selection, manual reference views, seed, steps and aspect ratio are available in folded controls.

Ordinary Create/Edit/Restyle and their privacy behavior remain separate. Story prompts and compiled continuity instructions are deliberately saved inside the story store. They are not embedded in generated PNG metadata or saved in browser prompt preferences.

## Generate up to five scenes automatically

In the desktop app, open **Visual Memory**, choose a story and a **Continue from** starting scene, then set **Generation workflow → 5-scene sequence**. Five numbered prompt boxes replace the single prompt. Fill them in order and click **Generate sequence** once. Unused final boxes may be blank; gaps between filled boxes are rejected. To generate a new opening, select **Start a new scene**. To continue an existing story, choose its completed starting frame.

The server generates Scene 1, waits until its image and memory are committed, then uses that exact scene ID as Scene 2's parent, and so on. Only the existing serial Studio GPU worker performs inference. Queues do not run all five models in parallel. Mode, aspect ratio, seed and continuity settings are shared across the five boxes; leave cast/location automatic if different scenes require different people or settings. An API caller can supply separate supported settings per scene. Preview retrieved context previews the first prompt; later context cannot be retrieved until earlier frames exist.

The **Scene sequence** panel shows each item's generated/running/not-started status and opens completed images. **Stop sequence** requests cancellation of its active job and prevents later scenes; completed images stay in history. Generation errors also stop the sequence. **Resume unfinished scenes** uses the stored prompts/settings, skips successful items and retries the first unfinished item with a new scene ID. Failed attempts remain in history. Editing the five boxes does not change a submitted queue or a Resume operation; use Generate sequence for a new edited run.

Submitted prompts, requests and item/attempt IDs are stored in SQLite's `sequences` table. Refreshing reconnects to the existing run, including prompts/settings, rather than submitting again. Duplicate submission keys cannot create a second queue; an active sequence excludes another scene/sequence/history mutation in that story. A full server restart pauses unfinished sequences for explicit resume. A browser page can close while a server-owned queue continues; closing the desktop app may also stop its owned server, in which case recovery applies. Unsubmitted box drafts are not saved in browser storage.

For strong character references, register characters and associate faces before queueing. A fresh story can continue using previous scene images, but the queue does not automatically identify or merge faces. It does not solve the separately documented named-action, car-location, pose or crying failures. A completed sequence means images were generated, not that each image visually followed the prompt; review each result. No automated visual-failure detector or unlimited retry loop was added.

Implementation: `story_sequences.py` coordinates the existing adapter, `visual_memory.py` stores sequence records and guards story mutations, and `story_api.py` adds POST sequences, GET sequence, POST cancel and POST resume routes under `/api/stories/{story-id}`. The request accepts 1–5 `NextScene` entries plus an idempotency key. Only the first entry may choose a starting parent; subsequent parents come from completed results. Original references and prior scenes are never replaced.

Queue regression command:

```powershell
.venv\Scripts\python.exe -m unittest test_story_sequences test_visual_memory test_qwen_core_ui test_ui_layout test_desktop_app
```

This selected suite passed 48 tests. The sequence unit tests use explicitly labeled bitmap fixtures, not model outputs, to verify five-step chaining, duplicate/concurrent submissions, failure/resume, cancellation, immutable references, restart recovery and the reservation-to-item crash window. The real five-scene UI run is recorded separately in `outputs/story_tests/sequence_20261015_1800/`.

Pre-queue rollback snapshot: `outputs/.rollback/20261015_175114_698823_identity/`. Preview using `tools/identity_snapshot.py restore` with that path; apply only when rollback is requested. It preserves the user's earlier working-tree changes. Story data and model files are retained.

## Data, branching and recovery

Default storage is `stories/`:

```text
stories/
  memory.sqlite3
  <story-id>/
    characters/<character-id>/references/<unique-id>.png
    characters/<character-id>/faces/<unique-id>.png
    scenes/<scene-id>/<unique-id>.png
    locations/<location-id>/<unique-id>.png
    memory/reference_boards/<content-hash>.png
```

SQLite stores story/character/location/scene metadata and graph edges. Images are files with SHA256 integrity records. Original references are immutable: neither generation nor a new upload overwrites them. Faces and names are associated by explicit character IDs, never merged by similar descriptions. Facial comparison vectors remain transient; this is not a face-identification database.

Each scene saves parent and previous pointers, requested instruction, structured changes, cast, intended clothing/location/lighting/style/camera/props, selected reference roles, model settings, generation output, optional advisory face comparisons and manual review. **Intended metadata is not proof that the pixels obey it.** Clothing on an earlier branch comes from its parent scene, not another branch's latest clothing. Character current-clothing fields show the latest committed appearance and supply defaults for new openings; parent snapshots take precedence when continuing old branches.

Successful generation writes a unique image first, then atomically commits the new scene, character appearances and current pointer. Failure/cancellation keeps the previous pointer. Startup marks interrupted story jobs failed with a retry explanation. One pending generation is allowed per story; Studio's existing single GPU worker serializes inference. Scenes with children cannot be deleted until their leaf scenes are removed. Scene/character deletion is soft and retains files for recovery; it is not secure disk erasure.

Back up the entire `stories/` folder after stopping the app, including its SQLite database and image subfolders. It is excluded from Git. Moving only the web files or only the database does not preserve the story's assets.

## Retrieval and native model adapter

- `visual_memory.py`: transactional storage, stable IDs, immutable assets, graph, branch state and recovery.
- `continuity_engine.py`: deterministic alias/pronoun retrieval, intentional changes, reference planning and Qwen instructions. Ambiguous aliases or pronouns require explicit cast selection. Unrecognized complex language is passed through as the original instruction; this parser is not a general language-understanding model.
- `story_api.py`: validated local API, CPU preprocessing/evaluation, memory-pressure heuristic and adapter to Studio's existing GPU queue.
- `studio_service.py`: existing generation remains intact. Story jobs select their saved continuity prompt before calling the same workflow builder and existing ComfyUI client.
- `web/story.*`: independent workspace inside the existing Studio visual system.

The actual installed native `TextEncodeQwenImage21` node splices indexed reference images into the text encoder and VAE sequence. Its first reference determines an edit canvas. The adapter uses only the already-supported source/reference interfaces in `qwen_engine.py`: up to three images in this 6-GB profile, even though the native node accepts more. See the [official ComfyUI Qwen Image 2.1 workflow documentation](https://docs.comfy.org/tutorials/image/qwen/qwen-image-2-1).

For consecutive moments, the previous scene is normally the editing source. Major movements and hand-held interactions can instead compose a new scene using confirmed faces and recorded current outfits; this prevents an old stationary pose or a tight portrait from dominating the requested action. If faces or outfits are not recorded, the previous image remains available as appearance evidence. Confirmed faces are packed into a small CPU reference board, including when both came from the same original scene. The old full composition is no longer substituted for those head crops: doing so reinforced stale poses and backgrounds. Explicit manual views remain available. Unused references are not loaded into the inference workflow, and omitted references are recorded rather than silently claimed to be used.

When the location changes, the planner releases the old room image and uses the selected character references on a fresh canvas. This materially improved the rain test, but reduces the available visual evidence for exact garment construction. Requested new people do not inherit replaced identities. Original identity assets are never replaced with later generated faces.

Contact instructions can extract actor, recipient and explicit body regions. Slaps add palm-to-face contact context without choosing an unrequested left/right side. Large motion edits allow pose/expression changes and do not demand preservation of the previous hand positions. No pose ControlNet, new LoRA or new model training is introduced.

## Hardware and configuration

### Scene adherence correction — 2026-10-16

The scene planner now puts the requested change first in a compact, single-paragraph instruction. It no longer automatically freezes pose, hand positions, facial expressions or camera composition when its verb detector fails to recognize an action. User instructions to retain a pose still reach Qwen verbatim. Car interiors and additional destinations are recognized, and repeating the same recorded location does not unnecessarily discard the preceding scene.

Opening clothing descriptions are saved by person and garment in each scene's `wardrobe` record. A changed garment updates only its owner and leaves the rest of that outfit intact. Removed jackets stay removed in subsequent scenes. Parent snapshots determine clothing on branches. This is intended state for conditioning; pixels still require review.

Face references from one original photograph now use head crops rather than repeating that whole photograph. This prevents an obsolete full composition from acting as an extra identity reference. On a location change with no assigned face references, the prior scene can supply appearance guidance, with an explicit warning, rather than silently losing all identity evidence.

An isolated native Balanced/28 edit successfully added a blue cup next to the stored red ball using CFG 1 and the installed INT8 models: `outputs/story_tests/native_edit_20261016_151846/`. The previous failure therefore cannot be attributed solely to the installed model's inability to add an object. The character acceptance queue is recorded separately under `outputs/story_tests/continuity_fix_20261016_152423/`; generation completion and manual visual review remain separate.

The desktop launcher also checks whether its Studio port is already listening after an HTTP timeout. It refuses to launch a second backend in that case; previously a failed second startup could run recovery against an active story before failing to bind the occupied port. Restart the desktop launcher/backend to load Python code changes. During this session, automatic process-stop approval was rejected, so the updated acceptance server uses local port 7861 while the original port 7860 remains available.

The final five-image comparison is `outputs/story_tests/continuity_fix_20261016_152423_recompose_from_5/`, including `index.html`, `run.json`, numbered PNG files and `scenes_1_to_5.zip`. Opening and argument frames are retained from the acceptance queue; one explicit three-scene tail retry tested major-action composition, and a final single-scene comparison tested phone reframing at the same prompt/seed. It is one valid parent chain. All five final frames passed manual visual review for the specified action and visible continuity: argument gestures, both-hand chest contact with recoil, tears in a car, and phone use with the requested red cardigan. Original unsuccessful attempts remain in earlier reports. Close-ups cannot verify hidden garments, and recognizable faces are not a biometric identity guarantee.

The targeted regression suite passed **67 tests** (`test_visual_memory`, `test_story_sequences`, `test_qwen_engine`, `test_desktop_app`), and Python compilation passed. Final new-composition frames took 62.2, 57.7 and 78.7 seconds, with sampled total-device VRAM below 5 GB for those three frames. These timings are specific to this run. The activation step for the existing desktop backend remains pending a normal restart because automatic process-stop approval was rejected.

Inspected target: RTX 4050 Laptop GPU, 6,141 MiB VRAM, about 16 GB system RAM. The existing ComfyUI command uses `--lowvram --reserve-vram 1.0`. Models:

- Diffusion: `qwen_image_2.1_int8_convrot.safetensors`.
- Text/vision encoder: `qwen3vl_8b_int8_convrot.safetensors`.
- VAE: `qwen_image_2.1_vae_bf16.safetensors`.

Logs show BF16 model computation/manual casts, FP16 text-encoder runtime, BF16 VAE, CPU offloading, and PyTorch VAE attention. Fast uses 512/16 steps and no prefix cache; Balanced uses 768/28 and CPU INT8 prefix cache with tiled decode; Ultra uses 1024/36 with the same cache/decode policy. Guidance remains 1.0, Euler/simple. These are the existing profiles; the memory database and image archive use no VRAM. References load lazily for the active job, and temporary Comfy uploads use the existing cleanup lifecycle.

`config/visual_memory.json` configures story storage, UI defaults and optional installed ComfyUI model filenames. `QWEN_STORY_DIR` overrides the disk store. `COMFYUI_URL` configures the existing backend connection. Model filenames are resolved under the installed ComfyUI `models/diffusion_models`, `text_encoders` and `vae` directories; files are validated and never overwritten. Restart the app after changing this configuration. Other compatible models would need an adapter rather than pretending to accept Qwen inputs.

Before generation, a profile/reference-count heuristic records estimated memory pressure and current GPU/RAM statistics. It is not a measured fit guarantee. Actual generations record two-second total-device VRAM samples. OOM and model errors become failed scene records; retry with Fast/fewer references/lower resolution remains available. No intentional real GPU OOM was forced during testing.

## Tested results — 2026-10-15

The selected regression suite passed **217 tests**, including 18 Visual Memory tests at that point. After the final pose-preservation and deleted-character refinements, the current **20 Visual Memory tests** also passed. The memory tests use explicitly labeled bitmap fixtures and mocked inference to exercise persistence, required scene-chain state, immutable originals, clothing ownership, branch restoration, new characters, ambiguity, reference limits/deduplication, path/cross-story validation, concurrency, interruption recovery and simulated CUDA OOM. These unit fixtures are not presented as model results.

Real local Qwen testing generated the opening, consecutive action, reaction, environment change and an alternative branch. Original face references were associated through the actual browser UI. Application processes were restarted, the same story/character records reopened, and further generation continued without re-upload. Live cancellation retained the previous successful scene and all references. Browser checks also covered context preview, reference association, comparison, retry staging, review status, page reconnection and responsive layout.

Reviewed report: `outputs/story_tests/20261015_164240_957271/index.html`, with prompts, settings, scene IDs, parent IDs, output images, timing, measured VRAM and pass/partial/fail notes in `run.json`. Earlier diagnostic runs remain available and are not overwritten.

| Real case | Time | Sampled device VRAM | Visual conclusion |
| --- | ---: | ---: | --- |
| Opening conversation | 53.6 s | 4.77 GB | Pass: two distinct adults, red dress/black shirt, apartment. |
| First shared-anchor slap | 67.5 s | 4.84 GB | Fail: continuity holds, but the contact resembles a high-five. |
| Shock/backward reaction | 80.4 s | 4.98 GB | Partial: recognizable people/clothes/room, surprised expression and backward lean; cropped feet prevent step verification. |
| Previous-scene rain edit | 77.0 s | 4.90 GB | Fail: room and extra person remain. This motivated changing location transitions to identity references on a fresh canvas. |
| Alternative goodbye branch | 63.8 s | 4.90 GB | Pass: woman waves, man returns the wave, recognizable identities/clothing/apartment. Parent is the opening; existing children remain. |
| Explicit-contact slap retry | 66.5 s | 4.86 GB | Partial: palm contacts upper cheek/temple correctly; recoil is mild and impact motion is not established. |
| Identity-guided rain retry | 64.8 s | 4.93 GB | Partial: woman alone outdoors in rain, running-arm movement; clothing construction and facial resemblance drift, and legs are cropped. |

All seven above are actual Balanced/28-step, 896×672 outputs with the existing default LoRA disabled. Peak samples are total device usage, not per-model allocations or guaranteed allocation peaks. Reopening, branching, hashes and metadata persistence passed structurally; **identity/action/clothing preservation is not a universal visual pass**. The original two-independent-face route also produced a male close-up instead of the requested interaction and is retained as a failed diagnostic.

Automatic evaluation currently compares detected faces on CPU and reports face-count concerns. It does not certify fingers, action correctness, garments, lighting or background consistency. Scores are raw advisory cosines, not accuracy percentages. No additional GPU vision judge is loaded. The optional manual review and explicit one-at-a-time retry preserve the generated result; there is no automatic retry loop. These limits are visible in the UI and report.

## API and test commands

### Qwen story enhancement — 2026-10-16

In Visual Memory, choose **5-scene sequence**, enter contiguous scenes, then click **Enhance story (Qwen)**. Review the rewritten boxes before clicking **Generate sequence**. Enhancement reads all entered scenes in one batch and uses established character/outfit facts from the selected parent branch. Explicit changes start at the requested scene; new actions are not constrained to the previous pose. It adds visual action/expression clarity without deliberately choosing unspecified colors, lighting or artistic styles. This is grounded local prompting, not model-weight training and not the official larger prompt-enhancer checkpoint.

The installed `qwen21-qwen4b` Ollama model runs through Studio's existing resource lock and sequential GPU management. No new download, online image service or database is required. Dolphin and the main Studio enhancer remain available separately. The story enhancer sees text context, not the uploaded images; it cannot guarantee exact identity, anatomy or adherence in generated pixels.

Enhancement is an explicit review step, not automatic generation. **Undo enhancement** restores the pre-enhancement text only where it has not since been edited manually. Invalid output receives at most one repair request; otherwise the original scene boxes remain unchanged with an error message. Generation and enhancement cannot be started together from this page. Input is bounded to five scenes, 4,000 characters per scene and 10,000 total characters for this local profile.

API: `POST /api/stories/{id}/enhance` accepts `scenes`, optional `parent_scene_id` and `character_ids`. It returns ordered scene prompts, local model/profile, timing and model-call count, without creating image jobs or changing saved story history. Regression command: `.venv\Scripts\python.exe -m unittest test_story_prompting test_visual_memory test_story_sequences test_qwen_engine test_desktop_app` (78 passing tests). Live text/UI evidence is recorded separately under `outputs/story_tests/story_enhancer_20261016/`; those rewrites are not image-generation quality results.

The backend must be restarted normally to activate Python changes. The existing port 7860 process was not forcibly stopped; the updated verification app is on port 7862. Rollback snapshot for existing source files: `outputs/.rollback/20261016_155522_322909_identity/`. New standalone enhancer files are retained harmlessly if the older route/UI is restored; the bounded flowing-tears validator correction is separate from that snapshot.

Core routes: `POST/GET /api/stories`, `GET /api/stories/{id}`, character create/PATCH/reference upload/scene-face assignment/delete, location create, `/plan`, scene generation/continue/review/cancel/delete, and validated story image access. `/api/jobs/{job-id}` supplies the existing progress stream. All app calls stay local.

```powershell
.venv\Scripts\python.exe -m unittest test_visual_memory
# Real GPU test: unique report; inspect its opening and assign face IDs before continuing.
.venv\Scripts\python.exe tools\test_story_live.py --opening
.venv\Scripts\python.exe tools\test_story_live.py --resume outputs\story_tests\YOUR_RUN
```

Do not rerun the real test against a busy GPU or replace a supplied run path casually. The runner saves scene IDs before waiting, so it can collect an interrupted test without submitting duplicate jobs.

## Revert

The pre-Visual-Memory code snapshot is `outputs/.rollback/20261015_161822_380549_identity/`. It preserves the user's earlier working-tree edits. Preview:

```powershell
.venv\Scripts\python.exe tools\identity_snapshot.py restore outputs\.rollback\20261015_161822_380549_identity
```

Use `--apply` only when reverting is requested. The snapshot verifies sealed hashes and refuses to overwrite later edits; displaced code is saved recoverably. Story data, models and test images are retained. The added Git ignore entry and test helper files are harmless retained support files if the feature is rolled back. The independent UI review/evidence lives under `.impeccable/review/`.
