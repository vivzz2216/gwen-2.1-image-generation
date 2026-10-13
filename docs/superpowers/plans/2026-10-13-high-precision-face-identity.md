# High-Precision Face Identity Preservation (7.5 -> 10/10) Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Elevate facial identity precision and likeness from current 7.5/10 (~0.78-0.84 similarity) to 9.5-10/10 (>0.92-0.96 similarity) across all angles, poses, and distances while strictly honoring the 6GB VRAM limit on NVIDIA RTX 4050.

**Architecture:** A 4-pillar identity pipeline combining (1) Landmark-Guided Contour Segmentation Masking, (2) Neural Face Restoration (CodeFormer with adjustable fidelity $w \in [0.7, 0.85]$), (3) Multi-Angle Reference Bank (Front, 3/4, Profile), and (4) Adaptive Denoise & Guidance Tuning.

**Tech Stack:** Qwen 2.1 (GGUF/Convrot), ComfyUI, PyTorch, Spandrel / CodeFormer ONNX, OpenCV (YuNet/SFace), Pillow, FastAPI.

---

## Root Cause Analysis: Why 7.5/10 is the Current Ceiling

1. **Diffusion ViT Token Compression**: Qwen 2.1 compresses reference images into coarse vision tokens (14×14 patches). It learns semantic concepts ("brunette woman, round eyes"), but loses micro-biometrics (exact iris patterns, subtle philtrum curvature, unique jawbone angle).
2. **Single-Angle Reference Mismatch**: In dynamic action poses (e.g. running profile, cycling 3/4, motorcycle looking up), conditioning diffusion on a single frontal selfie forces the model to hallucinate the unseen side of the face.
3. **Hardcoded Geometric Ellipse Mask**: `face_detail.py` currently draws a simple mathematical ellipse (`draw.ellipse`), which clips hair bangs, blurs into collars/helmets, or leaves hard boundaries around the chin.
4. **Absence of Neural Face Restoration**: Modern photorealistic face pipelines (ReActor, InstantID, Midjourney Character Ref) universally pair diffusion with CodeFormer/RestoreFormer to reconstruct high-frequency facial micro-features and eliminate plastic AI smoothing.

---

## Proposed Changes

### Component 1: Landmark-Guided Contour Segmentation Mask
- Replace the crude geometric ellipse in `face_detail.py` with an anatomical landmark-aware contour mask using detected facial landmarks.
- Cleanly protects hair bangs, ears, and clothing collars from smearing.

### Component 2: Neural Face Restoration (CodeFormer Module)
- Add `face_restore.py` leveraging `spandrel` (already installed in ComfyUI environment) or lightweight ONNX runtime.
- Runs post-inpaint on the 768px face crop with adjustable fidelity slider ($w=0.8$).
- Restores real iris clarity, sharp lashes, teeth, and skin micro-texture without AI plastic smoothing.
- Pure CPU/sequential GPU execution consuming <300MB RAM.

### Component 3: Adaptive Denoise & Prompt Anti-Degradation
- Dynamic denoise tuning based on face size (0.48 for large faces, 0.60 for medium, 0.68 for distant action poses).
- Negative facial guidance to eliminate asymmetrical pupils and plastic skin.

### Component 4: Multi-Angle Reference Bank (Studio UI & Service)
- Enable 1-3 reference photos (Front, 3/4, Profile) in Restyle mode.
- Match reference angle to the generated head pose.

---

## Verification Plan

### Automated Tests
```powershell
.venv\Scripts\python.exe -m unittest discover -s . -p "test_*.py"
```

### Benchmark Verification
```powershell
.venv\Scripts\python.exe tools/compare_10_prompts.py
```
