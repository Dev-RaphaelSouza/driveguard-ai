"""Gerador de sequências sintéticas de métricas faciais para os testes.

Permite descrever cenários como "10 s de olhos abertos, 2 s fechados" e
reproduzi-los em qualquer FPS, sem câmera nem MediaPipe.
"""

from __future__ import annotations

import time
from collections.abc import Iterable, Iterator

import numpy as np

from app.detection_engine import FaceMetrics, Observation
from app.drowsiness_analyzer import AnalysisResult, DriverState, DrowsinessAnalyzer

OPEN_EAR = 0.30
CLOSED_EAR = 0.08

Segment = tuple[float, dict]


def metrics(t: float, ear: float = OPEN_EAR, mar: float = 0.05, pitch: float = 0.0,
            yaw: float = 0.0, roll: float = 0.0, quality: float = 0.95,
            face: bool = True) -> FaceMetrics:
    if not face:
        return FaceMetrics(timestamp=t, face_detected=False)
    issues = ("pouca iluminação",) if quality < 0.6 else ()
    return FaceMetrics(timestamp=t, face_detected=True, ear_left=ear, ear_right=ear, mar=mar,
                       pitch=pitch, yaw=yaw, roll=roll, quality=quality, quality_issues=issues)


def stream(segments: Iterable[Segment], fps: float = 30.0, start: float = 0.0) -> Iterator[FaceMetrics]:
    t = start
    step = 1.0 / fps
    for duration, params in segments:
        for _ in range(int(round(duration * fps))):
            yield metrics(t, **params)
            t += step


def open_eyes(duration: float, **kw) -> Segment:
    return (duration, {"ear": OPEN_EAR, **kw})


def closed_eyes(duration: float, **kw) -> Segment:
    return (duration, {"ear": CLOSED_EAR, **kw})


def no_face(duration: float) -> Segment:
    return (duration, {"face": False})


def blinking(duration: float, period: float = 4.0, blink: float = 0.15, **kw) -> list[Segment]:
    """Olhos abertos com piscadas curtas periódicas."""
    segments: list[Segment] = []
    elapsed = 0.0
    while elapsed + period <= duration + 1e-9:
        segments.append(open_eyes(period - blink, **kw))
        segments.append(closed_eyes(blink, **kw))
        elapsed += period
    if duration - elapsed > 1e-9:
        segments.append(open_eyes(duration - elapsed, **kw))
    return segments


def run(analyzer: DrowsinessAnalyzer, segments: Iterable[Segment], fps: float = 30.0,
        start: float = 0.0) -> list[AnalysisResult]:
    return [analyzer.update(m) for m in stream(segments, fps=fps, start=start)]


def result(state: DriverState, t: float, unreliable: float = 0.0, reasons=()) -> AnalysisResult:
    """Cria um AnalysisResult mínimo com o estado desejado (testes de alerta)."""
    return AnalysisResult(
        timestamp=t, state=state, reasons=tuple(reasons), events=(), face_reliable=state != DriverState.NO_FACE,
        ear=0.3, ear_threshold=0.18, eyes_closed=False, closure_duration=0.0, perclos=None,
        blink_rate=None, long_blinks=0, yawns=0, mar=0.05, yawning=False, head_deviation=None,
        pitch=0.0, yaw=0.0, roll=0.0, quality=0.9, quality_issues=(), unreliable_duration=unreliable,
        recovering_for=None, calibrating=False, calibration_progress=1.0, calibrated=True,
        baseline_ear=0.3,
    )


class FakePlayer:
    """Player que apenas registra os sons solicitados."""

    def __init__(self, available: bool = True) -> None:
        self.available = available
        self.played: list[tuple] = []
        self.stopped = 0

    def play(self, kind, volume) -> bool:
        if not self.available:
            return False
        self.played.append((kind, volume))
        return True

    def stop(self) -> None:
        self.stopped += 1


class FakeClock:
    def __init__(self, now: float = 0.0) -> None:
        self.now = now

    def __call__(self) -> float:
        return self.now


class FakeSource:
    def __init__(self, fail_open=False, frames_before_disconnect=None, fps=60.0):
        self.fail_open = fail_open
        self.frames_left = frames_before_disconnect
        self.interval = 1.0 / fps
        self.opened = 0
        self.closed = 0
        self._open = False

    description = "Fonte falsa"

    @property
    def last_error(self):
        return "Câmera falsa indisponível" if self.fail_open else "Sem quadros"

    def open(self):
        self.opened += 1
        if self.fail_open:
            return False
        self._open = True
        return True

    def read(self):
        time.sleep(self.interval)
        if self.frames_left is not None:
            if self.frames_left <= 0:
                return None
            self.frames_left -= 1
        return np.zeros((48, 64, 3), np.uint8)

    def close(self):
        self.closed += 1
        self._open = False

    def is_open(self):
        return self._open


class FakeEngine:
    """Olhos abertos, exceto entre ``closed_from`` e ``closed_to`` (segundos)."""

    def __init__(self, closed_from=None, closed_to=None):
        self.t0 = None
        self.closed_from, self.closed_to = closed_from, closed_to
        self.closed = False

    def process(self, frame, timestamp):
        self.t0 = timestamp if self.t0 is None else self.t0
        elapsed = timestamp - self.t0
        closed = self.closed_from is not None and self.closed_from <= elapsed < self.closed_to
        return Observation(metrics(timestamp, ear=0.08 if closed else 0.30))

    def close(self):
        self.closed = True
