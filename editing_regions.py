"""CPU semantic edit masks and pixel-preserving original-resolution compositing.

The optional segmenter runs in a short-lived child process so its model is never
resident alongside Qwen. Automatic masks are proposals, not identity guarantees.
White mask pixels are editable; black pixels always retain the exact source data.
"""

from __future__ import annotations

from dataclasses import dataclass, field
import json
import os
from pathlib import Path
import re
import subprocess
import tempfile

import numpy as np
from PIL import Image, ImageChops, ImageDraw, ImageFilter, ImageOps

ROOT = Path(__file__).resolve().parent
MODEL_DIR = ROOT / "models" / "segformer_b2_clothes"
MODEL_ID = "mattmdjaga/segformer_b2_clothes"
DETECTOR_FILE = "ssdlite320_mobilenet_v3_large_coco-a79551df.pth"
TARGET_LABELS = {
    "outfit": (4, 5, 6, 7, 8, 17),
    "upper_clothes": (4, 7, 17),
    "lower_clothes": (5, 6),
    "shoes": (9, 10),
    "sunglasses": (3,),
    "hair": (2,),
    "background": (0,),
}
TARGET_KEYWORDS = {
    "outfit": r"outfit|clothes|clothing|suit|tuxedo|dress|costume|uniform",
    "upper_clothes": r"shirt|t-shirt|tee|top|jacket|blazer|coat|hoodie|sweater|blouse",
    "lower_clothes": r"pants|trousers|jeans|shorts|skirt",
    "shoes": r"shoes?|sneakers?|boots?|sandals?|loafers?|heels|footwear",
    "sunglasses": r"sunglasses|glasses|shades|spectacles|eyewear",
    "necklace": r"necklace|pendant|neck chain",
    "hair": r"hair|hairstyle|haircut|curls|ponytail|bangs",
    "background": r"background|backdrop",
}
SUPPORTED_TARGETS = tuple(TARGET_KEYWORDS)
SEGMENTATION_MAX_SIDE = 1024


class MaskError(ValueError):
    """An edit region cannot be selected safely; the caller should offer a brush."""


@dataclass
class MaskProposal:
    mask: Image.Image
    target: str
    confidence: float
    method: str
    warnings: list[str] = field(default_factory=list)
    requires_review: bool = True

    @property
    def bbox(self):
        return self.mask.getbbox()

    def metadata(self) -> dict:
        return {
            "target": self.target,
            "confidence": round(float(self.confidence), 3),
            "method": self.method,
            "warnings": self.warnings,
            "requires_review": self.requires_review,
            "bbox": list(self.bbox) if self.bbox else None,
            "coverage": round(float(np.count_nonzero(np.asarray(self.mask))) / (self.mask.width * self.mask.height), 4),
            "confidence_note": "Mean segmentation probability, not a calibrated accuracy score.",
        }


def detect_edit_target(instruction: str, target: str = "auto") -> str:
    """Conservative local instruction parser; explicit UI selection takes priority."""
    target = {"top": "upper_clothes", "eyewear": "sunglasses", "clothing": "outfit", "bottom": "lower_clothes"}.get(target, target)
    if target != "auto":
        if target not in SUPPORTED_TARGETS:
            raise MaskError("Unknown edit target. Choose a supported area or paint a mask.")
        return target
    text = instruction.lower().strip()
    # Ignore preservation clauses, including 'keep background and change clothes'.
    chunks = re.split(r"[,.;\n]|\bbut\b|\band (?=(?:change|replace|add|remove|recolor|make|turn|keep|preserve|maintain|leave)\b)", text)
    edits = []
    for chunk in chunks:
        if re.match(r"\s*(?:keep|keeping|preserve|preserving|maintain|leave|do not|don't)\b", chunk):
            continue
        # A preservation suffix does not describe another modification target.
        chunk = re.split(r"\b(?:while|without|keep|keeping|preserve|preserving|maintain|leave)\b", chunk, maxsplit=1)[0]
        edits.append(chunk)
    text = " ".join(edits)
    matches = {name for name, pattern in TARGET_KEYWORDS.items() if re.search(r"\b(?:" + pattern + r")\b", text)}
    if "outfit" in matches:
        matches.difference_update({"upper_clothes", "lower_clothes"})
    if matches == {"upper_clothes", "lower_clothes"}:
        matches = {"outfit"}
    if not matches:
        raise MaskError("The instruction has no supported automatic edit area. Choose an area or paint a mask.")
    if len(matches) != 1:
        raise MaskError("The instruction changes multiple areas. Choose one area, paint a combined mask, or use a whole-image edit.")
    return next(iter(matches))


def normalize_manual_mask(mask: Image.Image, original_size: tuple[int, int]) -> Image.Image:
    """Normalize an opaque black/white bitmap or transparent brush overlay."""
    if max(original_size) > 8192 or min(original_size) < 1:
        raise MaskError("Mask canvas dimensions are invalid or exceed 8192 pixels.")
    if mask.mode == "RGBA" and mask.getextrema()[3][0] < 255:
        mask = mask.getchannel("A")
    else:
        mask = mask.convert("L")
    mask = mask.resize(original_size, Image.Resampling.NEAREST).point(lambda value: 255 if value >= 128 else 0)
    if not mask.getbbox():
        raise MaskError("The edit mask is empty. Paint the area you want to change.")
    return mask


def inner_feather(mask: Image.Image, radius: int = 6) -> Image.Image:
    """Soften inside the chosen region only; never blur into protected pixels."""
    binary = mask.convert("L").point(lambda value: 255 if value >= 128 else 0)
    radius = max(0, min(64, int(radius)))
    if not radius:
        return binary
    # A blurred alpha clipped by binary support has zero exterior values even at
    # canvas edges. Thin accessories remain visible rather than being eroded away.
    padded = ImageOps.expand(binary, border=radius * 3, fill=0)
    softened = padded.filter(ImageFilter.GaussianBlur(radius / 2)).crop((radius * 3, radius * 3, radius * 3 + binary.width, radius * 3 + binary.height))
    return ImageChops.multiply(softened, binary)


def composite_preserving(original: Image.Image, generated: Image.Image, mask: Image.Image, feather: int = 6, crop_box: tuple[int, int, int, int] | None = None) -> Image.Image:
    """Paste generated content only inside the binary mask, retaining source alpha.

    A crop result is resized to its explicit source crop. Whole-frame results are
    resized to source dimensions. Protected RGB/RGBA pixels remain bit-exact.
    """
    if original.mode not in ("RGB", "RGBA", "L"):
        original = original.convert("RGBA" if "transparency" in original.info else "RGB")
    mask = normalize_manual_mask(mask, original.size)
    alpha = inner_feather(mask, feather)
    result = original.copy()
    if crop_box is None:
        generated = generated.resize(original.size, Image.Resampling.LANCZOS)
        box = (0, 0, original.width, original.height)
    else:
        box = tuple(int(value) for value in crop_box)
        x0, y0, x1, y1 = box
        if not (0 <= x0 < x1 <= original.width and 0 <= y0 < y1 <= original.height):
            raise MaskError("Crop bounds do not lie inside the original image.")
        generated = generated.resize((x1 - x0, y1 - y0), Image.Resampling.LANCZOS)
    if original.mode == "RGBA":
        generated = generated.convert("RGBA")
        # Source transparency is part of the protected original canvas, including
        # selected pixels. Qwen RGB results cannot recreate alpha faithfully.
        generated.putalpha(original.getchannel("A").crop(box))
    else:
        generated = generated.convert(original.mode)
    result.paste(generated, box[:2], alpha.crop(box))
    return result


def crop_bounds(mask: Image.Image, context: float = .35, min_size: int = 256) -> tuple[int, int, int, int]:
    bbox = mask.convert("L").getbbox()
    if not bbox:
        raise MaskError("Cannot crop an empty edit mask.")
    x0, y0, x1, y1 = bbox
    width = max(min_size, int((x1 - x0) * (1 + 2 * context)))
    height = max(min_size, int((y1 - y0) * (1 + 2 * context)))
    # Give accessories room for face/anatomy context, using a roughly square crop.
    side = max(width, height)
    width, height = min(mask.width, side), min(mask.height, side)
    left = max(0, min(mask.width - width, int((x0 + x1 - width) / 2)))
    top = max(0, min(mask.height - height, int((y0 + y1 - height) / 2)))
    return left, top, left + width, top + height


def preview_overlay(source: Image.Image, mask: Image.Image) -> Image.Image:
    source = source.convert("RGBA")
    overlay = Image.new("RGBA", source.size, (38, 221, 164, 0))
    overlay.putalpha(mask.convert("L").point(lambda value: 95 if value >= 128 else 0))
    return Image.alpha_composite(source, overlay)


def segmenter_status() -> dict:
    ready = all((MODEL_DIR / name).exists() for name in ("config.json", "preprocessor_config.json", "model.safetensors", DETECTOR_FILE))
    ready = ready and (MODEL_DIR / "model.safetensors").stat().st_size == 109493236
    return {"installed": ready, "model": MODEL_ID, "person_detector": "torchvision SSDLite320 MobileNetV3 COCO", "device": "cpu", "targets": list(SUPPORTED_TARGETS), "estimated_regions": ["necklace", "sunglasses when absent"], "inference_max_side": SEGMENTATION_MAX_SIDE, "model_directory": str(MODEL_DIR)}


def prepare_segmentation_input(source: Image.Image, max_side: int = SEGMENTATION_MAX_SIDE) -> Image.Image:
    """Bound CPU logits memory before converting/compositing high-resolution input.

    Parsing operates on a <=1024px canvas; masks are subsequently mapped to the
    source with nearest-neighbor interpolation. Fine boundaries require review.
    """
    if not 128 <= int(max_side) <= 1536:
        raise MaskError("Segmentation inference size must be between 128 and 1536 pixels.")
    scale = min(1.0, int(max_side) / max(source.size))
    size = (max(1, round(source.width * scale)), max(1, round(source.height * scale)))
    # Resize before RGBA conversion so a 24MP source never creates several full-size
    # RGB/RGBA intermediates solely for segmentation.
    bounded = source.resize(size, Image.Resampling.LANCZOS) if size != source.size else source.copy()
    rgba = bounded.convert("RGBA")
    rgb = Image.new("RGB", size, "white")
    rgb.paste(rgba.convert("RGB"), mask=rgba.getchannel("A"))
    return rgb


def _parse_source(source: Image.Image) -> tuple[np.ndarray, np.ndarray, dict]:
    if not segmenter_status()["installed"]:
        raise MaskError("Automatic masking model is not installed. Paint a mask or install the local segmenter.")
    interpreter = os.environ.get("QWEN_MASK_PYTHON", str(ROOT / "ComfyUI_windows_portable" / "python_embeded" / "python.exe"))
    if not Path(interpreter).exists():
        raise MaskError("The local segmentation Python runtime is unavailable. Paint a mask instead.")
    with tempfile.TemporaryDirectory(prefix="qwen-mask-") as temporary:
        folder = Path(temporary)
        # White alpha composite is only the segmenter's input; source is retained.
        rgb = prepare_segmentation_input(source)
        rgb.save(folder / "source.png")
        flags = subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0
        try:
            child = subprocess.run([interpreter, str(ROOT / "segmentation_worker.py"), "infer", str(folder / "source.png"), str(folder)], capture_output=True, text=True, timeout=250, creationflags=flags, env={**os.environ, "CUDA_VISIBLE_DEVICES": "", "HF_HUB_OFFLINE": "1"})
        except subprocess.TimeoutExpired as exc:
            raise MaskError("Automatic masking timed out. Paint the edit area instead.") from exc
        if child.returncode:
            raise MaskError("Automatic masking failed. Paint a mask instead. " + child.stderr[-350:])
        with Image.open(folder / "labels.png") as image:
            labels = np.asarray(image.resize(source.size, Image.Resampling.NEAREST)).copy()
        with Image.open(folder / "confidence.png") as image:
            confidence = np.asarray(image.resize(source.size, Image.Resampling.BILINEAR), dtype=np.float32) / 255
        return labels, confidence, json.loads((folder / "result.json").read_text(encoding="utf-8"))


def propose_mask(source: Image.Image, instruction: str, target: str = "auto", *, grow: int = 3) -> MaskProposal:
    target = detect_edit_target(instruction, target)
    labels, scores, info = _parse_source(source)
    return proposal_from_labels(source.size, labels, scores, target, info=info, grow=grow)


def proposal_from_labels(size: tuple[int, int], labels: np.ndarray, scores: np.ndarray, target: str, *, info: dict | None = None, grow: int = 3) -> MaskProposal:
    """Convert semantic predictions into a reviewable edit mask (testable in isolation)."""
    if labels.shape != (size[1], size[0]) or scores.shape != labels.shape:
        raise MaskError("Segmentation dimensions do not match the source.")
    if target not in SUPPORTED_TARGETS:
        raise MaskError("Unknown automatic edit target.")
    info = info or {}
    if "person_count" in info and not info["person_count"]:
        raise MaskError("No person was detected confidently. Paint a mask or use a whole-image edit.")
    if int(info.get("person_count", 1)) > 1:
        raise MaskError("More than one person was detected. Paint a mask to choose the person.")
    if int(info.get("face_count", 1)) > 1:
        raise MaskError("More than one person was detected. Paint a mask to choose the person.")
    foreground = (labels != 0) & (scores >= .5)
    if foreground.mean() < .005 or not np.any(np.isin(labels, [4, 5, 6, 7, 11])):
        raise MaskError("No confident person was found. Paint a mask or use a whole-image edit.")
    warnings = ["Review the highlighted area before editing; semantic segmentation can miss fine boundaries."]
    if target in ("hair", "outfit", "upper_clothes", "lower_clothes", "shoes"):
        warnings.append("For a larger hairstyle or garment silhouette, expand the highlighted area with the brush; only selected pixels can change.")
    method = "segformer_semantic"
    if target == "necklace" or (target == "sunglasses" and not np.any((labels == 3) & (scores >= .5))):
        face = Image.fromarray(((labels == 11) & (scores >= .6)).astype(np.uint8) * 255)
        bbox = face.getbbox()
        if bbox is None:
            raise MaskError("A clear face is required to estimate this accessory area. Paint a mask.")
        left, top, right, bottom = bbox
        fw, fh = right - left, bottom - top
        if fw < 10 or fh < 12:
            raise MaskError("The face is too small for an accessory mask. Paint a mask or use a closer photo.")
        binary = Image.new("L", size, 0)
        drawer = ImageDraw.Draw(binary)
        if target == "sunglasses":
            area = (left - .08 * fw, top + .24 * fh, right + .08 * fw, top + .61 * fh)
            drawer.ellipse(tuple(int(value) for value in area), fill=255)
        else:
            area = (left - .1 * fw, top + .84 * fh, right + .1 * fw, bottom + .65 * fh)
            drawer.ellipse(tuple(int(value) for value in area), fill=255)
        method = "estimated_face_anchor"
        confidence = min(.65, float(scores[labels == 11].mean()))
        warnings.append("This is an estimated placement area derived from the face, not a segmented accessory. Adjust it with the brush before generating.")
    else:
        selected = np.isin(labels, TARGET_LABELS[target]) & (scores >= .5)
        if target == "background":
            # The human boundary is retained, with a small protective margin.
            person = Image.fromarray((labels != 0).astype(np.uint8) * 255).filter(ImageFilter.MaxFilter(7))
            selected &= np.asarray(person) == 0
        if np.count_nonzero(selected) < max(12, int(labels.size * .00015)):
            raise MaskError("The requested area was not detected with enough confidence. Paint a mask.")
        confidence = float(scores[selected].mean())
        if confidence < .58:
            raise MaskError("The automatic mask has low confidence. Paint or refine the edit area.")
        binary = Image.fromarray(selected.astype(np.uint8) * 255)
        grow = max(0, min(20, int(grow)))
        if grow and target != "background":
            binary = binary.filter(ImageFilter.MaxFilter(grow * 2 + 1))
        # Protect detected exposed skin and other unrequested semantic categories.
        protected = np.zeros(labels.shape, dtype=bool)
        if target in ("outfit", "upper_clothes", "lower_clothes", "shoes"):
            protected = np.isin(labels, [1, 2, 3, 9, 10, 11, 12, 13, 14, 15, 16])
            if target == "shoes":
                protected &= ~np.isin(labels, [9, 10])
        elif target == "hair":
            protected = np.isin(labels, [3, 11])
        pixels = np.asarray(binary).copy()
        pixels[protected] = 0
        binary = Image.fromarray(pixels)
    if not binary.getbbox():
        raise MaskError("The automatic mask is empty after protecting the person. Paint a mask.")
    return MaskProposal(binary, target, confidence, method, warnings)
