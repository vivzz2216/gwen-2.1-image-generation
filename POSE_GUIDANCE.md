# Human anatomy and pose guidance

`pose_prompting.py` appends soft guidance for recognized human poses and multi-person scenes. It keeps limb ownership separate, asks for coherent joints and contact, respects occlusion and cropping, and discourages superimposed successive poses. It does not rewrite the original instruction, reject a request or load another model. Intentional unusual anatomy and surreal poses are left alone. Recognition is lexical and cannot understand every possible scene.

Both direct generation paths, including the existing clean-prompt path, receive the guidance. Optional Dolphin enhancement receives the same pose brief. Clothing-only edits do not acquire a new pose instruction. UI, model weights, LoRA configuration, mode resolutions and sampling settings are unchanged.

Set `QWEN_POSE_GUIDANCE=0` before launching Studio to disable this addition. No strict count/color/style rejection layer is reintroduced.

The current CFG is 1. ComfyUI skips negative conditioning at this value, so anatomy guidance belongs in the positive prompt rather than an ineffective negative-prompt list. See [ComfyUI's official Qwen Image 2.1 settings](https://docs.comfy.org/tutorials/image/qwen/qwen-image-2-1). Increasing CFG or resolution may increase cost and affect appearance; neither is changed automatically on the 6 GB GPU.

Run `python -m unittest test_pose_prompting` for regressions. `tools/verify_pose_guidance.py` runs fixed-seed base-model comparisons with no LoRA and saves original/compiled prompts, images, settings, timing, sampled total-device VRAM and errors in a unique `outputs/pose_tests/` subfolder. Visual outcomes must be reviewed manually. Generation completion alone is not an anatomy pass.

This is a prompt-level mitigation, not anatomical correction or a pose controller. Hard interactions may still need a pose/reference image, localized editing or another seed. No promise of zero extra limbs is made.
