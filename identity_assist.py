"""Local CPU face selection and advisory likeness checks; no identity database."""
import hashlib
import math
from functools import lru_cache
from pathlib import Path
import threading

import numpy as np
from PIL import Image

ROOT = Path(__file__).resolve().parent
MODEL_DIR = ROOT / 'models' / 'identity_assist'
DETECTOR = MODEL_DIR / 'face_detection_yunet_2023mar.onnx'
RECOGNIZER = MODEL_DIR / 'face_recognition_sface_2021dec.onnx'
HASHES = {'face_detection_yunet_2023mar.onnx': '8f2383e4dd3cfbb4553ea8718107fc0423210dc964f9f4280604804ed2552fa4',
          'face_recognition_sface_2021dec.onnx': '0ba9fbfa01b5270c96627c4ef784da859931e02f04419c829e83484087c34e79'}
CPU_LOCK = threading.Lock()

def capabilities():
    try:
        import cv2
        installed = hasattr(cv2, 'FaceDetectorYN')
    except ImportError:
        installed = False
    return {'native_reference': True, 'cpu_detection': installed and DETECTOR.is_file(),
            'cpu_similarity': installed and RECOGNIZER.is_file(), 'manual_crop': True,
            'max_identity_people': 1, 'exact_likeness_guaranteed': False,
            'scope': 'Local selected-face reference, not identification of a person. Similarity is advisory.'}

@lru_cache(maxsize=4)
def _verified(path, size, modified):
    if hashlib.sha256(Path(path).read_bytes()).hexdigest() != HASHES[Path(path).name]:
        raise ValueError('Face model checksum mismatch. Reinstall the official OpenCV model.')
    return path

def _model(path):
    if not path.is_file():
        raise RuntimeError('Local face model is unavailable. Use a manual head crop or install Identity Assist models.')
    stat = path.stat()
    return _verified(str(path), stat.st_size, stat.st_mtime_ns)

def validate_box(box, size):
    if not isinstance(box, (list, tuple)) or len(box) != 4:
        raise ValueError('Select a head crop: x, y, width and height as fractions of the photograph.')
    if any(isinstance(n, bool) or not isinstance(n, (int, float)) or not math.isfinite(n) for n in box):
        raise ValueError('Head crop coordinates must be finite numbers.')
    x, y, width, height = map(float, box)
    if x < 0 or y < 0 or width <= 0 or height <= 0 or x + width > 1.000001 or y + height > 1.000001:
        raise ValueError('The entire head crop must be inside the photograph.')
    pixel_box = (round(x*size[0]), round(y*size[1]), min(size[0], round((x+width)*size[0])), min(size[1], round((y+height)*size[1])))
    if pixel_box[2]-pixel_box[0] < 64 or pixel_box[3]-pixel_box[1] < 64:
        raise ValueError('Head crop is smaller than 64 pixels. Use a clearer/closer photograph or expand the crop.')
    return pixel_box

def crop_head(image, box):
    return image.crop(validate_box(box, image.size)).convert('RGB')

def _detect(image):
    import cv2
    cv2.setNumThreads(2)
    original = np.asarray(image.convert('RGB'))[:, :, ::-1].copy()
    ratio = min(1.0, 1536/max(image.size))
    frame = cv2.resize(original, (round(image.width*ratio), round(image.height*ratio))) if ratio < 1 else original
    detector = cv2.FaceDetectorYN.create(_model(DETECTOR), '', (frame.shape[1], frame.shape[0]), .85, .3, 500,
                                        cv2.dnn.DNN_BACKEND_OPENCV, cv2.dnn.DNN_TARGET_CPU)
    _, faces = detector.detect(frame)
    if faces is None:
        return original, []
    faces = sorted(faces, key=lambda face: -float(face[-1]))[:16]
    scaled = []
    for face in faces:
        face = face.copy()
        face[:-1] /= ratio
        scaled.append(face)
    return original, scaled

def detect(image):
    with CPU_LOCK:
        _, faces = _detect(image)
    candidates = []
    for index, face in enumerate(faces):
        x, y, width, height = map(float, face[:4])
        left, top = max(0, x-.45*width), max(0, y-.70*height)
        right, bottom = min(image.width, x+1.45*width), min(image.height, y+1.5*height)
        candidates.append({'index': index, 'confidence': round(float(face[-1]), 3),
            'face_box': [x/image.width, y/image.height, width/image.width, height/image.height],
            'head_box': [left/image.width, top/image.height, (right-left)/image.width, (bottom-top)/image.height],
            'face_pixels': [round(width), round(height)]})
    return {'dimensions': list(image.size), 'candidates': candidates, 'requires_selection': len(candidates) != 1,
            'warnings': ['Small faces carry little detail; use a closer photograph.'] if any(min(c['face_pixels']) < 64 for c in candidates) else [],
            'message': 'Select and review the intended head crop.' if candidates else 'No clear face detected. Adjust a manual crop or use a sharper photograph.'}

def similarity(reference_head, result):
    """Score all detected output faces; never label a biometric match as a pass."""
    if not capabilities()['cpu_similarity']:
        return {'status': 'unavailable', 'advisory': True, 'reason': 'CPU similarity model unavailable.'}
    with CPU_LOCK:
        import cv2
        ref_frame, ref_faces = _detect(reference_head)
        out_frame, out_faces = _detect(result)
        if len(ref_faces) != 1:
            return {'status': 'not_measured', 'advisory': True, 'reason': 'Reference crop must contain one clearly detected face.'}
        if not out_faces:
            return {'status': 'not_measured', 'advisory': True, 'reason': 'No clear output face; angle, size or occlusion may prevent scoring.'}
        recognizer = cv2.FaceRecognizerSF.create(_model(RECOGNIZER), '', cv2.dnn.DNN_BACKEND_OPENCV, cv2.dnn.DNN_TARGET_CPU)
        features = recognizer.feature(recognizer.alignCrop(ref_frame, ref_faces[0]))
        scores = []
        for index, face in enumerate(out_faces):
            output_features = recognizer.feature(recognizer.alignCrop(out_frame, face))
            score = recognizer.match(features, output_features, cv2.FaceRecognizerSF_FR_COSINE)
            scores.append({'face_index': index, 'cosine_similarity': round(float(score), 4),
                'box': [round(float(n), 1) for n in face[:4]]})
        return {'status': 'measured', 'advisory': True, 'faces': scores,
                'note': 'Not proof of identity or edit correctness. Compare the face visually; no automatic pass or retry.'}

def compile_identity(prompt, *, task, pose_enabled=False, pose_order='same'):
    roles = ('Create a new scene using the person in <image1>. This photograph supplies identity and unrequested appearance, not a fixed pose, background or camera framing. Replace the original composition with the requested action, framing and environment; do not preserve the original scene when a new one is requested. '
             if task == 'generate' else 'Edit the scene in <image1> according to the instruction. ')
    roles += ('<image2> is the reviewed identity head crop: use this selected person for the subject described by the instruction. '
              'Retain their recognizable facial identity; refer to the image rather than inventing facial features. '
              'Change expression, head angle and body pose when requested; identity preservation must not freeze those attributes. '
              'Do not reproduce the crop as an inset or create a duplicate person. ')
    if pose_enabled:
        roles += ('<image3> supplies only body pose, contact, subject arrangement and occlusion, never identity, outfit or background. '
                  'Keep the requested actor, recipient, acting limb and target body part. Do not draw skeletons or annotations. ')
        if pose_order == 'swap':
            roles += 'Exchange the two pose-reference subject assignments as selected; use the instruction to identify the identity-reference subject. '
    if task == 'edit':
        roles += 'Keep unrequested scene details and appearance unchanged, except for changes needed by the requested movement. '
    return roles + 'User instruction: ' + prompt.strip()
