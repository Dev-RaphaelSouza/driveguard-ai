"""Analisador temporal de sinais de sonolência.

Recebe uma sequência de ``FaceMetrics`` (um por quadro) e decide o estado do
motorista. Nenhuma decisão é tomada com base em um único quadro: todos os
sinais dependem de durações ou janelas temporais medidas por timestamps (e não
por contagem de quadros), de modo que o comportamento independe do FPS.

Sinais avaliados
----------------
* Fechamento ocular prolongado — olhos fechados continuamente por
  ``long_closure_seconds``. Diferencia piscadas (≈0,1–0,4 s) de microcochilos.
* PERCLOS — proporção do tempo com olhos fechados em uma janela deslizante.
* Piscadas longas — fechamentos entre ``long_blink_seconds`` e o prolongado.
* Bocejos — abertura da boca (MAR) sustentada.
* Inclinação da cabeça — desvio de pitch/roll em relação à postura calibrada.

Robustez
--------
* Calibração: o limiar de olho fechado é uma fração do EAR de olhos abertos
  medido para o próprio usuário (com valor manual enquanto não calibra).
* Qualidade: quadros com rosto ausente ou de baixa qualidade não alimentam as
  métricas; falhas curtas são toleradas e falhas longas reiniciam os
  rastreadores em andamento.
* Histerese: limiares de entrada e saída diferentes para olhos, PERCLOS,
  bocejo e cabeça.
* Confirmação e recuperação: um estado só sobe após o sinal persistir por
  ``confirmation_seconds`` e só desce após ``recovery_seconds`` sem sinais.

Os resultados indicam *possíveis* sinais de sonolência; não são diagnóstico.
"""

from __future__ import annotations

from collections import deque
from dataclasses import dataclass
from enum import Enum

import numpy as np

from app.detection_engine import FaceMetrics
from config.settings import DetectionSettings

MAX_SAMPLE_WEIGHT = 0.25   # s; limita o peso de um quadro após lacunas
CALIBRATION_MAX_YAW = 20.0  # graus; amostras de calibração exigem rosto frontal
BASELINE_EAR_RANGE = (0.15, 0.50)
THRESHOLD_EXIT_RATIO = 0.85  # histerese de PERCLOS, bocejo e cabeça


class DriverState(Enum):
    STARTING = "STARTING"
    NORMAL = "NORMAL"
    ATTENTION = "ATTENTION"
    DROWSY = "DROWSY"
    NO_FACE = "NO_FACE"
    CAMERA_UNAVAILABLE = "CAMERA_UNAVAILABLE"

    @property
    def label(self) -> str:
        return STATE_LABELS[self]

    @property
    def description(self) -> str:
        return STATE_DESCRIPTIONS[self]


STATE_LABELS = {
    DriverState.STARTING: "INICIANDO",
    DriverState.NORMAL: "NORMAL",
    DriverState.ATTENTION: "ATENÇÃO",
    DriverState.DROWSY: "SONOLÊNCIA PROVÁVEL",
    DriverState.NO_FACE: "ROSTO NÃO DETECTADO",
    DriverState.CAMERA_UNAVAILABLE: "CÂMERA INDISPONÍVEL",
}

STATE_DESCRIPTIONS = {
    DriverState.STARTING: "Inicializando câmera e detector…",
    DriverState.NORMAL: "Nenhum sinal persistente de sonolência identificado.",
    DriverState.ATTENTION: "Há sinais que merecem acompanhamento.",
    DriverState.DROWSY: "Os critérios configurados de sonolência foram atingidos.",
    DriverState.NO_FACE: "Monitoramento prejudicado: sem dados faciais confiáveis.",
    DriverState.CAMERA_UNAVAILABLE: "Monitoramento interrompido: falha na captura de vídeo.",
}

_RANK = {DriverState.NORMAL: 0, DriverState.ATTENTION: 1, DriverState.DROWSY: 2}
_BY_RANK = {v: k for k, v in _RANK.items()}


class SignalType(Enum):
    LONG_CLOSURE = "LONG_CLOSURE"
    HIGH_PERCLOS = "HIGH_PERCLOS"
    ELEVATED_PERCLOS = "ELEVATED_PERCLOS"
    LONG_BLINKS = "LONG_BLINKS"
    YAWNS = "YAWNS"
    HEAD_TILT = "HEAD_TILT"
    COMBINED = "COMBINED"
    FACE_UNAVAILABLE = "FACE_UNAVAILABLE"


@dataclass(frozen=True)
class Reason:
    signal: SignalType
    severity: DriverState
    text: str


class EventKind(Enum):
    BLINK = "BLINK"
    LONG_BLINK = "LONG_BLINK"
    LONG_CLOSURE = "LONG_CLOSURE"
    YAWN = "YAWN"
    FACE_LOST = "FACE_LOST"
    FACE_RECOVERED = "FACE_RECOVERED"
    CALIBRATED = "CALIBRATED"
    CALIBRATION_FAILED = "CALIBRATION_FAILED"


@dataclass(frozen=True)
class AnalyzerEvent:
    kind: EventKind
    timestamp: float
    duration: float | None = None
    detail: str = ""


@dataclass(frozen=True)
class AnalysisResult:
    """Retrato imutável da análise após um quadro (seguro entre threads)."""

    timestamp: float
    state: DriverState
    reasons: tuple[Reason, ...]
    events: tuple[AnalyzerEvent, ...]
    face_reliable: bool
    ear: float | None
    ear_threshold: float
    eyes_closed: bool | None
    closure_duration: float
    perclos: float | None
    blink_rate: float | None
    long_blinks: int
    yawns: int
    mar: float | None
    yawning: bool
    head_deviation: float | None
    pitch: float | None
    yaw: float | None
    roll: float | None
    quality: float
    quality_issues: tuple[str, ...]
    unreliable_duration: float
    recovering_for: float | None
    calibrating: bool
    calibration_progress: float
    calibrated: bool
    baseline_ear: float

    @property
    def summary(self) -> str:
        """Texto curto com o motivo principal, para alertas e histórico."""
        if self.reasons:
            return "; ".join(r.text for r in self.reasons)
        return self.state.description

    def metrics_snapshot(self) -> dict[str, float | int | None]:
        """Métricas numéricas relevantes para registrar em um evento."""
        def r(value: float | None, digits: int = 3) -> float | None:
            return None if value is None else round(float(value), digits)

        return {
            "ear": r(self.ear),
            "limiar_ear": r(self.ear_threshold),
            "ear_referencia": r(self.baseline_ear),
            "fechamento_s": r(self.closure_duration, 2),
            "perclos": r(self.perclos),
            "piscadas_min": r(self.blink_rate, 1),
            "piscadas_longas": self.long_blinks,
            "bocejos": self.yawns,
            "mar": r(self.mar),
            "desvio_cabeca_graus": r(self.head_deviation, 1),
            "qualidade": r(self.quality, 2),
        }


def _fmt_s(seconds: float) -> str:
    return f"{seconds:.1f}".replace(".", ",") + " s"


def _hysteresis_level(value: float, thresholds: tuple[float, ...], previous: int) -> int:
    """Nível 0..N com limiar de saída menor que o de entrada."""
    level = 0
    for i, threshold in enumerate(thresholds, start=1):
        if value >= threshold or (previous >= i and value >= threshold * THRESHOLD_EXIT_RATIO):
            level = i
    return level


class DrowsinessAnalyzer:
    """Máquina de estados temporal. Não é thread-safe: use em uma única thread."""

    def __init__(self, settings: DetectionSettings) -> None:
        self._s = settings
        self.reset()

    # ------------------------------------------------------------------ #
    # Controle
    # ------------------------------------------------------------------ #
    def reset(self) -> None:
        """Reinicia tudo, inclusive a calibração (nova sessão)."""
        self._level = DriverState.NORMAL
        self._has_reliable = False
        self._last_reliable_ts: float | None = None
        self._unreliable_since: float | None = None
        self._face_lost = False
        self._perclos_samples: deque[tuple[float, float, bool]] = deque()
        self._perclos_total = 0.0
        self._perclos_closed = 0.0
        self._perclos_level = 0
        self._blink_times: deque[float] = deque()
        self._long_blink_times: deque[float] = deque()
        self._yawn_times: deque[float] = deque()
        self._reset_tracking()
        self.recalibrate()

    def recalibrate(self) -> None:
        """Descarta a referência atual e inicia nova calibração."""
        self._baseline_ear = self._s.manual_baseline_ear
        self._baseline_pose: tuple[float, float] | None = None
        self._calibrated = False
        self._calibrating = True
        self._calib_ear: list[float] = []
        self._calib_pitch: list[float] = []
        self._calib_roll: list[float] = []
        self._calib_time = 0.0

    def update_settings(self, settings: DetectionSettings) -> None:
        """Aplica novos parâmetros sem perder o histórico observado."""
        auto_changed = settings.auto_calibration != self._s.auto_calibration
        self._s = settings
        if not self._calibrated or not settings.auto_calibration or auto_changed:
            self._baseline_ear = settings.manual_baseline_ear
        if auto_changed and settings.auto_calibration:
            self.recalibrate()

    def notify_interruption(self) -> None:
        """A captura foi interrompida (ex.: câmera reconectada).

        Sem isso, um fechamento de olhos iniciado antes da queda seria medido
        como se tivesse durado toda a interrupção. Mantém calibração e janelas.
        """
        self._reset_tracking()
        self._level = DriverState.NORMAL
        self._has_reliable = False
        self._unreliable_since = None
        self._face_lost = False
        self._last_reliable_ts = None

    def _reset_tracking(self) -> None:
        """Zera rastreadores em andamento (ex.: após perda do rosto)."""
        self._eyes_closed = False
        self._closure_start: float | None = None
        self._yawn_start: float | None = None
        self._tilt_since: float | None = None
        self._attention_since: float | None = None
        self._drowsy_since: float | None = None
        self._recovery_since: float | None = None
        self._level_reasons: tuple[Reason, ...] = ()

    @property
    def ear_threshold(self) -> float:
        return self._baseline_ear * self._s.closed_ratio()

    # ------------------------------------------------------------------ #
    # Atualização por quadro
    # ------------------------------------------------------------------ #
    def update(self, m: FaceMetrics) -> AnalysisResult:
        now = m.timestamp
        events: list[AnalyzerEvent] = []
        reliable = (
            m.face_detected and m.ear is not None and m.quality >= self._s.min_quality
        )

        if reliable:
            self._on_reliable(m, now, events)
        else:
            self._on_unreliable(now, events)

        self._prune(now)
        perclos, coverage = self._perclos()
        blink_rate = self._blink_rate()
        long_blinks = len(self._long_blink_times)
        yawns = len(self._yawn_times)
        head_dev = self._head_deviation(m) if reliable else None
        closure = (now - self._closure_start) if (self._eyes_closed and self._closure_start is not None) else 0.0
        yawning = self._yawn_start is not None and now - self._yawn_start >= self._s.yawn_min_seconds

        reasons: tuple[Reason, ...] = ()
        if self._face_lost:
            state = DriverState.NO_FACE
            reasons = (self._no_face_reason(m),)
        elif not self._has_reliable:
            state = DriverState.STARTING
        elif reliable:
            drowsy, attention, instant = self._evaluate_signals(
                now, closure, perclos, long_blinks, yawns
            )
            state = self._resolve_level(now, drowsy, attention, instant)
            reasons = drowsy + attention
            if state != DriverState.NORMAL and not reasons:
                reasons = self._level_reasons
        else:
            # Falha curta (dentro da tolerância): mantém o estado já confirmado.
            state = self._level
            reasons = self._level_reasons if state != DriverState.NORMAL else ()

        recovering = None
        if self._recovery_since is not None and not self._face_lost:
            recovering = now - self._recovery_since

        return AnalysisResult(
            timestamp=now,
            state=state,
            reasons=reasons,
            events=tuple(events),
            face_reliable=reliable,
            ear=m.ear,
            ear_threshold=self.ear_threshold,
            eyes_closed=self._eyes_closed if reliable else None,
            closure_duration=closure,
            perclos=perclos if coverage else None,
            blink_rate=blink_rate,
            long_blinks=long_blinks,
            yawns=yawns,
            mar=m.mar,
            yawning=yawning,
            head_deviation=head_dev,
            pitch=m.pitch,
            yaw=m.yaw,
            roll=m.roll,
            quality=m.quality if m.face_detected else 0.0,
            quality_issues=m.quality_issues,
            unreliable_duration=(now - self._unreliable_since) if self._unreliable_since is not None else 0.0,
            recovering_for=recovering,
            calibrating=self._calibrating,
            calibration_progress=min(1.0, self._calib_time / self._s.calibration_seconds),
            calibrated=self._calibrated,
            baseline_ear=self._baseline_ear,
        )

    # ------------------------------------------------------------------ #
    # Etapas internas
    # ------------------------------------------------------------------ #
    def _on_unreliable(self, now: float, events: list[AnalyzerEvent]) -> None:
        if self._unreliable_since is None:
            self._unreliable_since = now
        lost_for = now - self._unreliable_since
        if not self._face_lost and lost_for >= self._s.face_lost_grace_seconds:
            self._face_lost = True
            events.append(AnalyzerEvent(EventKind.FACE_LOST, now))
            # Sem dados confiáveis, fechamentos/bocejos em andamento perdem sentido.
            self._reset_tracking()
            self._level = DriverState.NORMAL

    def _on_reliable(self, m: FaceMetrics, now: float, events: list[AnalyzerEvent]) -> None:
        if self._unreliable_since is not None:
            if self._face_lost:
                events.append(AnalyzerEvent(EventKind.FACE_RECOVERED, now, now - self._unreliable_since))
            self._unreliable_since = None
            self._face_lost = False
        self._has_reliable = True

        weight = 0.0
        if self._last_reliable_ts is not None:
            weight = min(max(now - self._last_reliable_ts, 0.0), MAX_SAMPLE_WEIGHT)
        self._last_reliable_ts = now

        ear = float(m.ear)  # reliable garante EAR presente
        self._update_eyes(ear, now, events)
        self._update_calibration(m, ear, weight, now, events)
        self._update_yawn(m.mar, now, events)
        self._update_head(m, now)

        self._perclos_samples.append((now, weight, self._eyes_closed))
        self._perclos_total += weight
        if self._eyes_closed:
            self._perclos_closed += weight

    def _update_eyes(self, ear: float, now: float, events: list[AnalyzerEvent]) -> None:
        threshold = self.ear_threshold
        if self._eyes_closed:
            if ear > threshold * (1.0 + self._s.eye_hysteresis):
                start = self._closure_start if self._closure_start is not None else now
                self._end_closure(now - start, now, events)
                self._eyes_closed = False
                self._closure_start = None
        elif ear < threshold:
            self._eyes_closed = True
            self._closure_start = now

    def _end_closure(self, duration: float, now: float, events: list[AnalyzerEvent]) -> None:
        if duration >= self._s.long_closure_seconds:
            events.append(AnalyzerEvent(EventKind.LONG_CLOSURE, now, duration))
        elif duration >= self._s.long_blink_seconds:
            events.append(AnalyzerEvent(EventKind.LONG_BLINK, now, duration))
        else:
            events.append(AnalyzerEvent(EventKind.BLINK, now, duration))
        if duration >= self._s.long_blink_seconds:
            self._long_blink_times.append(now)
        self._blink_times.append(now)

    def _update_calibration(self, m: FaceMetrics, ear: float, weight: float, now: float,
                            events: list[AnalyzerEvent]) -> None:
        if not self._calibrating:
            return
        if m.yaw is not None and abs(m.yaw) > CALIBRATION_MAX_YAW:
            return
        if self._eyes_closed:
            return  # piscadas não entram na referência de olhos abertos
        self._calib_ear.append(ear)
        if m.pitch is not None and m.roll is not None:
            self._calib_pitch.append(m.pitch)
            self._calib_roll.append(m.roll)
        self._calib_time += weight
        if self._calib_time < self._s.calibration_seconds or len(self._calib_ear) < 10:
            return

        self._calibrating = False
        if self._calib_pitch:
            self._baseline_pose = (float(np.median(self._calib_pitch)), float(np.median(self._calib_roll)))
        if not self._s.auto_calibration:
            self._calibrated = False
            return
        baseline = float(np.percentile(self._calib_ear, 70))
        low, high = BASELINE_EAR_RANGE
        if low <= baseline <= high:
            self._baseline_ear = baseline
            self._calibrated = True
            events.append(AnalyzerEvent(EventKind.CALIBRATED, now,
                                        detail=f"EAR de referência {baseline:.3f}".replace(".", ",")))
        else:
            events.append(AnalyzerEvent(
                EventKind.CALIBRATION_FAILED, now,
                detail=f"EAR medido {baseline:.3f} fora da faixa plausível; usando valor manual".replace(".", ","),
            ))

    def _update_yawn(self, mar: float | None, now: float, events: list[AnalyzerEvent]) -> None:
        if mar is None:
            return
        threshold = self._s.yawn_mar_threshold
        if self._yawn_start is None:
            if mar > threshold:
                self._yawn_start = now
        elif mar < threshold * THRESHOLD_EXIT_RATIO:
            duration = now - self._yawn_start
            if duration >= self._s.yawn_min_seconds:
                self._yawn_times.append(now)
                events.append(AnalyzerEvent(EventKind.YAWN, now, duration))
            self._yawn_start = None

    def _head_deviation(self, m: FaceMetrics) -> float | None:
        if self._baseline_pose is None or m.pitch is None or m.roll is None:
            return None
        base_pitch, base_roll = self._baseline_pose
        return max(abs(m.pitch - base_pitch), abs(m.roll - base_roll))

    def _update_head(self, m: FaceMetrics, now: float) -> None:
        deviation = self._head_deviation(m)
        if deviation is None:
            self._tilt_since = None
            return
        limit = self._s.head_tilt_degrees
        if deviation >= limit:
            if self._tilt_since is None:
                self._tilt_since = now
        elif deviation < limit * THRESHOLD_EXIT_RATIO:
            self._tilt_since = None

    def _prune(self, now: float) -> None:
        window = self._s.perclos_window_seconds
        while self._perclos_samples and self._perclos_samples[0][0] < now - window:
            _, weight, closed = self._perclos_samples.popleft()
            self._perclos_total -= weight
            if closed:
                self._perclos_closed -= weight
        if not self._perclos_samples:
            self._perclos_total = self._perclos_closed = 0.0
        for times in (self._blink_times, self._long_blink_times):
            while times and times[0] < now - window:
                times.popleft()
        while self._yawn_times and self._yawn_times[0] < now - self._s.yawn_window_seconds:
            self._yawn_times.popleft()

    def _perclos(self) -> tuple[float | None, bool]:
        """Retorna (PERCLOS, cobertura suficiente)."""
        if self._perclos_total <= 0:
            return None, False
        value = max(0.0, min(1.0, self._perclos_closed / self._perclos_total))
        coverage = self._perclos_total >= 0.5 * self._s.perclos_window_seconds
        return value, coverage

    def _blink_rate(self) -> float | None:
        """Piscadas por minuto de observação confiável (exige ≥15 s)."""
        if self._perclos_total < 15.0:
            return None
        return len(self._blink_times) * 60.0 / self._perclos_total

    def _evaluate_signals(self, now: float, closure: float, perclos: float | None,
                          long_blinks: int, yawns: int
                          ) -> tuple[tuple[Reason, ...], tuple[Reason, ...], bool]:
        s = self._s
        drowsy: list[Reason] = []
        attention: list[Reason] = []
        instant = False

        if closure >= s.long_closure_seconds:
            instant = True
            drowsy.append(Reason(SignalType.LONG_CLOSURE, DriverState.DROWSY,
                                 f"Olhos fechados há {_fmt_s(closure)}"))

        _, coverage = self._perclos()
        if perclos is not None and coverage:
            self._perclos_level = _hysteresis_level(
                perclos, (s.perclos_attention, s.perclos_drowsy), self._perclos_level)
        else:
            self._perclos_level = 0
        if self._perclos_level == 2:
            drowsy.append(Reason(SignalType.HIGH_PERCLOS, DriverState.DROWSY,
                                 f"Olhos fechados {perclos:.0%} do tempo recente (PERCLOS)"))
        elif self._perclos_level == 1:
            attention.append(Reason(SignalType.ELEVATED_PERCLOS, DriverState.ATTENTION,
                                    f"PERCLOS elevado ({perclos:.0%})"))

        if long_blinks >= s.long_blinks_attention_count:
            attention.append(Reason(SignalType.LONG_BLINKS, DriverState.ATTENTION,
                                    f"{long_blinks} piscadas longas recentes"))
        if yawns >= s.yawns_attention_count:
            attention.append(Reason(SignalType.YAWNS, DriverState.ATTENTION,
                                    f"{yawns} bocejos recentes"))
        if self._tilt_since is not None and now - self._tilt_since >= s.head_tilt_seconds:
            attention.append(Reason(SignalType.HEAD_TILT, DriverState.ATTENTION,
                                    f"Cabeça inclinada há {_fmt_s(now - self._tilt_since)}"))

        if s.combine_signals and len(attention) >= 2:
            drowsy.append(Reason(SignalType.COMBINED, DriverState.DROWSY,
                                 f"{len(attention)} sinais de atenção simultâneos"))
        return tuple(drowsy), tuple(attention), instant

    def _resolve_level(self, now: float, drowsy: tuple[Reason, ...],
                       attention: tuple[Reason, ...], instant: bool) -> DriverState:
        """Aplica confirmação (subida) e recuperação (descida) ao nível bruto."""
        confirm = self._s.confirmation_seconds
        if not drowsy:
            self._drowsy_since = None
        elif self._drowsy_since is None:
            self._drowsy_since = now
        if not (drowsy or attention):
            self._attention_since = None
        elif self._attention_since is None:
            self._attention_since = now

        if instant or (self._drowsy_since is not None and now - self._drowsy_since >= confirm):
            confirmed = DriverState.DROWSY
        elif self._attention_since is not None and now - self._attention_since >= confirm:
            confirmed = DriverState.ATTENTION
        else:
            confirmed = DriverState.NORMAL

        raw = DriverState.DROWSY if drowsy else DriverState.ATTENTION if attention else DriverState.NORMAL
        current = self._level

        if _RANK[confirmed] > _RANK[current]:
            self._level = confirmed
            self._recovery_since = None
            self._level_reasons = drowsy + attention
        elif _RANK[raw] < _RANK[current]:
            if self._recovery_since is None:
                self._recovery_since = now
            if now - self._recovery_since >= self._s.recovery_seconds:
                self._level = _BY_RANK[max(_RANK[raw], _RANK[confirmed])]
                self._recovery_since = None
                self._level_reasons = drowsy + attention
        else:
            self._recovery_since = None
            if drowsy or attention:
                self._level_reasons = drowsy + attention
        return self._level

    def _no_face_reason(self, m: FaceMetrics) -> Reason:
        if m.face_detected and m.quality_issues:
            text = "Detecção pouco confiável: " + ", ".join(m.quality_issues)
        elif m.face_detected:
            text = "Detecção pouco confiável"
        else:
            text = "Rosto não detectado pela câmera"
        return Reason(SignalType.FACE_UNAVAILABLE, DriverState.NO_FACE, text)
