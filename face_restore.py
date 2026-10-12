"""Neural Face Restoration via CodeFormer ONNX.

Restores high-frequency facial micro-biometrics (irises, eyelashes, skin texture)
and eliminates AI diffusion smoothing artifacts. Runs on CPU with minimal memory.
"""
from pathlib import Path
import threading
from typing import Optional

import numpy as np
from PIL import Image

ROOT = Path(__file__).resolve().parent
MODEL_PATH = ROOT / "models" / "face_restore" / "codeformer.onnx"

_SESSION: Optional[object] = None
_SESSION_LOCK = threading.Lock()


def is_available() -> bool:
    """Return True if CodeFormer model file exists and onnxruntime is installed."""
    if not MODEL_PATH.is_file():
        return False
    try:
        import onnxruntime as _ort  # noqa: F401
        return True
    except ImportError:
        return False


def _get_session():
    """Lazily initialize and cache the ONNX inference session."""
    global _SESSION
    if _SESSION is not None:
        return _SESSION
    with _SESSION_LOCK:
        if _SESSION is not None:
            return _SESSION
        import onnxruntime as ort

        opts = ort.SessionOptions()
        opts.intra_op_num_threads = 4
        opts.graph_optimization_level = ort.GraphOptimizationLevel.ORT_ENABLE_ALL
        _SESSION = ort.InferenceSession(
            str(MODEL_PATH),
            sess_options=opts,
            providers=["CPUExecutionProvider"],
        )
        return _SESSION


def restore_face(image: Image.Image, fidelity: float = 0.8) -> Image.Image:
    """Restore high-precision facial micro-features on a face crop.

    Args:
        image: PIL Image (RGB) containing the face crop.
        fidelity: Float in [0.0, 1.0]. Higher values preserve more of the original
                  reference identity, while lower values apply stronger restoration.

    Returns:
        Restored PIL Image (RGB) matching the original input dimensions.
    """
    if not is_available():
        return image

    original_size = image.size
    fidelity = max(0.0, min(1.0, float(fidelity)))

    # CodeFormer accepts 512x512 input
    resized = image.resize((512, 512), Image.Resampling.LANCZOS).convert("RGB")
    np_img = np.array(resized, dtype=np.float32)

    # Normalize to [-1.0, 1.0] in RGB order
    norm_img = (np_img / 255.0 - 0.5) / 0.5
    # Shape: (1, 3, 512, 512)
    tensor_in = np.transpose(norm_img, (2, 0, 1))[np.newaxis, :, :, :]

    sess = _get_session()
    w_scalar = np.array(fidelity, dtype=np.float64)

    outputs = sess.run(None, {"x": tensor_in, "w": w_scalar})
    tensor_out = outputs[0]  # shape (1, 3, 512, 512)

    # De-normalize back to [0, 255] uint8
    restored_np = tensor_out[0].transpose(1, 2, 0)
    restored_np = np.clip((restored_np * 0.5 + 0.5) * 255.0, 0, 255).astype(np.uint8)

    restored_img = Image.fromarray(restored_np, mode="RGB")
    if restored_img.size != original_size:
        restored_img = restored_img.resize(original_size, Image.Resampling.LANCZOS)

    return restored_img
