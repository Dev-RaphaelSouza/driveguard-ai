"""Orquestração de uma sessão de monitoramento.

Vive na thread da interface e conecta as peças:

    CameraWorker ──(sinais)──► MonitoringController ──► AlertManager ──► AudioPlayer
                                        │                      │
                                        ├──► EventDatabase ◄───┘
                                        └──► sinais para a UI (estado, quadros, alertas)

A janela principal apenas reage aos sinais deste objeto; nenhuma regra de
negócio fica nos widgets.
"""

from __future__ import annotations

import copy
import logging
import time
from collections.abc import Callable
from datetime import timedelta
from pathlib import Path

from PySide6.QtCore import QObject, Signal, Slot
from PySide6.QtGui import QImage

from app.alert_manager import Alert, AlertKind, AlertManager
from app.camera_worker import CameraStatus, CameraWorker, EngineFactory
from app.database import EventDatabase, EventType, utc_now
from app.drowsiness_analyzer import AnalysisResult, AnalyzerEvent, DriverState, EventKind
from app.frame_source import FrameSource, OpenCVCameraSource, VideoFileSource
from app.sound import SoundPlayer
from config.settings import AppSettings

log = logging.getLogger(__name__)

WORKER_STOP_TIMEOUT_MS = 5000

_EPISODE_FOR_STATE = {
    DriverState.DROWSY: EventType.DROWSINESS,
    DriverState.ATTENTION: EventType.ATTENTION,
    DriverState.NO_FACE: EventType.FACE_LOST,
}


def make_source_factory(video_path: Path | None) -> Callable[[AppSettings], FrameSource]:
    """Cria a fábrica de origem: arquivo de vídeo (testes) ou webcam."""
    if video_path is not None:
        return lambda _settings: VideoFileSource(video_path, loop=True, realtime=True)

    def camera(settings: AppSettings) -> FrameSource:
        cam = settings.camera
        return OpenCVCameraSource(cam.camera_index, cam.frame_width, cam.frame_height,
                                  cam.capture_fps, cam.backend)
    return camera


class MonitoringController(QObject):
    state_changed = Signal(object)            # DriverState | None (None = parado)
    analysis_updated = Signal(object)         # AnalysisResult
    frame_ready = Signal(QImage)
    alert_raised = Signal(object)             # Alert
    camera_status_changed = Signal(str, str)  # CameraStatus, mensagem
    running_changed = Signal(bool)
    error_occurred = Signal(str, str)         # título, detalhe
    alerts_count_changed = Signal(int)
    stats_updated = Signal(float, float)      # FPS, ms/quadro
    history_changed = Signal()
    notice = Signal(str)                      # mensagens informativas curtas

    def __init__(self, settings: AppSettings, database: EventDatabase,
                 player: SoundPlayer | None, video_path: Path | None = None,
                 engine_factory: EngineFactory | None = None,
                 source_factory: Callable[[AppSettings], FrameSource] | None = None,
                 parent: QObject | None = None) -> None:
        super().__init__(parent)
        self._settings = copy.deepcopy(settings)
        self._db = database
        self._player = player
        self._video_path = video_path
        self._source_factory = source_factory or make_source_factory(video_path)
        self._engine_factory = engine_factory
        self._alerts = AlertManager(self._settings.alerts, player)
        self._worker: CameraWorker | None = None
        # O último worker encerrado é mantido vivo por um ciclo: sinais já
        # enfileirados dele ainda podem chegar, e ``sender()`` não pode apontar
        # para um objeto C++ destruído.
        self._retired_worker: CameraWorker | None = None
        self._session_id: int | None = None
        self._session_started: float | None = None
        self._state: DriverState | None = None
        self._episodes: dict[EventType, tuple[int, float]] = {}
        self._camera_failure_logged = False
        self._last_result: AnalysisResult | None = None

    # ------------------------------------------------------------------ #
    # Propriedades
    # ------------------------------------------------------------------ #
    @property
    def is_running(self) -> bool:
        return self._worker is not None

    @property
    def state(self) -> DriverState | None:
        return self._state

    @property
    def settings(self) -> AppSettings:
        return copy.deepcopy(self._settings)

    @property
    def session_id(self) -> int | None:
        return self._session_id

    @property
    def alerts_count(self) -> int:
        return self._alerts.drowsiness_count

    @property
    def session_elapsed(self) -> float:
        return 0.0 if self._session_started is None else time.monotonic() - self._session_started

    @property
    def source_label(self) -> str:
        if self._video_path is not None:
            return f"Arquivo: {self._video_path.name}"
        return f"Câmera {self._settings.camera.camera_index}"

    @property
    def using_video_file(self) -> bool:
        return self._video_path is not None

    @property
    def muted(self) -> bool:
        return self._alerts.muted

    @property
    def mute_remaining(self) -> float:
        return self._alerts.mute_remaining

    @property
    def audio_available(self) -> bool:
        return self._player is not None and self._player.available

    # ------------------------------------------------------------------ #
    # Controle da sessão
    # ------------------------------------------------------------------ #
    def start(self) -> None:
        if self._worker is not None:
            return
        self._alerts.reset()
        self._episodes.clear()
        self._camera_failure_logged = False
        self._last_result = None
        try:
            self._session_id = self._db.start_session(self.source_label)
        except Exception as exc:  # noqa: BLE001 - monitorar é mais importante que registrar
            log.error("Não foi possível registrar a sessão: %s", exc)
            self._session_id = None
        self._session_started = time.monotonic()

        worker = CameraWorker(self._source_factory, self._settings, self._engine_factory)
        worker.analysis_ready.connect(self._on_analysis)
        worker.frame_ready.connect(self._on_frame)
        worker.status_changed.connect(self._on_camera_status)
        worker.fatal_error.connect(self._on_fatal_error)
        worker.stats.connect(self.stats_updated)
        self._worker = worker
        self._set_state(DriverState.STARTING)
        self.alerts_count_changed.emit(0)
        self.running_changed.emit(True)
        worker.start()
        log.info("Monitoramento iniciado (%s)", self.source_label)

    def stop(self) -> None:
        worker = self._worker
        if worker is None:
            return
        self._worker = None  # resultados que ainda chegarem serão ignorados
        worker.request_stop()
        if not worker.wait(WORKER_STOP_TIMEOUT_MS):
            log.warning("A thread de captura não terminou em %s ms", WORKER_STOP_TIMEOUT_MS)
        self._retired_worker = worker
        self._close_all_episodes(time.monotonic())
        if self._session_id is not None:
            try:
                self._db.end_session(self._session_id, self._alerts.drowsiness_count)
            except Exception as exc:  # noqa: BLE001
                log.error("Não foi possível encerrar a sessão no banco: %s", exc)
        self._session_id = None
        self._session_started = None
        self._set_state(None)
        self.running_changed.emit(False)
        self.history_changed.emit()
        log.info("Monitoramento encerrado")

    def toggle(self) -> None:
        self.stop() if self.is_running else self.start()

    def shutdown(self) -> None:
        self.stop()
        if self._player is not None and hasattr(self._player, "shutdown"):
            self._player.shutdown()

    def recalibrate(self) -> None:
        if self._worker is not None:
            self._worker.request_recalibration()
            self.notice.emit("Recalibrando: olhe para a frente com os olhos abertos por alguns segundos.")

    def test_alert(self) -> Alert:
        alert = self._alerts.test()
        if not alert.sound_played:
            self.error_occurred.emit("Áudio indisponível", self._audio_problem())
        return alert

    def mute(self) -> float:
        seconds = self._alerts.mute()
        self.notice.emit(f"Som silenciado por {int(seconds // 60)} min. O monitoramento continua ativo.")
        return seconds

    def unmute(self) -> None:
        self._alerts.unmute()
        self.notice.emit("Som reativado.")

    def apply_settings(self, settings: AppSettings) -> None:
        """Aplica configurações; reinicia a captura se a câmera mudou."""
        old = self._settings
        self._settings = copy.deepcopy(settings)
        self._alerts.update_settings(self._settings.alerts)
        if self._worker is None:
            return
        if self._settings.camera != old.camera and not self.using_video_file:
            capture_keys = ("camera_index", "backend", "frame_width", "frame_height", "capture_fps")
            if any(getattr(old.camera, k) != getattr(self._settings.camera, k) for k in capture_keys):
                self.notice.emit("Reiniciando a captura com as novas preferências de câmera…")
                self.stop()
                self.start()
                return
        self._worker.update_settings(self._settings)

    # ------------------------------------------------------------------ #
    # Slots (executados na thread da GUI)
    # ------------------------------------------------------------------ #
    @Slot(QImage)
    def _on_frame(self, image: QImage) -> None:
        worker = self.sender()
        if isinstance(worker, CameraWorker):
            worker.frame_budget.release()
        if self._worker is not None:
            self.frame_ready.emit(image)

    @Slot(object)
    def _on_analysis(self, result: AnalysisResult) -> None:
        if self._worker is None or self.sender() is not self._worker:
            return
        previous = self._state
        self._last_result = result
        if result.state != previous:
            self._on_state_exit(previous, result.timestamp)
            self._set_state(result.state)

        for event in result.events:
            self._handle_analyzer_event(event, result)

        count_before = self._alerts.drowsiness_count
        for alert in self._alerts.process(result):
            self._handle_alert(alert, result)
        if self._alerts.drowsiness_count != count_before:
            self.alerts_count_changed.emit(self._alerts.drowsiness_count)

        self.analysis_updated.emit(result)

    @Slot(str, str)
    def _on_camera_status(self, status: str, message: str) -> None:
        if self._worker is None or self.sender() is not self._worker:
            return
        self.camera_status_changed.emit(status, message)
        if status == CameraStatus.ERROR:
            self._close_all_episodes(time.monotonic())
            self._set_state(DriverState.CAMERA_UNAVAILABLE)
            alert = self._alerts.camera_failure(message)
            if alert is not None:
                self._log(EventType.CAMERA_FAILURE, message, None, None)
                self.alert_raised.emit(alert)
        elif status == CameraStatus.RUNNING:
            self._alerts.camera_recovered()
            if self._state == DriverState.CAMERA_UNAVAILABLE:
                self._set_state(DriverState.STARTING)

    @Slot(str)
    def _on_fatal_error(self, message: str) -> None:
        if self._worker is None or self.sender() is not self._worker:
            return
        log.error("Erro fatal no monitoramento: %s", message)
        self.stop()
        self.error_occurred.emit("Não foi possível continuar o monitoramento", message)

    # ------------------------------------------------------------------ #
    # Registro de eventos
    # ------------------------------------------------------------------ #
    def _set_state(self, state: DriverState | None) -> None:
        if state != self._state:
            log.info("Estado: %s", state.label if state else "DESLIGADO")
            self._state = state
            self.state_changed.emit(state)

    def _handle_alert(self, alert: Alert, result: AnalysisResult) -> None:
        if alert.new_episode:
            event_type = {
                AlertKind.DROWSINESS: EventType.DROWSINESS,
                AlertKind.ATTENTION: EventType.ATTENTION,
                AlertKind.FACE_LOST: EventType.FACE_LOST,
            }.get(alert.kind)
            if event_type is not None:
                started = result.timestamp
                if event_type is EventType.FACE_LOST:
                    started -= result.unreliable_duration
                event_id = self._log(event_type, alert.reason, result,
                                     ago=result.timestamp - started)
                if event_id is not None:
                    self._episodes[event_type] = (event_id, started)
        self.alert_raised.emit(alert)

    def _handle_analyzer_event(self, event: AnalyzerEvent, result: AnalysisResult) -> None:
        if event.kind is EventKind.YAWN:
            self._log(EventType.YAWN, "Abertura da boca sustentada", result,
                      duration=event.duration, ago=event.duration or 0.0)
        elif event.kind is EventKind.LONG_CLOSURE:
            self._log(EventType.LONG_CLOSURE,
                      f"Olhos fechados por {event.duration:.1f} s".replace(".", ","), result,
                      duration=event.duration, ago=event.duration or 0.0)
        elif event.kind is EventKind.CALIBRATED:
            self.notice.emit(f"Calibração concluída ({event.detail}).")
        elif event.kind is EventKind.CALIBRATION_FAILED:
            self.notice.emit(f"Calibração inconsistente: {event.detail}.")

    def _on_state_exit(self, previous: DriverState | None, now: float) -> None:
        event_type = _EPISODE_FOR_STATE.get(previous) if previous else None
        if event_type is not None:
            self._close_episode(event_type, now)

    def _close_episode(self, event_type: EventType, now: float) -> None:
        episode = self._episodes.pop(event_type, None)
        if episode is None:
            return
        event_id, started = episode
        try:
            self._db.set_event_duration(event_id, max(0.0, now - started))
        except Exception as exc:  # noqa: BLE001
            log.error("Falha ao atualizar duração do evento: %s", exc)
        self.history_changed.emit()

    def _close_all_episodes(self, now: float) -> None:
        for event_type in list(self._episodes):
            self._close_episode(event_type, now)

    def _log(self, event_type: EventType, reason: str, result: AnalysisResult | None,
             duration: float | None = None, ago: float = 0.0) -> int | None:
        metrics = None
        if result is not None and self._settings.privacy.store_event_metrics:
            metrics = result.metrics_snapshot()
        try:
            event_id = self._db.log_event(
                event_type, session_id=self._session_id, reason=reason, duration=duration,
                metrics=metrics, occurred_at=utc_now() - timedelta(seconds=max(0.0, ago)),
            )
        except Exception as exc:  # noqa: BLE001 - falha de disco não interrompe o monitoramento
            log.error("Falha ao registrar evento %s: %s", event_type.value, exc)
            return None
        self.history_changed.emit()
        return event_id

    def _audio_problem(self) -> str:
        if self._player is None:
            return "Nenhum dispositivo de áudio foi inicializado."
        error = getattr(self._player, "last_error", "")
        return error or "Não foi possível tocar o som. Verifique o dispositivo de saída de áudio."
