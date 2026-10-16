"""Política de alertas sonoros e visuais.

O analisador diz *qual é o estado*; o AlertManager decide *quando avisar*:

* um alerta de sonolência por episódio (contabilizado e registrado);
* repetição do som no ritmo de ``alert_interval_seconds`` enquanto o estado
  persistir — nunca a cada quadro;
* intervalo mínimo entre sons, mesmo se um novo episódio começar logo depois;
* silêncio temporário que suprime o som sem interromper o monitoramento;
* avisos de falha de monitoramento (rosto ausente/câmera) distintos do alerta
  de sonolência, com som próprio.
"""

from __future__ import annotations

import time
from collections.abc import Callable
from dataclasses import dataclass
from enum import Enum

from app.drowsiness_analyzer import AnalysisResult, DriverState
from app.sound import SoundKind, SoundPlayer
from config.settings import AlertSettings

DROWSINESS_MESSAGE = (
    "ALERTA: POSSÍVEIS SINAIS DE SONOLÊNCIA. REDUZA O RISCO E PARE EM UM LOCAL SEGURO."
)
DROWSINESS_ADVICE = (
    "Um alarme não substitui o descanso. Assim que for seguro, estacione e descanse."
)
ATTENTION_MESSAGE = "Atenção: sinais que merecem acompanhamento. Considere fazer uma pausa."
FACE_LOST_MESSAGE = (
    "MONITORAMENTO PREJUDICADO: ROSTO NÃO DETECTADO. AJUSTE A CÂMERA OU SUA POSIÇÃO."
)
CAMERA_MESSAGE = "MONITORAMENTO INTERROMPIDO: FALHA NA CÂMERA."


class AlertKind(Enum):
    DROWSINESS = "DROWSINESS"
    ATTENTION = "ATTENTION"
    FACE_LOST = "FACE_LOST"
    CAMERA_FAILURE = "CAMERA_FAILURE"
    TEST = "TEST"


_SOUND_FOR = {
    AlertKind.DROWSINESS: SoundKind.DROWSINESS,
    AlertKind.ATTENTION: SoundKind.ATTENTION,
    AlertKind.FACE_LOST: SoundKind.MONITORING,
    AlertKind.CAMERA_FAILURE: SoundKind.MONITORING,
    AlertKind.TEST: SoundKind.DROWSINESS,
}


@dataclass(frozen=True)
class Alert:
    kind: AlertKind
    timestamp: float
    message: str
    reason: str
    sound_played: bool
    new_episode: bool


class AlertManager:
    """Decide quando emitir alertas. Deve ser usado em uma única thread."""

    def __init__(self, settings: AlertSettings, player: SoundPlayer | None,
                 clock: Callable[[], float] = time.monotonic) -> None:
        self._s = settings
        self._player = player
        self._clock = clock
        self._muted_until = 0.0
        self.reset()

    # ------------------------------------------------------------------ #
    # Sessão e configuração
    # ------------------------------------------------------------------ #
    def reset(self) -> None:
        self.drowsiness_count = 0
        self._last_sound: dict[AlertKind, float] = {}
        self._in_drowsy = False
        self._in_attention = False
        self._in_face_lost = False
        self._camera_failed = False

    def update_settings(self, settings: AlertSettings) -> None:
        self._s = settings
        if not settings.sound_enabled and self._player is not None:
            self._player.stop()

    # ------------------------------------------------------------------ #
    # Silêncio temporário
    # ------------------------------------------------------------------ #
    def mute(self, minutes: float | None = None) -> float:
        """Silencia os sons por ``minutes`` (padrão da configuração). Retorna os segundos."""
        seconds = 60.0 * (minutes if minutes is not None else self._s.mute_minutes)
        self._muted_until = self._clock() + seconds
        if self._player is not None:
            self._player.stop()
        return seconds

    def unmute(self) -> None:
        self._muted_until = 0.0

    @property
    def muted(self) -> bool:
        return self._clock() < self._muted_until

    @property
    def mute_remaining(self) -> float:
        return max(0.0, self._muted_until - self._clock())

    # ------------------------------------------------------------------ #
    # Processamento
    # ------------------------------------------------------------------ #
    def process(self, result: AnalysisResult) -> list[Alert]:
        """Avalia um resultado da análise e retorna os alertas emitidos agora."""
        t = result.timestamp
        state = result.state
        alerts: list[Alert] = []

        if state is DriverState.DROWSY:
            new = not self._in_drowsy
            if new:
                self._in_drowsy = True
                self.drowsiness_count += 1
            alert = self._episode_alert(AlertKind.DROWSINESS, t, new, DROWSINESS_MESSAGE,
                                        result.summary, sound=True)
            if alert:
                alerts.append(alert)
        else:
            self._in_drowsy = False

        if state is DriverState.ATTENTION and not self._in_attention:
            played = self._s.attention_sound and self._try_play(AlertKind.ATTENTION, t)
            alerts.append(Alert(AlertKind.ATTENTION, t, ATTENTION_MESSAGE, result.summary,
                                played, new_episode=True))
        self._in_attention = state is DriverState.ATTENTION

        face_lost = (state is DriverState.NO_FACE
                     and result.unreliable_duration >= self._s.face_lost_alert_seconds)
        if face_lost:
            new = not self._in_face_lost
            self._in_face_lost = True
            alert = self._episode_alert(AlertKind.FACE_LOST, t, new, FACE_LOST_MESSAGE,
                                        result.summary, sound=self._s.face_lost_sound)
            if alert:
                alerts.append(alert)
        elif state is not DriverState.NO_FACE:
            self._in_face_lost = False

        return alerts

    def camera_failure(self, reason: str) -> Alert | None:
        """Registra uma falha de câmera (um alerta por episódio de falha)."""
        if self._camera_failed:
            return None
        self._camera_failed = True
        t = self._clock()
        played = self._s.camera_failure_sound and self._try_play(AlertKind.CAMERA_FAILURE, t)
        return Alert(AlertKind.CAMERA_FAILURE, t, CAMERA_MESSAGE, reason, played, new_episode=True)

    def camera_recovered(self) -> None:
        self._camera_failed = False

    def test(self) -> Alert:
        """Toca o alarme de sonolência para teste (ignora silêncio e ativação)."""
        t = self._clock()
        played = False
        if self._player is not None and self._player.available:
            played = self._player.play(SoundKind.DROWSINESS, self._s.volume)
        return Alert(AlertKind.TEST, t, "Teste do alerta sonoro", "Acionado manualmente",
                     played, new_episode=False)

    # ------------------------------------------------------------------ #
    # Internos
    # ------------------------------------------------------------------ #
    def _episode_alert(self, kind: AlertKind, t: float, new: bool, message: str,
                       reason: str, sound: bool) -> Alert | None:
        last = self._last_sound.get(kind)
        due = last is None or t - last >= self._s.alert_interval_seconds
        played = sound and due and self._try_play(kind, t)
        if new or played:
            return Alert(kind, t, message, reason, played, new_episode=new)
        return None

    def _try_play(self, kind: AlertKind, t: float) -> bool:
        if not self._s.sound_enabled or self.muted:
            return False
        if self._player is None or not self._player.available:
            return False
        if self._player.play(_SOUND_FOR[kind], self._s.volume):
            self._last_sound[kind] = t
            return True
        return False
