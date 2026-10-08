# Better instruction understanding and person editing without LoRA

This plan is based on the current code and the five-photo test suite. Ten jobs completed, but visual review found four passes, five partial results, and one failure. Model completion and unchanged protected pixels do not establish that the requested change happened.

## 1. Build an edit plan from the request

Replace the single keyword target decision with a structured plan containing the requested action, subject, affected regions, attributes to change, attributes to preserve, and whether new space is required. Start with deterministic parsing for common requests and keep a fallback for ambiguous instructions.

Examples:

- "Make my shirt blue" changes fabric color; preserve its cut, pose, face, and background.
- "Replace my dress with a suit" changes garment shape and coverage; the mask must include skin or surrounding space that the new clothing will occupy.
- "Raise my left arm" changes pose and hand position; preserve the face and unrequested body parts. Include both the old arm position and the new arm position in the editable area.
- "Change my clothes and add sunglasses" becomes two ordered edits instead of an unsupported multi-target request.

Current evidence: `detect_edit_target` uses keyword matching and rejects multiple areas. `compile_prompt` always includes pose and hand positions in its preservation list for preserved edits, even if those are the requested changes. "Enhance prompt" currently appends a generic sentence rather than reading the image.

## 2. Fix accessory anchors and mask completeness

The current accessory heuristic takes one bounding box around all confident face-label pixels. A distant false-positive component can shift the mask toward the torso. Use a validated face component initially, then add pretrained face and pose landmarks for eye angle, jaw, shoulders, and neck placement. These models need no LoRA training.

Keep all masks reviewable. Reject uncertain anchors instead of presenting a misplaced estimate as reliable. Remove small isolated clothing components conservatively. Treat straps, hems, and existing jewelry explicitly: our black-top test changed the fabric panels but missed the straps.

MediaPipe provides [face landmarks](https://developers.google.com/edge/mediapipe/solutions/vision/face_landmarker) and [body pose landmarks](https://developers.google.com/edge/mediapipe/solutions/vision/pose_landmarker). Their geometry can guide masks; it does not itself understand a text instruction or guarantee a generated pose.

## 3. Use context appropriate to the edit

Choose context from anatomy as well as mask bounds: sunglasses need eyes, temples, and head angle; necklaces need neck, shoulders, and upper chest; outfit replacement needs garment silhouette and nearby hands. Preserve enough of the source image in visual conditioning to establish scale and placement.

For the necklace failure, compare the raw ComfyUI output with the stitched final image before deciding whether the model failed to create the accessory or the approved mask clipped it. Existing results alone do not establish the cause.

## 4. Add an image-aware instruction rewriter

Qwen publishes an official image-edit prompt rewriter, `Qwen-Image-2.1-PE-I2I`, based on Qwen3.5-VL 9B. Its role is to inspect the source and turn vague requests into specific edits. See the [official prompt rewriting documentation](https://github.com/QwenLM/Qwen-Image-2.1#prompt-rewriting).

On the RTX 4050 6 GB configuration, benchmark quantized/offloaded inference before adopting it. Run analysis before diffusion and release its resources first. A smaller local vision-language planner is an alternative, but its instruction quality must be measured. The existing Qwen text encoder is not an exposed general-purpose planning/chat service.

## 5. Check whether the edit visibly happened

Keep the exact exterior-pixel check, and add separate checks for requested attributes: accessory present, clothing color changed, requested pose approximately reached, and unexpected facial changes. Use simple measurements where sufficient and an optional image-aware evaluator for semantic checks.

Allow a bounded retry when an edit is missing: adjust crop context, mask, or instruction based on the failure. Record attempts and limitations. An evaluator must not be presented as a guarantee of quality or identity.

## 6. Make modes differ in useful work

Retain current Fast/Balanced/Ultra resolutions and resource limits. Benchmark edit-specific denoise settings rather than assuming one strength works for recoloring, accessory additions, and garment replacement. Ultra can optionally perform one focused refinement or evaluate a second candidate sequentially, with a clear time cost.

Qwen supports native 2K generation according to its official repository, but current Ultra uses a 1024-area setting. Treat a 2K option as a separate measured experiment on this hardware, not a free quality upgrade.

## Recommended order and verification

First fix instruction conflicts and accessory anchors. Then implement multiple ordered edits, anatomy-aware context, and visible-result checks. Finally benchmark the image-aware rewriter and stronger Ultra processing.

Reuse the existing five photos to compare before/after behavior. Add explicit action tests (such as changing one arm position), multi-edit requests, and deliberately ambiguous prompts. Judge instruction adherence, mask coverage, exterior preservation, and runtime separately. Keep the Recent images section removed as requested.
