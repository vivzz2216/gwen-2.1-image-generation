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

    mask = Image.new("L", (WORK_SIZE, WORK_SIZE), 0)
    draw = ImageDraw.Draw(mask)

    landmarks = face.get("landmarks")
    landmark_aligned = False
    if landmarks and len(landmarks) == 10:
        try:
            # 5 landmarks: right_eye, left_eye, nose, right_mouth, left_mouth
            r_eye = ((landmarks[0] - left) * scale, (landmarks[1] - top) * scale)
            l_eye = ((landmarks[2] - left) * scale, (landmarks[3] - top) * scale)
            nose = ((landmarks[4] - left) * scale, (landmarks[5] - top) * scale)
            r_mouth = ((landmarks[6] - left) * scale, (landmarks[7] - top) * scale)
            l_mouth = ((landmarks[8] - left) * scale, (landmarks[9] - top) * scale)

            eye_mid = ((r_eye[0] + l_eye[0]) / 2.0, (r_eye[1] + l_eye[1]) / 2.0)
            mouth_mid = ((r_mouth[0] + l_mouth[0]) / 2.0, (r_mouth[1] + l_mouth[1]) / 2.0)

            dx_side = l_eye[0] - r_eye[0]
            dy_side = l_eye[1] - r_eye[1]
            roll_angle = math.degrees(math.atan2(dy_side, dx_side))

            # Build anatomical facial hull points
            dx_down = mouth_mid[0] - eye_mid[0]
            dy_down = mouth_mid[1] - eye_mid[1]

            chin = (mouth_mid[0] + 0.65 * dx_down, mouth_mid[1] + 0.65 * dy_down)
            forehead_mid = (eye_mid[0] - 0.75 * dx_down, eye_mid[1] - 0.75 * dy_down)
            l_forehead = (forehead_mid[0] + 0.45 * dx_side, forehead_mid[1] + 0.45 * dy_side)
            r_forehead = (forehead_mid[0] - 0.45 * dx_side, forehead_mid[1] - 0.45 * dy_side)
            l_temple = (l_eye[0] + 0.4 * dx_side, l_eye[1] + 0.4 * dy_side)
            r_temple = (r_eye[0] - 0.4 * dx_side, r_eye[1] - 0.4 * dy_side)
            l_jaw = (l_mouth[0] + 0.35 * dx_side + 0.25 * dx_down, l_mouth[1] + 0.35 * dy_side + 0.25 * dy_down)
            r_jaw = (r_mouth[0] - 0.35 * dx_side + 0.25 * dx_down, r_mouth[1] - 0.35 * dy_side + 0.25 * dy_down)

            polygon_pts = [r_forehead, forehead_mid, l_forehead, l_temple, l_jaw, chin, r_jaw, r_temple]
            draw.polygon(polygon_pts, fill=255)

            # Also draw roll-aligned ellipse to ensure smooth oval coverage
            ellipse_layer = Image.new("L", (WORK_SIZE, WORK_SIZE), 0)
            e_draw = ImageDraw.Draw(ellipse_layer)
            e_draw.ellipse(
                [
                    fcx_work - rx_work * 0.9,
                    fcy_work - ry_work * 0.95,
                    fcx_work + rx_work * 0.9,
                    fcy_work + ry_work * 0.95,
                ],
                fill=255,
            )
            ellipse_rotated = ellipse_layer.rotate(-roll_angle, resample=Image.Resampling.BILINEAR, center=(fcx_work, fcy_work))
            mask = Image.new("L", (WORK_SIZE, WORK_SIZE), 0)
            from PIL import ImageChops
            draw_poly_layer = Image.new("L", (WORK_SIZE, WORK_SIZE), 0)
            ImageDraw.Draw(draw_poly_layer).polygon(polygon_pts, fill=255)
            mask = ImageChops.lighter(draw_poly_layer, ellipse_rotated)
            landmark_aligned = True
        except Exception:
            landmark_aligned = False

    if not landmark_aligned:
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
        "landmark_aligned": landmark_aligned,
        "work_face_center": (fcx_work, fcy_work),
    }

def adaptive_denoise(face_px: tuple[int, int] | list[int]) -> float:
    """Return optimal inpainting denoise strength based on detected face size.

    Micro faces (<64px) need slightly higher regeneration leeway (0.66) to reconstruct structure.
    Medium faces (64-160px) balance identity and pose adaptation (0.62).
    Larger faces (>160px) need lower denoise (0.52) to avoid hallucinating novel features.
    """
    min_dim = min(face_px) if face_px else 64
    if min_dim < 64:
        return 0.66
    elif min_dim < 160:
        return 0.62
    else:
        return 0.52


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
