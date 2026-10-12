"""Local face detail enhancement planning and compositing."""
import math
from PIL import Image, ImageDraw, ImageFilter

from editing_regions import composite_preserving

SMALL_FACE_PX = 256
MIN_FACE_PX = 20
CONTEXT = 3.0
WORK_SIZE = 768


def plan(image_size: tuple[int, int], faces: list[dict]) -> dict | None:
    """Return an enhancement plan for small faces, or None if skipped."""
    if len(faces) != 1:
        return None
    face = faces[0]
    w = face.get("width")
    h = face.get("height")
    if w is None or h is None:
        return None
    min_side = min(w, h)
    if min_side >= SMALL_FACE_PX or min_side < MIN_FACE_PX:
        return None

    img_w, img_h = image_size
    box = face.get("box")
    if not box or len(box) != 4:
        return None
    x, y, w_box, h_box = box
    cx = x + w_box / 2.0
    cy = y + h_box / 2.0

    side = max(w_box, h_box) * CONTEXT
    side = max(side, 256.0)
    side = min(side, float(min(img_w, img_h)))
    side = int(round(side))
    # Make side a multiple of 16 for clean tiling and downscaling
    side = max(128, (side // 16) * 16)
    side = min(side, min(img_w, img_h))

    left = int(round(cx - side / 2.0))
    top = int(round(cy - side / 2.0))
    left = max(0, min(img_w - side, left))
    top = max(0, min(img_h - side, top))
    crop_box = (left, top, left + side, top + side)

    # Generate feathered mask at WORK_SIZE
    scale = WORK_SIZE / float(side)
    fcx_work = (cx - left) * scale
    fcy_work = (cy - top) * scale
    rx_work = (w_box / 2.0) * scale * 1.3
    ry_work = (h_box / 2.0) * scale * 1.4

    # Create elliptical mask with soft feathering
    mask = Image.new("L", (WORK_SIZE, WORK_SIZE), 0)
    draw = ImageDraw.Draw(mask)
    draw.ellipse(
        [
            fcx_work - rx_work,
            fcy_work - ry_work,
            fcx_work + rx_work,
            fcy_work + ry_work,
        ],
        fill=255,
    )
    # Apply blur for seamless edge blending
    mask = mask.filter(ImageFilter.GaussianBlur(radius=20))

    return {
        "crop_box": crop_box,
        "crop_size": (side, side),
        "work_size": (WORK_SIZE, WORK_SIZE),
        "face_px": [int(round(w_box)), int(round(h_box))],
        "scale": scale,
        "mask": mask,
    }


def prompt(user_prompt: str, use_bfs_head: bool = False) -> str:
    """Compose the refinement instruction for the face detail inpainting pass."""
    if use_bfs_head:
        return (
            "head_swap: start with <image1> as the base image, keeping its lighting, environment, and background. "
            "remove the head from <image1> completely and replace it with the head from <image2>, strictly preserving "
            "the hair, eye color, nose structure from <image2>. copy the direction of the eye, head rotation, "
            "micro expressions from <image1>, high quality, sharp details, 4k"
        )
    return (
        "Refine the face in <image1> using the identity references in <image2>. "
        "Keep the exact head pose, facial expression, eye gaze, lighting and hairstyle unchanged. "
        "Sharpen natural skin texture, eyes and facial features inside the masked area without altering the surrounding composition."
    )


def composite(original: Image.Image, refined_work: Image.Image, detail_plan: dict) -> Image.Image:
    """Seamlessly composite the sharpened face crop back into the original image."""
    crop_box = detail_plan["crop_box"]
    side = crop_box[2] - crop_box[0]

    # Resize refined crop and mask to crop_box dimensions
    resized_work = refined_work.resize((side, side), Image.Resampling.LANCZOS)
    resized_mask = detail_plan["mask"].resize((side, side), Image.Resampling.BILINEAR)

    # Place resized mask onto full original canvas
    full_mask = Image.new("L", original.size, 0)
    full_mask.paste(resized_mask, (crop_box[0], crop_box[1]))

    return composite_preserving(original, resized_work, full_mask, feather=0, crop_box=crop_box)
