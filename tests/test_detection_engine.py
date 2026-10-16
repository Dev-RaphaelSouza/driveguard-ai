"""Testes das métricas geométricas, da heurística de qualidade e do detector."""

from __future__ import annotations

import math

import numpy as np
import pytest

from app.detection_engine import (
    MOUTH_CORNERS,
    MOUTH_VERTICAL_PAIRS,
    DetectionEngine,
    FaceMetrics,
    ModelNotFoundError,
    assess_quality,
    eye_aspect_ratio,
    mouth_aspect_ratio,
    rotation_to_euler,
    verify_model,
)
from config.settings import MODEL_PATH

needs_model = pytest.mark.skipif(not MODEL_PATH.is_file(), reason="modelo não baixado")


def eye(opening: float) -> np.ndarray:
    """Olho sintético de largura 10 px com abertura vertical ``opening``."""
    h = opening / 2
    return np.array([(0, 0), (3, -h), (7, -h), (10, 0), (7, h), (3, h)], dtype=float)


def test_ear_open_vs_closed():
    assert eye_aspect_ratio(eye(6.0)) == pytest.approx(0.6)
    assert eye_aspect_ratio(eye(0.5)) == pytest.approx(0.05)
    assert eye_aspect_ratio(eye(6.0)) > eye_aspect_ratio(eye(3.0)) > eye_aspect_ratio(eye(0.5))


def test_ear_is_scale_invariant():
    assert eye_aspect_ratio(eye(4.0) * 7.5) == pytest.approx(eye_aspect_ratio(eye(4.0)))


def test_ear_degenerate_eye_returns_zero():
    assert eye_aspect_ratio(np.zeros((6, 2))) == 0.0


def test_mouth_aspect_ratio():
    pts = np.zeros((478, 2))
    left, right = MOUTH_CORNERS
    pts[left], pts[right] = (0, 0), (40, 0)
    for top, bottom in MOUTH_VERTICAL_PAIRS:
        pts[top], pts[bottom] = (20, -10), (20, 10)
    assert mouth_aspect_ratio(pts) == pytest.approx(0.5)


def _rotation(pitch: float, yaw: float, roll: float) -> np.ndarray:
    p, y, r = map(math.radians, (pitch, yaw, roll))
    rx = np.array([[1, 0, 0], [0, math.cos(p), -math.sin(p)], [0, math.sin(p), math.cos(p)]])
    ry = np.array([[math.cos(y), 0, math.sin(y)], [0, 1, 0], [-math.sin(y), 0, math.cos(y)]])
    rz = np.array([[math.cos(r), -math.sin(r), 0], [math.sin(r), math.cos(r), 0], [0, 0, 1]])
    return rz @ ry @ rx


@pytest.mark.parametrize("angles", [(0, 0, 0), (15, -10, 5), (-30, 20, -12), (5, 40, 25)])
def test_rotation_to_euler_roundtrip(angles):
    assert rotation_to_euler(_rotation(*angles)) == pytest.approx(angles, abs=1e-6)


GOOD_BOX = (0.35, 0.25, 0.65, 0.75)


def test_quality_good_face():
    quality, issues = assess_quality(GOOD_BOX, 120.0, 0.0, 0.0, 0.3, 0.3)
    assert quality == pytest.approx(1.0)
    assert issues == ()


@pytest.mark.parametrize("kwargs,issue", [
    ({"box": (0.48, 0.48, 0.53, 0.55)}, "rosto distante"),
    ({"luma": 25.0}, "pouca iluminação"),
    ({"luma": 250.0}, "imagem superexposta"),
    ({"yaw": 50.0}, "cabeça muito girada"),
    ({"box": (0.0, 0.2, 0.3, 0.7)}, "rosto cortado pela borda"),
    ({"ears": (0.30, 0.08)}, "olhos inconsistentes (reflexo/oclusão)"),
])
def test_quality_flags_problems(kwargs, issue):
    box = kwargs.get("box", GOOD_BOX)
    luma = kwargs.get("luma", 120.0)
    yaw = kwargs.get("yaw", 0.0)
    ear_l, ear_r = kwargs.get("ears", (0.3, 0.3))
    quality, issues = assess_quality(box, luma, yaw, 0.0, ear_l, ear_r)
    assert quality < 0.6
    assert issue in issues


def test_face_metrics_mean_ear():
    m = FaceMetrics(timestamp=0.0, face_detected=True, ear_left=0.2, ear_right=0.3)
    assert m.ear == pytest.approx(0.25)
    assert FaceMetrics(timestamp=0.0, face_detected=False).ear is None


def test_missing_model_raises_helpful_error(tmp_path):
    with pytest.raises(ModelNotFoundError, match="download_model"):
        DetectionEngine(tmp_path / "nao_existe.task")


@needs_model
def test_bundled_model_matches_checksum():
    assert verify_model(MODEL_PATH)


@needs_model
def test_engine_reports_no_face_on_empty_frame():
    engine = DetectionEngine(MODEL_PATH)
    try:
        frame = np.zeros((480, 640, 3), np.uint8)
        obs = engine.process(frame, 1.0)
        # O mesmo timestamp duas vezes não pode quebrar o modo VIDEO do MediaPipe.
        obs2 = engine.process(frame, 1.0)
    finally:
        engine.close()
    assert not obs.metrics.face_detected and not obs2.metrics.face_detected
    assert obs.metrics.ear is None and obs.landmarks_px is None
