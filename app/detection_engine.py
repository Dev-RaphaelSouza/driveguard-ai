"""Detecção facial e extração de métricas geométricas por quadro.

Este módulo transforma UM quadro em números (``FaceMetrics``). Ele não decide
se o motorista está sonolento: essa decisão depende do tempo e fica no
``drowsiness_analyzer``. Também não identifica pessoas — apenas mede a
geometria de olhos, boca e postura da cabeça.

Métricas
--------
EAR (Eye Aspect Ratio, Soukupová & Čech, 2016)::

    EAR = (|p2 - p6| + |p3 - p5|) / (2 · |p1 - p4|)

    p1 e p4 são os cantos do olho; (p2, p6) e (p3, p5) são pares de pontos das
    pálpebras superior e inferior. O valor cai perto de zero quando o olho fecha
    e é aproximadamente invariante à escala do rosto na imagem.

MAR (Mouth Aspect Ratio): média de três aberturas verticais dos lábios
internos dividida pela largura interna da boca. Valores altos e sustentados
sugerem bocejo.

Pose da cabeça: ângulos de Euler (pitch, yaw, roll) extraídos da matriz de
transformação facial fornecida pelo MediaPipe.
"""

from __future__ import annotations

import hashlib
import logging
from dataclasses import dataclass, field
from pathlib import Path

import cv2
import numpy as np

from config.settings import MODEL_PATH, MODEL_SHA256

log = logging.getLogger(__name__)

# Índices dos landmarks da malha facial do MediaPipe (478 pontos).
# Ordem p1..p6 da fórmula do EAR. "Direito/esquerdo" referem-se ao motorista.
RIGHT_EYE_EAR = (33, 160, 158, 133, 153, 144)
LEFT_EYE_EAR = (362, 385, 387, 263, 373, 380)
MOUTH_VERTICAL_PAIRS = ((82, 87), (13, 14), (312, 317))
MOUTH_CORNERS = (78, 308)

# Contornos usados apenas para desenhar a sobreposição na pré-visualização.
RIGHT_EYE_CONTOUR = (33, 7, 163, 144, 145, 153, 154, 155, 133, 173, 157, 158, 159, 160, 161, 246)
LEFT_EYE_CONTOUR = (362, 382, 381, 380, 374, 373, 390, 249, 263, 466, 388, 387, 386, 385, 384, 398)
INNER_LIPS_CONTOUR = (78, 95, 88, 178, 87, 14, 317, 402, 318, 324, 308, 415, 310, 311, 312, 13, 82, 81, 80, 191)

# Limiares da heurística de qualidade (experimentais).
MIN_FACE_WIDTH = 0.08      # fração da largura do quadro: abaixo disso, rosto distante demais
GOOD_FACE_WIDTH = 0.18
POSE_SOFT_LIMIT = 25.0     # graus de yaw/pitch a partir dos quais o EAR perde confiabilidade
POSE_HARD_LIMIT = 45.0
DARK_LUMA = 35.0           # luminância média do rosto (0–255)
GOOD_LUMA = 70.0
BRIGHT_LUMA = 225.0


class ModelNotFoundError(FileNotFoundError):
    """O arquivo do modelo do Face Landmarker não está disponível."""


@dataclass(frozen=True, slots=True)
class FaceMetrics:
    """Medidas extraídas de um único quadro."""

    timestamp: float
    face_detected: bool
    ear_left: float | None = None
    ear_right: float | None = None
    mar: float | None = None
    pitch: float | None = None
    yaw: float | None = None
    roll: float | None = None
    quality: float = 0.0
    quality_issues: tuple[str, ...] = ()

    @property
    def ear(self) -> float | None:
        if self.ear_left is None or self.ear_right is None:
            return None
        return (self.ear_left + self.ear_right) / 2.0


@dataclass
class Observation:
    """Resultado do processamento de um quadro (métricas + pontos para desenho)."""

    metrics: FaceMetrics
    landmarks_px: np.ndarray | None = field(default=None, repr=False)
    face_box: tuple[int, int, int, int] | None = None  # x0, y0, x1, y1 em pixels


# --------------------------------------------------------------------------- #
# Geometria (funções puras, testadas sem câmera)
# --------------------------------------------------------------------------- #
def eye_aspect_ratio(points: np.ndarray) -> float:
    """Calcula o EAR a partir de seis pontos (p1..p6) em pixels."""
    p1, p2, p3, p4, p5, p6 = np.asarray(points, dtype=np.float64)
    horizontal = np.linalg.norm(p1 - p4)
    if horizontal < 1e-6:
        return 0.0
    vertical = np.linalg.norm(p2 - p6) + np.linalg.norm(p3 - p5)
    return float(vertical / (2.0 * horizontal))


def mouth_aspect_ratio(landmarks_px: np.ndarray) -> float:
    """Calcula o MAR a partir da malha completa em pixels."""
    left, right = MOUTH_CORNERS
    width = np.linalg.norm(landmarks_px[left] - landmarks_px[right])
    if width < 1e-6:
        return 0.0
    openings = [np.linalg.norm(landmarks_px[a] - landmarks_px[b]) for a, b in MOUTH_VERTICAL_PAIRS]
    return float(np.mean(openings) / width)


def rotation_to_euler(rotation: np.ndarray) -> tuple[float, float, float]:
    """Converte uma matriz de rotação 3×3 em (pitch, yaw, roll) em graus.

    Convenção R = Rz(roll) · Ry(yaw) · Rx(pitch). Validado empiricamente:
    girar a imagem no plano altera apenas o roll; espelhá-la inverte o yaw.
    """
    r = np.asarray(rotation, dtype=np.float64)
    sy = float(np.hypot(r[0, 0], r[1, 0]))
    if sy > 1e-6:
        pitch = np.arctan2(r[2, 1], r[2, 2])
        yaw = np.arctan2(-r[2, 0], sy)
        roll = np.arctan2(r[1, 0], r[0, 0])
    else:  # gimbal lock (praticamente impossível para um rosto real)
        pitch = np.arctan2(-r[1, 2], r[1, 1])
        yaw = np.arctan2(-r[2, 0], sy)
        roll = 0.0
    return float(np.degrees(pitch)), float(np.degrees(yaw)), float(np.degrees(roll))


def _ramp(value: float, low: float, high: float) -> float:
    """0 abaixo de ``low``, 1 acima de ``high`` e linear entre eles."""
    if high <= low:
        return 1.0 if value >= high else 0.0
    return float(min(1.0, max(0.0, (value - low) / (high - low))))


def assess_quality(
    face_box_norm: tuple[float, float, float, float],
    face_luma: float,
    yaw: float | None,
    pitch: float | None,
    ear_left: float,
    ear_right: float,
) -> tuple[float, tuple[str, ...]]:
    """Heurística de confiabilidade das métricas (0 = inútil, 1 = boa).

    O MediaPipe não fornece uma confiança por quadro para a malha facial, então
    combinamos fatores que sabidamente degradam o EAR: rosto pequeno, cortado
    pela borda, muito girado, mal iluminado ou com olhos inconsistentes entre si
    (reflexo em óculos, oclusão). A qualidade final é o PIOR fator.
    """
    x0, y0, x1, y1 = face_box_norm
    scores: dict[str, float] = {}

    scores["rosto distante"] = _ramp(x1 - x0, MIN_FACE_WIDTH, GOOD_FACE_WIDTH)

    margin = 0.01
    cut = x0 < margin or y0 < margin or x1 > 1 - margin or y1 > 1 - margin
    scores["rosto cortado pela borda"] = 0.55 if cut else 1.0

    if yaw is not None and pitch is not None:
        worst = max(abs(yaw), abs(pitch))
        scores["cabeça muito girada"] = 1.0 - _ramp(worst, POSE_SOFT_LIMIT, POSE_HARD_LIMIT)

    if face_luma < GOOD_LUMA:
        scores["pouca iluminação"] = _ramp(face_luma, DARK_LUMA, GOOD_LUMA)
    elif face_luma > BRIGHT_LUMA:
        scores["imagem superexposta"] = 1.0 - _ramp(face_luma, BRIGHT_LUMA, 255.0)

    frontal = yaw is None or abs(yaw) < 15
    widest = max(ear_left, ear_right)
    if frontal and widest > 0.12 and abs(ear_left - ear_right) / widest > 0.5:
        scores["olhos inconsistentes (reflexo/oclusão)"] = 0.45

    quality = min(scores.values()) if scores else 0.0
    issues = tuple(name for name, value in scores.items() if value < 0.6)
    return round(quality, 3), issues


# --------------------------------------------------------------------------- #
# Wrapper do MediaPipe
# --------------------------------------------------------------------------- #
def verify_model(path: Path) -> bool:
    """Retorna True se o arquivo do modelo corresponde ao SHA-256 esperado."""
    digest = hashlib.sha256(path.read_bytes()).hexdigest()
    return digest == MODEL_SHA256


class DetectionEngine:
    """Encapsula o MediaPipe Face Landmarker no modo VIDEO (com rastreamento)."""

    def __init__(self, model_path: Path = MODEL_PATH, min_confidence: float = 0.5) -> None:
        model_path = Path(model_path)
        if not model_path.is_file():
            raise ModelNotFoundError(
                f"Modelo não encontrado em '{model_path}'. Execute "
                "'python scripts/download_model.py' para baixá-lo."
            )
        # Importação tardia: o MediaPipe só é carregado quando realmente necessário.
        import mediapipe as mp
        from mediapipe.tasks.python import BaseOptions, vision

        self._mp = mp
        model_bytes = model_path.read_bytes()
        if hashlib.sha256(model_bytes).hexdigest() != MODEL_SHA256:
            log.warning("O modelo em %s não corresponde ao SHA-256 esperado.", model_path)
        options = vision.FaceLandmarkerOptions(
            # Buffer em vez de caminho: evita falhas do C++ com caminhos acentuados.
            base_options=BaseOptions(model_asset_buffer=model_bytes),
            running_mode=vision.RunningMode.VIDEO,
            num_faces=1,
            min_face_detection_confidence=min_confidence,
            min_face_presence_confidence=min_confidence,
            min_tracking_confidence=min_confidence,
            output_face_blendshapes=False,
            output_facial_transformation_matrixes=True,
        )
        self._landmarker = vision.FaceLandmarker.create_from_options(options)
        self._last_ts_ms = -1
        log.info("Face Landmarker inicializado (confiança mínima %.2f)", min_confidence)

    def process(self, frame_bgr: np.ndarray, timestamp: float) -> Observation:
        """Processa um quadro BGR; ``timestamp`` em segundos (relógio monotônico)."""
        height, width = frame_bgr.shape[:2]
        rgb = cv2.cvtColor(frame_bgr, cv2.COLOR_BGR2RGB)
        image = self._mp.Image(image_format=self._mp.ImageFormat.SRGB, data=rgb)

        # O modo VIDEO exige timestamps estritamente crescentes em milissegundos.
        ts_ms = max(int(timestamp * 1000), self._last_ts_ms + 1)
        self._last_ts_ms = ts_ms
        result = self._landmarker.detect_for_video(image, ts_ms)

        if not result.face_landmarks:
            return Observation(FaceMetrics(timestamp=timestamp, face_detected=False))

        landmarks = result.face_landmarks[0]
        norm = np.array([(p.x, p.y) for p in landmarks], dtype=np.float64)
        # Converter para pixels antes das distâncias evita distorção pela razão de aspecto.
        pts = norm * np.array([width, height], dtype=np.float64)

        ear_right = eye_aspect_ratio(pts[list(RIGHT_EYE_EAR)])
        ear_left = eye_aspect_ratio(pts[list(LEFT_EYE_EAR)])
        mar = mouth_aspect_ratio(pts)

        pitch = yaw = roll = None
        if result.facial_transformation_matrixes:
            matrix = np.asarray(result.facial_transformation_matrixes[0])
            pitch, yaw, roll = rotation_to_euler(matrix[:3, :3])

        x0n, y0n = np.clip(norm.min(axis=0), 0.0, 1.0)
        x1n, y1n = np.clip(norm.max(axis=0), 0.0, 1.0)
        box = (int(x0n * width), int(y0n * height), int(x1n * width), int(y1n * height))
        roi = frame_bgr[box[1]:max(box[3], box[1] + 1), box[0]:max(box[2], box[0] + 1)]
        b, g, r, _ = cv2.mean(roi)
        luma = 0.114 * b + 0.587 * g + 0.299 * r

        quality, issues = assess_quality((x0n, y0n, x1n, y1n), luma, yaw, pitch, ear_left, ear_right)
        metrics = FaceMetrics(
            timestamp=timestamp,
            face_detected=True,
            ear_left=ear_left,
            ear_right=ear_right,
            mar=mar,
            pitch=pitch,
            yaw=yaw,
            roll=roll,
            quality=quality,
            quality_issues=issues,
        )
        return Observation(metrics=metrics, landmarks_px=pts.astype(np.float32), face_box=box)

    def close(self) -> None:
        if self._landmarker is not None:
            self._landmarker.close()
            self._landmarker = None
