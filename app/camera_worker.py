"""Thread de captura e análise.

Toda a visão computacional roda aqui, fora da thread da interface. A
comunicação com a GUI acontece apenas por sinais Qt (enfileirados
automaticamente entre threads) carregando dados imutáveis: ``QImage`` copiada e
``AnalysisResult`` congelado. Nenhum widget é tocado nesta thread.

Ciclo de vida: ``start()`` → laço em ``run()`` → ``request_stop()`` + ``wait()``.
Como o laço é bloqueante e não precisa de event loop, subclassificar QThread é
mais simples e seguro do que ``moveToThread`` (evita o deadlock de esperar um
``quit()`` enfileirado na thread que está bloqueada em ``wait()``).
"""

from __future__ import annotations

import copy
import logging
import threading
import time
from collections.abc import Callable

import cv2
import numpy as np
from PySide6.QtCore import QThread, Signal
from PySide6.QtGui import QImage

from app.detection_engine import (
    INNER_LIPS_CONTOUR,
    LEFT_EYE_CONTOUR,
    RIGHT_EYE_CONTOUR,
    DetectionEngine,
    ModelNotFoundError,
    Observation,
)
from app.drowsiness_analyzer import AnalysisResult, DrowsinessAnalyzer
from app.frame_source import FrameSource
from config.settings import MODEL_PATH, AppSettings

log = logging.getLogger(__name__)

FRAME_TIMEOUT_SECONDS = 2.5      # sem quadros por mais que isso = câmera caiu
MAX_CONSECUTIVE_ERRORS = 30      # erros seguidos de processamento antes de desistir
MAX_FRAMES_IN_FLIGHT = 2         # quadros emitidos ainda não exibidos pela GUI

# Cores (BGR) da sobreposição — mesma paleta da interface.
COLOR_OK = (94, 197, 34)
COLOR_DANGER = (68, 68, 239)
COLOR_WARN = (36, 165, 245)
COLOR_ACCENT = (191, 212, 45)
COLOR_MUTED = (160, 140, 120)


class CameraStatus:
    OPENING = "opening"
    RUNNING = "running"
    ERROR = "error"
    STOPPED = "stopped"


class FrameBudget:
    """Limita quadros pendentes na fila da GUI (evita acúmulo de memória)."""

    def __init__(self, limit: int = MAX_FRAMES_IN_FLIGHT) -> None:
        self._limit = limit
        self._count = 0
        self._lock = threading.Lock()

    def try_acquire(self) -> bool:
        with self._lock:
            if self._count >= self._limit:
                return False
            self._count += 1
            return True

    def release(self) -> None:
        with self._lock:
            self._count = max(0, self._count - 1)


EngineFactory = Callable[[AppSettings], DetectionEngine]
SourceFactory = Callable[[AppSettings], FrameSource]


def default_engine_factory(settings: AppSettings) -> DetectionEngine:
    return DetectionEngine(MODEL_PATH, settings.detection.min_detection_confidence)


class CameraWorker(QThread):
    frame_ready = Signal(QImage)
    analysis_ready = Signal(object)        # AnalysisResult
    status_changed = Signal(str, str)      # CameraStatus, mensagem
    fatal_error = Signal(str)
    stats = Signal(float, float)           # FPS analisado, ms por quadro

    def __init__(self, source_factory: SourceFactory, settings: AppSettings,
                 engine_factory: EngineFactory | None = None, parent=None) -> None:
        super().__init__(parent)
        self.setObjectName("CameraWorker")
        self._source_factory = source_factory
        self._engine_factory = engine_factory or default_engine_factory
        self._settings = copy.deepcopy(settings)
        self._pending: AppSettings | None = None
        self._recalibrate = False
        self._lock = threading.Lock()
        self._stop_event = threading.Event()
        self.frame_budget = FrameBudget()

    # ------------------------------------------------------------------ #
    # API thread-safe (chamada a partir da GUI)
    # ------------------------------------------------------------------ #
    def request_stop(self) -> None:
        self._stop_event.set()

    def update_settings(self, settings: AppSettings) -> None:
        with self._lock:
            self._pending = copy.deepcopy(settings)

    def request_recalibration(self) -> None:
        with self._lock:
            self._recalibrate = True

    # ------------------------------------------------------------------ #
    # Laço principal (thread de captura)
    # ------------------------------------------------------------------ #
    def run(self) -> None:  # noqa: D401 - executado pela QThread
        self._engine: DetectionEngine | None = None
        source = None
        try:
            try:
                self._engine = self._engine_factory(self._settings)
            except ModelNotFoundError as exc:
                self.fatal_error.emit(str(exc))
                return
            except Exception as exc:  # noqa: BLE001
                log.exception("Falha ao iniciar o detector")
                self.fatal_error.emit(f"Falha ao inicializar o detector facial: {exc}")
                return
            source = self._source_factory(self._settings)
            self._loop(source)
        except Exception as exc:  # noqa: BLE001 - nunca derrubar a aplicação
            log.exception("Erro inesperado na thread de captura")
            self.fatal_error.emit(f"Erro inesperado na captura: {exc}")
        finally:
            if source is not None:
                source.close()
            if self._engine is not None:
                self._engine.close()
                self._engine = None
            self.status_changed.emit(CameraStatus.STOPPED, "")

    def _loop(self, source: FrameSource) -> None:
        settings = self._settings
        analyzer = DrowsinessAnalyzer(settings.detection)
        interval = 1.0 / settings.camera.process_fps
        next_due = 0.0
        last_frame_at = time.monotonic()
        errors = 0
        window_start, window_frames, window_busy = time.monotonic(), 0, 0.0

        while not self._stop_event.is_set():
            new_settings, recalibrate = self._take_pending()
            if recalibrate:
                analyzer.recalibrate()
            if new_settings is not None:
                self._apply_settings(new_settings, analyzer)
                settings = self._settings
                interval = 1.0 / settings.camera.process_fps

            if not source.is_open():
                self.status_changed.emit(CameraStatus.OPENING, f"Abrindo {source.description}…")
                if not source.open():
                    self.status_changed.emit(CameraStatus.ERROR, source.last_error)
                    self._stop_event.wait(settings.camera.reconnect_seconds)
                    continue
                analyzer.notify_interruption()
                self.status_changed.emit(CameraStatus.RUNNING, source.description)
                last_frame_at = time.monotonic()

            frame = source.read()
            now = time.monotonic()
            if frame is None:
                if now - last_frame_at > FRAME_TIMEOUT_SECONDS:
                    source.close()
                    message = (source.last_error or "A câmera parou de enviar imagens.") + \
                        " Tentando reconectar…"
                    self.status_changed.emit(CameraStatus.ERROR, message)
                    self._stop_event.wait(settings.camera.reconnect_seconds)
                else:
                    self._stop_event.wait(0.01)
                continue
            last_frame_at = now

            # Limita a taxa de análise; quadros excedentes são descartados para
            # manter a latência baixa (tolerância de 4 ms para jitter da câmera).
            if now < next_due - 0.004:
                continue
            next_due = max(next_due + interval, now - interval)

            started = time.perf_counter()
            try:
                observation = self._engine.process(frame, now)
                result = analyzer.update(observation.metrics)
            except Exception as exc:  # noqa: BLE001
                errors += 1
                log.warning("Erro ao processar quadro (%s/%s): %s", errors, MAX_CONSECUTIVE_ERRORS, exc)
                if errors >= MAX_CONSECUTIVE_ERRORS:
                    self.fatal_error.emit(f"Falhas repetidas ao analisar o vídeo: {exc}")
                    break
                continue
            errors = 0
            self.analysis_ready.emit(result)

            if settings.privacy.show_preview and self.frame_budget.try_acquire():
                self.frame_ready.emit(self._render(frame, observation, result, settings))

            window_frames += 1
            window_busy += time.perf_counter() - started
            elapsed = now - window_start
            if elapsed >= 1.0:
                self.stats.emit(window_frames / elapsed, 1000.0 * window_busy / max(window_frames, 1))
                window_start, window_frames, window_busy = now, 0, 0.0

    def _take_pending(self) -> tuple[AppSettings | None, bool]:
        with self._lock:
            pending, self._pending = self._pending, None
            recalibrate, self._recalibrate = self._recalibrate, False
        return pending, recalibrate

    def _apply_settings(self, new: AppSettings, analyzer: DrowsinessAnalyzer) -> None:
        old_confidence = self._settings.detection.min_detection_confidence
        self._settings = new
        analyzer.update_settings(new.detection)
        if new.detection.min_detection_confidence != old_confidence:
            try:
                replacement = self._engine_factory(new)
            except Exception as exc:  # noqa: BLE001 - mantém o detector anterior
                log.error("Não foi possível recriar o detector: %s", exc)
            else:
                self._engine.close()
                self._engine = replacement

    # ------------------------------------------------------------------ #
    # Desenho da pré-visualização
    # ------------------------------------------------------------------ #
    @staticmethod
    def _render(frame: np.ndarray, obs: Observation, result: AnalysisResult,
                settings: AppSettings) -> QImage:
        if settings.privacy.draw_landmarks and obs.landmarks_px is not None:
            draw_overlay(frame, obs, result)
        if settings.camera.mirror_preview:
            frame = cv2.flip(frame, 1)
        frame = np.ascontiguousarray(frame)
        h, w = frame.shape[:2]
        image = QImage(frame.data, w, h, frame.strides[0], QImage.Format.Format_BGR888)
        return image.copy()  # copia: o buffer NumPy deixa de existir após o retorno


def draw_overlay(frame: np.ndarray, obs: Observation, result: AnalysisResult) -> None:
    """Desenha contornos dos olhos, boca e cantoneiras do rosto (in-place)."""
    pts = obs.landmarks_px
    width = frame.shape[1]
    thick = max(1, round(width / 480))
    eye_color = COLOR_DANGER if result.eyes_closed else COLOR_OK
    for contour in (RIGHT_EYE_CONTOUR, LEFT_EYE_CONTOUR):
        poly = pts[list(contour)].astype(np.int32)
        cv2.polylines(frame, [poly], True, eye_color, thick, cv2.LINE_AA)
    mouth = pts[list(INNER_LIPS_CONTOUR)].astype(np.int32)
    cv2.polylines(frame, [mouth], True, COLOR_WARN if result.yawning else COLOR_MUTED, thick, cv2.LINE_AA)

    if obs.face_box is not None:
        x0, y0, x1, y1 = obs.face_box
        pad = int(0.08 * (x1 - x0))
        x0, y0, x1, y1 = x0 - pad, y0 - pad, x1 + pad, y1 + pad
        arm = max(8, int(0.16 * (x1 - x0)))
        for cx, cy, dx, dy in ((x0, y0, 1, 1), (x1, y0, -1, 1), (x0, y1, 1, -1), (x1, y1, -1, -1)):
            cv2.line(frame, (cx, cy), (cx + dx * arm, cy), COLOR_ACCENT, thick + 1, cv2.LINE_AA)
            cv2.line(frame, (cx, cy), (cx, cy + dy * arm), COLOR_ACCENT, thick + 1, cv2.LINE_AA)
