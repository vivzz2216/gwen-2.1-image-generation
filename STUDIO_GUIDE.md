# Qwen Image 2.1 Studio (Windows Desktop App)

Launch via **`QwenImageStudio.exe`** or **`start_studio.bat`**. This opens a dedicated
standalone Windows desktop application window (powered by Microsoft Edge WebView2)
without console clutter or browser tabs. The launcher automatically starts or reuses the
bundled CUDA ComfyUI backend at http://localhost:8188 and local Dolphin prompt enhancer.

### Launching & Shortcuts
- **Direct Executable**: Double-click `QwenImageStudio.exe` (or `launch_app.vbs` for silent background start).
- **Desktop Shortcut**: Run `create_desktop_shortcut.bat` to create a dedicated shortcut on your Windows Desktop and Start Menu.
- **Batch Launcher**: Run `start_studio.bat` (or `start_studio.bat --browser` for external browser mode).
- **Hotkeys**:
  - `Ctrl + Enter`: Generate / Run current task
  - `Ctrl + S`: Download result PNG
  - `Ctrl + O`: Choose/open image for editing
  - `Ctrl + Shift + O`: Open outputs folder in Windows File Explorer
- **Drag & Drop**: Drag any image from Windows Explorer directly into the app window to start editing.


## Choose a mode

| Mode | Square generation | Default steps | Processing |
| --- | --- | --- | --- |
| Fast | 512 × 512 | 16 | Smallest native pass, cache off for lowest RAM, CUDA decode |
| Balanced | 768 × 768 | 28 | CPU INT8 prefix cache with Comfy's memory fallback, tiled decode |
| Ultra | 1024 × 1024 | 36 | Native higher detail, CPU INT8 cache, tiled decode; focused context crop for masked edits |

All modes use Euler, the simple scheduler, CFG 1, and ComfyUI's streamed CUDA
execution for the text encoder and VAE. Weights larger than VRAM are offloaded by
ComfyUI's dynamic memory manager. The model does not need to fit wholly in VRAM.
Other aspect ratios retain similar pixel area and use multiples of 32. Advanced
settings allow repeatable seeds and a step override. Mode names specify actual
model work, not an interpolated enlargement. Runtime depends on reference count,
image shape, paging, and whether model weights are already loaded. The UI displays
elapsed time and real sampler steps where ComfyUI emits them.

## Edit an existing image

1. Choose **Edit image** and upload the original photo. Describe the requested change.
2. For a local change, enable preservation and choose **Auto** or an explicit target.
3. Click **Find edit area**. Review the highlighted region. Use paint, erase, undo,
   or a separate mask PNG to correct it. White means editable; black means protected.
4. Click **Use reviewed mask**, select the mode, then run the edit.
5. Compare the original and edited image with the split slider. Download the PNG
   and its ComfyUI API workflow, or use the result as the source for another edit.

Automatic masks cover clothes, tops, shoes, hair, and background. Added sunglasses
or a necklace use estimated face/neck anchor regions when the accessory is absent.
These estimates always need review. Rings, watches, unusual objects, multiple
people, or unsupported instructions require a manual mask or a global edit.
The detector may miss a small, obscured, or stylized person. Paint a mask when that happens.

Extend the mask into adjacent space when changing the clothing silhouette, adding
a collar, or making hair longer. Protect hands, face, jewelry and props by erasing
those areas from the mask. Native Qwen reference conditioning and masked latent
sampling guide the change. Final compositing keeps the original resolution and
the exact original pixel values outside the approved binary mask. Feathering is
confined to its interior. Pixels inside the mask can change; no model can promise
unchanged facial identity when you include the whole face in the editable region.

Ultra processes the masked region with surrounding context at the larger model
resolution, then stitches it back into the original canvas. This gives small
accessories more model detail without resampling the protected original image.

For a scene-wide change, choose **Whole image** or disable preservation. Native
reference conditioning guides this edit, but unmentioned details may change.
For transparent output, use the RGBA option and a global/background instruction.
Protected local edits retain the source alpha. Native RGBA references are restored
before the Qwen encoder; the downloaded output keeps any generated alpha channel.

## References, history, and jobs

An optional extra image supplies a clothing/material/style reference while the
first image determines composition. The backend accepts two extra references
(three images total), bounded for this 6 GB GPU. The installed Qwen node supports
more inputs, but longer visual sequences can exceed this machine's memory.

Jobs run serially to avoid competing GPU/model allocations. Reloading the browser
reconnects to the active job. Cancellation targets only the job owned by Studio.
History survives restarts; jobs interrupted by a Studio process restart are marked
failed so they can be rerun. Original sources, masks, results, and namespaced Comfy
inputs are kept locally for history and workflow replay. No history is deleted
automatically. PNGs include the chosen settings and compiled instruction.

Workflow downloads are ComfyUI **API JSON** graphs, importable through its frontend.
They refer to namespaced input files in `ComfyUI/input/studio_uploads/` and require
the installed Qwen assets. Moving just the JSON to another machine does not transfer
the source images or weights. New Studio PNGs embed the native API prompt graph
alongside readable settings so their generation recipe travels with the result.

## Installed masking models

The isolated CPU worker first checks for people, then parses clothing/face/hair.
Inference is bounded to a 1024px longest side; masks return at source resolution.
Review fine boundaries on large images with the brush.
It exits before Qwen starts. It needs about 124 MB of downloaded model weights:

- [Author's SegFormer B2 clothing parser](https://huggingface.co/mattmdjaga/segformer_b2_clothes), pinned revision `584abc1e1d260e23c0fc627c5217a09b2b461046`.
- [Official torchvision SSDLite320 MobileNetV3 person detector](https://docs.pytorch.org/vision/master/models/generated/torchvision.models.detection.ssdlite320_mobilenet_v3_large.html).

Weights are in `models/segformer_b2_clothes/`. To restore only these small weights:
`ComfyUI_windows_portable\python_embeded\python.exe segmentation_worker.py install`.
The large Qwen assets remain in `ComfyUI_windows_portable/ComfyUI/models/`.

## Verification and troubleshooting

Run `.venv\Scripts\python.exe -m unittest test_studio_app test_studio_jobs test_qwen_engine test_editing_regions`
for API, workflow, cancellation, mask, and exact-pixel preservation checks.
`verify_studio_live.py` runs real GPU jobs for all three profiles and two masked
edits; it takes several minutes and writes `outputs/verification_report.json`.

If the backend is offline, start `run_qwen_comfyui.bat` and inspect
`ComfyUI_windows_portable/qwen_comfyui.stderr.log`. If memory runs out, try Fast,
close memory-heavy applications, and use fewer reference images. Segmentation and
generation are serialized. The optional official 9B vision prompt rewriter and
extra upscaler are not installed. Enhance uses the local `qwen21-dolphin` profile
for task-specific text rewriting. See [DOLPHIN_PROMPTING.md](DOLPHIN_PROMPTING.md)
for configuration, limitations, sources and verification.

If needed, `QWEN_ENCODER_DEVICE=cpu` selects the slower CPU encoder for
troubleshooting. Adding `--cpu-vae` to the Comfy launcher selects CPU VAE fallback.
The default streamed CUDA path was verified on this RTX 4050 6 GB card.
