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


# --- Pillar 2: Expression & Action-Aware Prompting ---

# Maps scene actions to natural facial expression descriptions so the face
# detail inpainting pass generates expressions that match the body's activity
# rather than producing a stiff studio-portrait look.
ACTION_EXPRESSIONS = {
    "running": "determined expression, focused gaze forward, slight exertion",
    "cycling": "focused concentration, wind-swept, forward gaze",
    "surfing": "intense focus, water spray, squinting eyes",
    "skydiving": "exhilarated expression, wind-distorted features, wide eyes",
    "crying": "tears streaming, reddened eyes, emotional distress",
    "laughing": "genuine smile, crow's feet, bright eyes",
    "smiling": "warm genuine smile, relaxed cheeks, bright eyes",
    "reading": "downward gaze, relaxed expression, gentle focus",
    "fighting": "intense glare, clenched jaw, aggressive posture",
    "sleeping": "closed eyes, relaxed face muscles, peaceful",
    "yoga": "serene calm, meditative focus, relaxed features",
    "climbing": "determined upward gaze, gritted teeth, focused effort",
    "swimming": "wet features, focused eyes, exertion",
    "singing": "open mouth expressively, engaged eyes, emotional",
    "dancing": "joyful expression, dynamic energy, bright eyes",
    "meditating": "serene closed eyes, peaceful expression, calm",
    "skateboarding": "focused concentration, determined expression, balance",
    "motorcycle": "focused forward gaze, wind-swept, alert eyes",
}


def _detect_action_expression(user_prompt: str) -> str:
    """Extract action-matching expression from the user prompt."""
    lower = user_prompt.lower()
    for action, expression in ACTION_EXPRESSIONS.items():
        if action in lower:
            return expression
    return ""


# --- Pillar 1: Two-Pass Inpainting Config ---

def two_pass_config(face_px: tuple[int, int] | list[int]) -> dict:
    """Return separate configs for structural and detail inpainting passes.

    Pass 1 (Structure): Higher denoise for reconstructing correct facial
    geometry, pose alignment, and expression. Full LoRA strength.

    Pass 2 (Detail): Low denoise for sharpening micro-features (irises,
    pores, eyelashes) on the now-correct structure. Reduced LoRA strength
    to avoid over-stylizing fine textures.
    """
    min_dim = min(face_px) if face_px else 64

    if min_dim < 64:
        p1_denoise, p2_denoise = 0.62, 0.32
        p1_steps, p2_steps = 18, 12
    elif min_dim < 160:
        p1_denoise, p2_denoise = 0.55, 0.28
        p1_steps, p2_steps = 16, 10
    else:
        p1_denoise, p2_denoise = 0.48, 0.24
        p1_steps, p2_steps = 14, 8

    return {
        "pass_1": {
            "denoise": p1_denoise,
            "steps": p1_steps,
            "lora_strength": 1.0,
            "prompt_mode": "structure",
        },
        "pass_2": {
            "denoise": p2_denoise,
            "steps": p2_steps,
            "lora_strength": 0.6,
            "prompt_mode": "detail",
        },
    }


# --- Pillar 4: Precision Denoise Scheduling ---

def precision_denoise_schedule(face_px: tuple[int, int] | list[int],
                               pass_type: str) -> dict:
    """Return denoise + step configuration for a specific pass type.

    Structure pass: moderate denoise for geometry reconstruction.
    Detail pass: low denoise for identity-safe micro-detailing.
    """
    min_dim = min(face_px) if face_px else 64

    if pass_type == "structure":
        if min_dim < 64:
            return {"denoise": 0.62, "steps": 18, "cfg": 1.0}
        elif min_dim < 120:
            return {"denoise": 0.55, "steps": 16, "cfg": 1.0}
        else:
            return {"denoise": 0.48, "steps": 14, "cfg": 1.0}

    elif pass_type == "detail":
        if min_dim < 64:
            return {"denoise": 0.30, "steps": 12, "cfg": 1.0}
        elif min_dim < 120:
            return {"denoise": 0.26, "steps": 10, "cfg": 1.0}
        else:
            return {"denoise": 0.22, "steps": 8, "cfg": 1.0}

    return {"denoise": 0.50, "steps": 15, "cfg": 1.0}


# --- Pillar 5: Smart Face Restoration Blending ---

def smart_restore(original_crop: Image.Image, refined_crop: Image.Image,
                  face_px: tuple[int, int] | list[int]) -> Image.Image:
    """Apply face restoration only when beneficial, with size-adaptive blending.

    Strategy:
    - Tiny faces (<80px): skip restoration — too small for meaningful benefit
    - Medium faces (80-200px): blend at 40% — subtle application
    - Large faces (>200px): blend at 60% — more texture benefit

    CodeFormer fidelity is set to 0.9 (high identity retention) to avoid
    the FFHQ-averaging effect that hurt benchmark scores at w=0.8.
    """
    import face_restore

    min_dim = min(face_px) if face_px else 64

    if min_dim < 80 or not face_restore.is_available():
        return refined_crop

    restored = face_restore.restore_face(refined_crop, fidelity=0.9)

    # Size-adaptive blending ratio
    blend_alpha = 0.4 if min_dim < 200 else 0.6

    return Image.blend(
        refined_crop.convert("RGB"),
        restored.convert("RGB"),
        blend_alpha,
    )


def prompt(user_prompt: str, use_bfs_head: bool = False,
           action_context: str = "") -> str:
    """Compose the refinement instruction for the face detail inpainting pass.

    Now with expression-aware prompting: detects actions from the user prompt
    and injects matching facial expression guidance so the inpainted face
    matches the scene's activity naturally.
    """
    # Auto-detect expression from user prompt, or use provided context
    expression_guidance = action_context or _detect_action_expression(user_prompt)

    if use_bfs_head:
        base = (
            "head_swap: start with <image1> as the base image, keeping its "
            "lighting, environment, and background. "
            "remove the head from <image1> completely and replace it with "
            "the head from <image2>, strictly preserving "
            "the hair, eye color, nose structure from <image2>."
        )
        if expression_guidance:
            base += (
                f" The face must show {expression_guidance}, matching the "
                f"body's action naturally."
            )
        base += (
            " copy the direction of the eye, head rotation, "
            "micro expressions from <image1>, natural skin texture, high quality, sharp details, 4k"
        )
        return base

    # Standard (non-BFS) path
    base = (
        "Refine the face in <image1> using the identity references in "
        "<image2>. Keep the exact head pose, facial expression, eye gaze, "
        "lighting and hairstyle unchanged."
    )
    if expression_guidance:
        base += f" The expression should show {expression_guidance}."
    base += (
        " Sharpen natural skin texture, eyes and facial features inside "
        "the masked area without altering the surrounding composition."
    )
    return base


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


def should_inpaint(face_px: tuple[int, int] | list[int],
                   current_similarity: float | None = None) -> bool:
    """Adaptive gating: determine if inpainting is required based on size & similarity."""
    min_dim = min(face_px) if face_px else 0
    if min_dim < 20:
        return False
    if min_dim < 120:
        return True
    if current_similarity is not None and current_similarity >= 0.75:
        return False
    return True


def adaptive_codeformer_weight(face_px: tuple[int, int] | list[int]) -> float:
    """Return size-tuned CodeFormer blending weight."""
    min_dim = min(face_px) if face_px else 0
    if min_dim < 64:
        return 0.55
    elif min_dim < 120:
        return 0.30
    return 0.0


def harmonize_lighting(src_img: Image.Image, inp_img: Image.Image) -> Image.Image:
    """Reinhard color / lighting harmonization from source crop to inpainted crop."""
    import numpy as np

    src_arr = np.array(src_img).astype(np.float32)
    inp_arr = np.array(inp_img).astype(np.float32)

    src_mean = src_arr.mean(axis=(0, 1))
    src_std = np.maximum(src_arr.std(axis=(0, 1)), 1e-4)
    inp_mean = inp_arr.mean(axis=(0, 1))
    inp_std = np.maximum(inp_arr.std(axis=(0, 1)), 1e-4)

    harmonized = (inp_arr - inp_mean) / inp_std * src_std + src_mean
    clipped = np.clip(harmonized, 0, 255).astype(np.uint8)
    return Image.fromarray(clipped, mode="RGB")


detail_prompt = prompt


