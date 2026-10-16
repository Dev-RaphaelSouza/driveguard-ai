"""Tela principal: vídeo ao vivo, estado do motorista e sinais monitorados."""

from __future__ import annotations

from PySide6.QtCore import Qt, Signal
from PySide6.QtGui import QImage
from PySide6.QtWidgets import (
    QFrame,
    QGridLayout,
    QHBoxLayout,
    QScrollArea,
    QVBoxLayout,
    QWidget,
)

from app.alert_manager import (
    CAMERA_MESSAGE,
    DROWSINESS_ADVICE,
    DROWSINESS_MESSAGE,
    FACE_LOST_MESSAGE,
)
from app.camera_worker import CameraStatus
from app.drowsiness_analyzer import AnalysisResult, DriverState
from config.settings import AppSettings
from ui.components import (
    AlertBanner,
    Card,
    EyesIndicator,
    MetricTile,
    Sparkline,
    StatusPanel,
    VideoView,
    make_button,
    repolish,
    styled_label,
)
from ui.icons import icon
from ui.styles import IDLE_DESCRIPTION, IDLE_LABEL, Palette


def fmt(value: float, digits: int = 1) -> str:
    """Formata números no padrão brasileiro (vírgula decimal)."""
    return f"{value:.{digits}f}".replace(".", ",")


class MonitorPage(QWidget):
    start_stop_requested = Signal()
    test_requested = Signal()
    mute_requested = Signal()
    recalibrate_requested = Signal()

    def __init__(self, settings: AppSettings, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setObjectName("Page")
        self._settings = settings
        self._state: DriverState | None = None
        self._running = False
        self._camera_message = ""
        self._last_result: AnalysisResult | None = None

        root = QVBoxLayout(self)
        root.setContentsMargins(24, 20, 24, 22)
        root.setSpacing(16)

        self.banner = AlertBanner()
        self.banner.mute_requested.connect(self.mute_requested)
        root.addWidget(self.banner)

        body = QHBoxLayout()
        body.setSpacing(20)
        root.addLayout(body, 1)

        # ---- coluna esquerda: vídeo e controles -------------------------- #
        left = QVBoxLayout()
        left.setSpacing(14)
        self.video = VideoView()
        left.addWidget(self.video, 1)

        controls = QHBoxLayout()
        controls.setSpacing(10)
        self.start_button = make_button("Iniciar monitoramento", "play", "Primary", Palette.ON_ACCENT)
        self.start_button.clicked.connect(self.start_stop_requested)
        self.test_button = make_button("Testar alerta", "bell")
        self.test_button.setToolTip("Toca o alarme sonoro para conferir volume e dispositivo de áudio.")
        self.test_button.clicked.connect(self.test_requested)
        self.mute_button = make_button("Silenciar", "volume-off")
        self.mute_button.setCheckable(True)
        self.mute_button.setToolTip("Silencia o som temporariamente; o monitoramento continua.")
        self.mute_button.clicked.connect(self._on_mute_clicked)
        self.recal_button = make_button("Recalibrar", "refresh")
        self.recal_button.setToolTip("Mede novamente a referência de olhos abertos e postura.")
        self.recal_button.clicked.connect(self.recalibrate_requested)
        for button in (self.start_button, self.test_button, self.mute_button, self.recal_button):
            controls.addWidget(button)
        controls.addStretch(1)
        left.addLayout(controls)

        chart = Card("Abertura ocular (EAR) · últimos 20 s", "eye")
        chart.body.setContentsMargins(18, 14, 18, 12)
        chart.body.setSpacing(6)
        self.sparkline = Sparkline(20.0)
        self.sparkline.setFixedHeight(92)
        chart.body.addWidget(self.sparkline)
        left.addWidget(chart)
        body.addLayout(left, 1)
        self._source = ""

        # ---- coluna direita: estado e métricas --------------------------- #
        side = QWidget()
        side_layout = QVBoxLayout(side)
        side_layout.setContentsMargins(0, 0, 6, 0)
        side_layout.setSpacing(14)

        self.status = StatusPanel()
        side_layout.addWidget(self.status)

        signals = Card("Sinais monitorados", "activity")
        grid = QGridLayout()
        grid.setSpacing(10)
        grid.setColumnStretch(0, 1)
        grid.setColumnStretch(1, 1)
        self.tile_eyes = MetricTile("Olhos")
        self.eyes = EyesIndicator()
        self.tile_eyes.add_to_caption_row(self.eyes)
        self.tile_closure = MetricTile("Fechamento atual")
        self.tile_ear = MetricTile("EAR (abertura)")
        self.tile_quality = MetricTile("Qualidade")
        self.tile_perclos = MetricTile("PERCLOS")
        self.tile_blinks = MetricTile("Piscadas")
        self.tile_yawns = MetricTile("Bocejos")
        self.tile_head = MetricTile("Cabeça")
        tiles = [self.tile_eyes, self.tile_closure, self.tile_ear, self.tile_quality,
                 self.tile_perclos, self.tile_blinks, self.tile_yawns, self.tile_head]
        for i, tile in enumerate(tiles):
            grid.addWidget(tile, i // 2, i % 2)
        signals.body.addLayout(grid)
        side_layout.addWidget(signals)

        disclaimer = styled_label(
            "Ferramenta experimental de assistência. Não substitui o descanso nem "
            "dispositivos de segurança certificados.", "Faint", wrap=True)
        side_layout.addWidget(disclaimer)
        side_layout.addStretch(1)

        scroll = QScrollArea()
        scroll.setWidget(side)
        scroll.setWidgetResizable(True)
        scroll.setFrameShape(QFrame.Shape.NoFrame)
        scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        scroll.setFixedWidth(404)
        body.addWidget(scroll)

        self.set_running(False, "")
        self.set_state(None)
        self._reset_tiles()

    # ------------------------------------------------------------------ #
    # API usada pela janela principal
    # ------------------------------------------------------------------ #
    def apply_settings(self, settings: AppSettings) -> None:
        self._settings = settings
        if not settings.privacy.show_preview:
            self.video.clear_frame()
        self._refresh_placeholder()

    def set_running(self, running: bool, source_label: str) -> None:
        self._running = running
        self.start_button.setText("Parar monitoramento" if running else "Iniciar monitoramento")
        self.start_button.setIcon(icon("stop" if running else "play",
                                       "#FCA5A5" if running else Palette.ON_ACCENT, 18))
        self.start_button.setProperty("running", running)
        repolish(self.start_button)
        self.mute_button.setEnabled(running)
        self.recal_button.setEnabled(running)
        self._source = source_label if running else ""
        self.video.set_live(running)
        if not running:
            self.video.clear_frame()
            self.video.set_hud("", "")
            self.video.set_calibration(None)
            self.sparkline.clear()
            self._reset_tiles()
            self._camera_message = ""
        self._refresh_placeholder()

    def set_state(self, state: DriverState | None) -> None:
        self._state = state
        self.video.set_state(state)
        if state is None:
            self.status.set_state(None, IDLE_LABEL, IDLE_DESCRIPTION)
        elif state == DriverState.CAMERA_UNAVAILABLE:
            self.status.set_state(state, state.label, state.description,
                                  [self._camera_message] if self._camera_message else [])
            self.video.clear_frame()
        else:
            reasons = [r.text for r in self._last_result.reasons] if self._last_result else []
            self.status.set_state(state, state.label, state.description, reasons)
        self._update_banner()
        self._refresh_placeholder()

    def set_camera_status(self, status: str, message: str) -> None:
        if status == CameraStatus.ERROR:
            self._camera_message = message
            if self._state == DriverState.CAMERA_UNAVAILABLE:
                self.set_state(self._state)
        elif status == CameraStatus.RUNNING:
            self._camera_message = ""
        self._refresh_placeholder()

    def set_frame(self, image: QImage) -> None:
        if self._running and self._settings.privacy.show_preview:
            self.video.set_frame(image)

    def set_stats(self, fps: float, ms: float) -> None:
        self.video.set_hud(f"AO VIVO  ·  {fps:.0f} FPS  ·  {ms:.0f} ms", self._source)

    def set_muted(self, muted: bool, remaining: float) -> None:
        self.mute_button.setChecked(muted)
        if muted:
            minutes, seconds = divmod(int(remaining), 60)
            self.mute_button.setText(f"Silenciado {minutes}:{seconds:02d}")
            self.mute_button.setIcon(icon("volume-off", Palette.WARN, 18))
        else:
            self.mute_button.setText("Silenciar")
            self.mute_button.setIcon(icon("volume-off", Palette.TEXT, 18))
        self.banner.mute_button.setText("Som silenciado" if muted else "Silenciar")
        self.banner.mute_button.setEnabled(not muted)

    def update_analysis(self, result: AnalysisResult) -> None:
        self._last_result = result
        det = self._settings.detection
        if result.state == self._state and result.state not in (None, DriverState.CAMERA_UNAVAILABLE):
            self.status.set_state(result.state, result.state.label, self._description(result),
                                  [r.text for r in result.reasons])
        self.video.set_calibration(result.calibration_progress if result.calibrating else None)
        self.sparkline.add(result.timestamp, result.ear if result.face_reliable else None,
                           result.ear_threshold)
        self._update_tiles(result, det)
        self._update_banner()

    # ------------------------------------------------------------------ #
    # Internos
    # ------------------------------------------------------------------ #
    def _on_mute_clicked(self) -> None:
        # O estado visual real vem de set_muted (fonte da verdade: AlertManager).
        self.mute_button.setChecked(not self.mute_button.isChecked())
        self.mute_requested.emit()

    @staticmethod
    def _description(result: AnalysisResult) -> str:
        if result.recovering_for is not None and result.state in (DriverState.DROWSY, DriverState.ATTENTION):
            return f"Sinais diminuíram há {fmt(result.recovering_for)} s — aguardando confirmação."
        return result.state.description

    def _refresh_placeholder(self) -> None:
        if not self._running:
            self.video.set_placeholder("power", "Câmera desligada",
                                       "Clique em Iniciar monitoramento para começar.")
        elif self._state == DriverState.CAMERA_UNAVAILABLE:
            self.video.set_placeholder("camera-off", "Câmera indisponível",
                                       self._camera_message or "Tentando reconectar…")
        elif not self._settings.privacy.show_preview:
            self.video.set_placeholder("eye-off", "Pré-visualização oculta",
                                       "A análise continua normalmente. Ative o vídeo em "
                                       "Configurações › Privacidade.")
        else:
            self.video.set_placeholder("loader", "Iniciando câmera e detector…",
                                       "Isso leva alguns segundos na primeira vez.")

    def _update_banner(self) -> None:
        state, result = self._state, self._last_result
        if state == DriverState.DROWSY:
            detail = result.summary if result else ""
            self.banner.show_banner(AlertBanner.DROWSY, DROWSINESS_MESSAGE, detail, DROWSINESS_ADVICE)
        elif state == DriverState.CAMERA_UNAVAILABLE:
            self.banner.show_banner(AlertBanner.MONITORING, CAMERA_MESSAGE, self._camera_message,
                                    "O sistema tentará reconectar automaticamente.", mute_visible=False)
        elif (state == DriverState.NO_FACE and result is not None
              and result.unreliable_duration >= self._settings.alerts.face_lost_alert_seconds):
            detail = result.reasons[0].text if result.reasons else ""
            self.banner.show_banner(AlertBanner.MONITORING, FACE_LOST_MESSAGE, detail,
                                    "Sem o rosto visível, a sonolência não pode ser avaliada.")
        else:
            self.banner.hide_banner()

    def _reset_tiles(self) -> None:
        self.eyes.set_closed(None)
        for tile in (self.tile_eyes, self.tile_closure, self.tile_ear, self.tile_quality,
                     self.tile_perclos, self.tile_blinks, self.tile_yawns, self.tile_head):
            tile.set_value("—", "", None)
            tile.set_bar(None)
        self.tile_closure.set_bar(0.0)
        self.tile_quality.set_bar(0.0)

    def _update_tiles(self, r: AnalysisResult, det) -> None:
        reliable = r.face_reliable
        # Olhos
        if not reliable:
            self.eyes.set_closed(None)
            self.tile_eyes.set_value("—", "sem dados confiáveis", Palette.TEXT_MUTED)
        elif r.eyes_closed:
            self.eyes.set_closed(True)
            self.tile_eyes.set_value("Fechados", "abaixo do limiar", Palette.DANGER)
        else:
            self.eyes.set_closed(False)
            self.tile_eyes.set_value("Abertos", "acima do limiar", Palette.OK)

        # Fechamento em andamento em relação ao limite de alerta
        limit = det.long_closure_seconds
        fraction = min(1.0, r.closure_duration / limit) if limit else 0.0
        color = Palette.DANGER if fraction >= 1 else Palette.WARN if fraction > 0.5 else Palette.ACCENT
        self.tile_closure.set_value(f"{fmt(r.closure_duration)} s", f"alerta a partir de {fmt(limit)} s",
                                    color if r.closure_duration > 0 else None)
        self.tile_closure.set_bar(fraction, color)

        # EAR
        if reliable and r.ear is not None:
            ref = "calibrada" if r.calibrated else "manual"
            self.tile_ear.set_value(fmt(r.ear, 2), f"limiar {fmt(r.ear_threshold, 2)} · ref. {ref}",
                                    Palette.DANGER if r.eyes_closed else None)
        else:
            self.tile_ear.set_value("—", f"limiar {fmt(r.ear_threshold, 2)}", None)

        # Qualidade
        q = r.quality
        q_color = Palette.OK if q >= 0.75 else Palette.WARN if q >= det.min_quality else Palette.DANGER
        issues = ", ".join(r.quality_issues) if r.quality_issues else ("boa" if q >= 0.75 else "aceitável")
        if q <= 0:
            issues = "rosto não detectado"
        self.tile_quality.set_value(f"{q * 100:.0f}%", issues, q_color)
        self.tile_quality.set_bar(q, q_color)

        # PERCLOS
        if r.perclos is None:
            self.tile_perclos.set_value("—", "coletando dados…", None)
        else:
            p_color = (Palette.DANGER if r.perclos >= det.perclos_drowsy
                       else Palette.WARN if r.perclos >= det.perclos_attention else None)
            self.tile_perclos.set_value(f"{r.perclos * 100:.0f}%",
                                        f"olhos fechados · {det.perclos_window_seconds:.0f} s", p_color)

        # Piscadas
        if r.blink_rate is None:
            self.tile_blinks.set_value("—", f"{r.long_blinks} longas · coletando…", None)
        else:
            lb_color = Palette.WARN if r.long_blinks >= det.long_blinks_attention_count else None
            self.tile_blinks.set_value(f"{r.blink_rate:.0f}/min", f"{r.long_blinks} longas recentes", lb_color)

        # Bocejos
        y_color = Palette.WARN if (r.yawning or r.yawns >= det.yawns_attention_count) else None
        sub = "bocejando agora" if r.yawning else f"últimos {det.yawn_window_seconds / 60:.0f} min"
        self.tile_yawns.set_value(str(r.yawns), sub, y_color)

        # Cabeça
        if r.head_deviation is None:
            pending = "aguardando calibração" if r.calibrating else "sem dados"
            self.tile_head.set_value("—", pending, None)
        else:
            h_color = Palette.WARN if r.head_deviation >= det.head_tilt_degrees else None
            self.tile_head.set_value(f"{r.head_deviation:.0f}°", "desvio da postura de referência", h_color)
