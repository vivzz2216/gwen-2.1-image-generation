# Identity Assist

An optional local Qwen Image 2.1 workflow for a selected person in different actions. It is native image-reference guidance, not a trained FaceID adapter or an exact face lock. The existing design and Fast/Balanced/Ultra settings are retained.

## Use

1. Open **Qwen-Image-Core Studio**, above the prompt, and enable the reviewed identity reference. This is the UI name for Identity Assist, not a separate model.
2. Upload PNG/JPEG/WebP/AVIF (25 MB maximum), or use the original Edit photograph. AVIF is decoded by the browser and converted in memory to a PNG copy before local detection/generation; the original is not modified. Unsupported AVIF encodings offer an export-to-PNG error. Inputs are limited to 24 megapixels and 8192 pixels per side; the normalized PNG must also fit the 25 MB limit. Other upload controls still accept PNG/JPEG/WebP only.
3. CPU YuNet outlines and numbers detected faces. With multiple faces, select the intended one; no face is automatically approved. The selected head crop includes hair/head context. Inspect it at full size and adjust its percentages if necessary. Missing detection offers a manual crop, not a fake detected face.
4. Approve the head preview. Changing the photograph, crop, selected face or Create/Edit role revokes approval.
5. Press **Write the action or scene** to focus the existing prompt. Enter the desired action, choose Fast/Balanced/Ultra, and use the existing Generate button; no duplicate prompt or run controls. Create uses the full identity photograph as image 1 and the head crop as image 2. An optional reviewed pose photograph is image 3, used for movement only. The chosen aspect ratio controls the creation canvas, independently of the reference photograph's shape.
6. Edit uses the original scene as image 1 and the identity head crop as image 2. For unchanged-face clothing edits use a reviewed mask excluding the face. Movement needs a global edit or a sufficiently large reviewed movement mask.

Identity Assist reserves the two extra reference slots for the head crop and optional pose on this 6 GB configuration. An appearance reference is rejected explicitly rather than silently dropped. Batch jobs reuse the reviewed references, but output faces/actions must each be reviewed.

## Face repair

After a Studio result, choose **Repair a face, hand, leg or contact area**, then **Face / likeness (Identity Assist)**. Enable Identity Assist and approve its head crop again. Describe the correction while retaining the desired expression and head angle. Paint and approve a mask with sufficient face/head/neck context. Ultra uses a focused crop for face repair when Pose Assist is off (repair entry turns it off). Repairing a result requires its unchanged source, a reviewed custom mask and protected pixels. Only one successful/in-progress repair is permitted per original result; there are no automatic repair chains or retries.

## What is measured

CPU OpenCV SFace compares the reviewed head crop with each detected output face. Raw cosine scores appear in result warnings and generation details. They are not percentages, calibrated quality grades or proof of identity. Detection may fail for tiny, profile, hidden or stylized faces. No automatic pass threshold or person-name identification is used; face embeddings stay in memory and are not saved in a face database.

Masked edits measure exact RGBA equality outside the black mask. An unchanged face wholly outside that mask can retain its original pixels. A new head angle/expression regenerates facial pixels; likeness cannot be guaranteed. Full-body Fast output contains much smaller faces than a close-up, so likeness measurements and fine details may be weaker. Native references can also pull old background/composition into a requested new scene. Do not treat generation completion as a pass.

## Runtime and models

The desktop launcher serves this project's Python backend and web files; no second UI build is required. `requirements_identity.txt` pins CPU-only OpenCV. Installed official models under `models/identity_assist/`:

- YuNet 2023: 232,589 bytes; SHA256 `8f2383e4dd3cfbb4553ea8718107fc0423210dc964f9f4280604804ed2552fa4` (MIT).
- SFace 2021: 38,696,353 bytes; SHA256 `0ba9fbfa01b5270c96627c4ef784da859931e02f04419c829e83484087c34e79` (Apache 2.0).

Licenses are saved next to the models. Integrity is checked before inference. No LoRA, cloud image service, InstantID/PuLID or heavy ControlNet is installed by this feature. Detection/scoring uses CPU and two OpenCV threads. Image generation stays on the existing local ComfyUI pipeline. Analyze uploads are not persisted; job references follow the existing local Studio file lifecycle. Images/face selections are not stored in browser preferences. The public job API hides internal head-crop paths and user prompts.

## Tests and rollback

The Qwen-Image-Core Studio UI extension passed the selected 168-test regression suite and JavaScript syntax check. Browser checks covered real `jenna-test.avif` upload, local PNG conversion, detection/head preview, rejected unreviewed submission, approved prompt shortcut, Create/Edit approval invalidation, photo removal and desktop/mobile overflow. No new GPU render was started for this UI-only extension. Its pre-change snapshot is `outputs/.rollback/20261012_192316_487351_identity/`; use the same preview/explicit-apply workflow below with this path to revert only this stage. Development evidence is under `.impeccable/review/core-verification.md`.

`test_identity_assist.py` covers review/crop validation, reference slot order, explicit conflicts, batch reuse, privacy, selected creation dimensions, face-repair requirements and enhancer roles. The selected regression suite passed 165 tests. `tools/test_identity_live.py` creates unique local-only galleries under `outputs/identity_tests/`. The reviewed run is `outputs/identity_tests/20261012_182045_594032/index.html`: five API-render cases plus browser-submitted cup generation and one face repair. Fast cases took about 44–49 seconds; the Balanced smile took 114 seconds. Ultra face repair with an explicit 16-step override took 93 seconds (not the default 36-step Ultra benchmark). One-second total-device VRAM samples for the first five cases peaked at 5,165 MiB; the two browser cases were not continuously sampled.

Results are partial, not universal successes: full-body faces were small, the requested park location and head-and-shoulders framing were missed, and the tiny sleeve color change was not convincing. Cup generation had stronger face resemblance (cosine 0.822); its repair measured 0.8281, not a demonstrated material improvement. Repair preserved all 237,423 outside-mask pixels exactly. The sleeve test preserved all 4,145,910 outside-mask pixels, independently of its failed visible color change. No whole-shirt replacement or perfect face lock is claimed. Original `assets/example-38.jpg` was unchanged.

Pre-change code snapshot: `outputs/.rollback/20261012_180736_156712_identity/`. The snapshot preserves existing user edits, excludes original photos/models and must be sealed after the final edits. Preview rollback with:

```powershell
.venv\Scripts\python.exe tools\identity_snapshot.py restore outputs\.rollback\20261012_180736_156712_identity
```

Use `--apply` only when rollback is requested. It refuses subsequent file changes and saves displaced current code recoverably. Model/dependency files are retained; it never resets Git or removes source photos.

Sources: [Qwen 2.1](https://github.com/QwenLM/Qwen-Image-2.1), [official identity-aware editing instructions](https://huggingface.co/Qwen/Qwen-Image-2.1-PE-I2I/blob/main/system_prompt.txt), [OpenCV YuNet](https://github.com/opencv/opencv_zoo/tree/main/models/face_detection_yunet), [OpenCV SFace](https://github.com/opencv/opencv_zoo/tree/main/models/face_recognition_sface).
