"""Janela principal: navegação, cabeçalho de status e ligação com o controlador.

A janela só traduz sinais do ``MonitoringController`` em atualizações visuais
e ações do usuário em chamadas ao controlador. Regras de negócio ficam fora
daqui.
"""

from __future__ import annotations

import logging
from pathlib import Path

from PySide6.QtCore import QSize, Qt, QTimer, QUrl
from PySide6.QtGui import QCloseEvent, QDesktopServices, QKeySequence, QShortcut
from PySide6.QtWidgets import (
    QApplication,
    QFrame,
    QHBoxLayout,
    QLabel,
    QMainWindow,
    QMessageBox,
    QStackedWidget,
    QVBoxLayout,
    QWidget,
)

from app.alert_manager import Alert, AlertKind
from app.camera_worker import CameraStatus
from app.database import EventDatabase
from app.drowsiness_analyzer import DriverState
from app.monitoring_controller import MonitoringController
from app.settings_manager import SettingsError, SettingsManager
from app.sound import SoundKind
from config.settings import APP_NAME, APP_VERSION, AppSettings
from ui.components import NavButton, Pill, Toast, styled_label
from ui.history_page import HistoryPage
from ui.icons import app_icon, pixmap
from ui.monitor_page import MonitorPage
from ui.settings_page import SettingsPage
from ui.styles import Palette

log = logging.getLogger(__name__)

PAGES = (
    ("Monitor", "monitor", "Monitoramento", "Sinais visuais de sonolência em tempo real"),
    ("Histórico", "history", "Histórico de eventos", "Alertas e sinais registrados nas sessões"),
    ("Ajustes", "settings", "Configurações", "Sensibilidade, alertas, câmera e privacidade"),
)


class MainWindow(QMainWindow):
    def __init__(self, controller: MonitoringController, settings_manager: SettingsManager,
                 database: EventDatabase, data_path: Path, player=None) -> None:
        super().__init__()
        self._controller = controller
        self._settings_manager = settings_manager
        self._data_path = data_path
        self._player = player
        self._camera_ok = False

        self.setWindowTitle(APP_NAME)
        self.setWindowIcon(app_icon())
        self.resize(1360, 860)
        self.setMinimumSize(1120, 720)

        settings = controller.settings
        root = QWidget()
        root.setObjectName("Root")
        self.setCentralWidget(root)
        layout = QHBoxLayout(root)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(0)
        layout.addWidget(self._build_sidebar())

        main = QVBoxLayout()
        main.setContentsMargins(0, 0, 0, 0)
        main.setSpacing(0)
        main.addWidget(self._build_header())
        self.stack = QStackedWidget()
        self.monitor_page = MonitorPage(settings)
        self.history_page = HistoryPage(database, lambda: controller.session_id)
        self.settings_page = SettingsPage(settings, data_path, self._camera_in_use)
        for page in (self.monitor_page, self.history_page, self.settings_page):
            self.stack.addWidget(page)
        main.addWidget(self.stack, 1)
        layout.addLayout(main, 1)

        self.toast = Toast(root)
        self._history_refresh = QTimer(self, singleShot=True, interval=600,
                                       timeout=self._refresh_history_if_visible)
        self._ticker = QTimer(self, interval=1000, timeout=self._tick)
        self._ticker.start()

        self._connect()
        self._go_to(0)
        self._on_running_changed(False)
        self._tick()

    # ------------------------------------------------------------------ #
    # Construção
    # ------------------------------------------------------------------ #
    def _build_sidebar(self) -> QFrame:
        sidebar = QFrame()
        sidebar.setObjectName("Sidebar")
        sidebar.setFixedWidth(96)
        layout = QVBoxLayout(sidebar)
        layout.setContentsMargins(9, 18, 9, 16)
        layout.setSpacing(6)
        logo = QLabel()
        logo.setPixmap(pixmap("logo", Palette.TEXT, 42))
        logo.setAlignment(Qt.AlignmentFlag.AlignCenter)
        layout.addWidget(logo)
        brand = styled_label("DRIVEGUARD", "Brand")
        brand.setAlignment(Qt.AlignmentFlag.AlignCenter)
        layout.addWidget(brand)
        ai = QLabel("AI")
        ai.setAlignment(Qt.AlignmentFlag.AlignCenter)
        ai.setStyleSheet(f"color: {Palette.ACCENT}; font-size: 11px; font-weight: 800;")
        layout.addWidget(ai)
        layout.addSpacing(22)
        self.nav_buttons = []
        for index, (text, icon_name, _title, _subtitle) in enumerate(PAGES):
            button = NavButton(text, icon_name)
            button.clicked.connect(lambda _=False, i=index: self._go_to(i))
            layout.addWidget(button, 0, Qt.AlignmentFlag.AlignHCenter)
            self.nav_buttons.append(button)
        layout.addStretch(1)
        version = styled_label(f"v{APP_VERSION}", "Faint")
        version.setAlignment(Qt.AlignmentFlag.AlignCenter)
        layout.addWidget(version)
        return sidebar

    def _build_header(self) -> QFrame:
        header = QFrame()
        header.setObjectName("Header")
        header.setFixedHeight(76)
        row = QHBoxLayout(header)
        row.setContentsMargins(24, 10, 24, 10)
        row.setSpacing(10)
        titles = QVBoxLayout()
        titles.setSpacing(1)
        self.page_title = styled_label("", "H1")
        self.page_subtitle = styled_label("", "Muted")
        titles.addWidget(self.page_title)
        titles.addWidget(self.page_subtitle)
        row.addLayout(titles, 1)
        self.mute_pill = Pill("volume-off")
        self.camera_pill = Pill("camera")
        self.timer_pill = Pill("timer")
        self.alerts_pill = Pill("bell")
        for pill in (self.mute_pill, self.camera_pill, self.timer_pill, self.alerts_pill):
            row.addWidget(pill)
        return header

    def _connect(self) -> None:
        c = self._controller
        c.state_changed.connect(self._on_state_changed)
        c.analysis_updated.connect(self.monitor_page.update_analysis)
        c.frame_ready.connect(self.monitor_page.set_frame)
        c.camera_status_changed.connect(self._on_camera_status)
        c.running_changed.connect(self._on_running_changed)
        c.alert_raised.connect(self._on_alert)
        c.alerts_count_changed.connect(self._on_alerts_count)
        c.stats_updated.connect(self.monitor_page.set_stats)
        c.error_occurred.connect(self._show_error)
        c.notice.connect(self.toast.show_message)
        c.history_changed.connect(lambda: self._history_refresh.start())

        m = self.monitor_page
        m.start_stop_requested.connect(c.toggle)
        m.test_requested.connect(self._test_alert)
        m.mute_requested.connect(self._toggle_mute)
        m.recalibrate_requested.connect(c.recalibrate)

        self.history_page.notice.connect(self.toast.show_message)
        s = self.settings_page
        s.settings_saved.connect(self._save_settings)
        s.test_sound_requested.connect(self._test_sound)
        s.clear_history_requested.connect(self._clear_history)
        s.open_data_dir_requested.connect(
            lambda: QDesktopServices.openUrl(QUrl.fromLocalFile(str(self._data_path))))

        QShortcut(QKeySequence("Ctrl+M"), self, activated=self._toggle_mute)
        QShortcut(QKeySequence("Esc"), self, activated=self._escape)

    # ------------------------------------------------------------------ #
    # Navegação e cabeçalho
    # ------------------------------------------------------------------ #
    def _go_to(self, index: int) -> None:
        self.stack.setCurrentIndex(index)
        self.nav_buttons[index].setChecked(True)
        _text, _icon, title, subtitle = PAGES[index]
        self.page_title.setText(title)
        self.page_subtitle.setText(subtitle)
        if index == 1:
            self.history_page.refresh()

    def _tick(self) -> None:
        c = self._controller
        elapsed = int(c.session_elapsed)
        hours, rem = divmod(elapsed, 3600)
        minutes, seconds = divmod(rem, 60)
        self.timer_pill.set(f"{hours:02d}:{minutes:02d}:{seconds:02d}",
                            Palette.ACCENT if c.is_running else Palette.IDLE)
        muted = c.muted
        self.mute_pill.setVisible(muted)
        if muted:
            m, s = divmod(int(c.mute_remaining), 60)
            self.mute_pill.set(f"Som silenciado · {m}:{s:02d}", Palette.WARN)
        self.monitor_page.set_muted(muted, c.mute_remaining)

    def _update_camera_pill(self) -> None:
        c = self._controller
        if not c.is_running:
            self.camera_pill.set("Câmera inativa", Palette.IDLE, "camera")
        elif self._camera_ok:
            label = "Vídeo de teste ativo" if c.using_video_file else "Câmera ativa"
            self.camera_pill.set(label, Palette.OK, "camera")
        elif c.state == DriverState.CAMERA_UNAVAILABLE:
            self.camera_pill.set("Câmera indisponível", Palette.SYSTEM, "camera-off")
        else:
            self.camera_pill.set("Conectando…", Palette.INFO, "camera")

    def _on_alerts_count(self, count: int) -> None:
        color = Palette.DANGER if count else Palette.IDLE
        self.alerts_pill.set(f"{count} alerta{'s' if count != 1 else ''}", color)

    # ------------------------------------------------------------------ #
    # Reações ao controlador
    # ------------------------------------------------------------------ #
    def _on_running_changed(self, running: bool) -> None:
        if not running:
            self._camera_ok = False
        self.monitor_page.set_running(running, self._controller.source_label)
        self._update_camera_pill()
        self._on_alerts_count(self._controller.alerts_count)
        self._tick()

    def _on_state_changed(self, state: DriverState | None) -> None:
        self.monitor_page.set_state(state)
        self._update_camera_pill()
        self.setWindowTitle(f"{APP_NAME} — {state.label}" if state else APP_NAME)

    def _on_camera_status(self, status: str, message: str) -> None:
        self._camera_ok = status == CameraStatus.RUNNING
        self.monitor_page.set_camera_status(status, message)
        self._update_camera_pill()

    def _on_alert(self, alert: Alert) -> None:
        if alert.kind in (AlertKind.DROWSINESS, AlertKind.FACE_LOST, AlertKind.CAMERA_FAILURE):
            QApplication.alert(self)  # pisca a barra de tarefas se a janela estiver em segundo plano
            if alert.kind == AlertKind.DROWSINESS and self.stack.currentIndex() != 0:
                self._go_to(0)

    def _show_error(self, title: str, detail: str) -> None:
        QMessageBox.critical(self, title, detail)

    def _refresh_history_if_visible(self) -> None:
        if self.stack.currentIndex() == 1:
            self.history_page.refresh()

    # ------------------------------------------------------------------ #
    # Ações do usuário
    # ------------------------------------------------------------------ #
    def _toggle_mute(self) -> None:
        if not self._controller.is_running:
            return
        if self._controller.muted:
            self._controller.unmute()
        else:
            self._controller.mute()
        self._tick()

    def _escape(self) -> None:
        if self.monitor_page.banner.showing and not self._controller.muted:
            self._toggle_mute()

    def _test_alert(self) -> None:
        alert = self._controller.test_alert()
        if alert.sound_played:
            self.toast.show_message("Alarme de teste reproduzido. Ajuste o volume em Configurações.")

    def _test_sound(self, volume: float) -> None:
        if self._player is None or not self._player.play(SoundKind.DROWSINESS, volume):
            self._show_error("Áudio indisponível",
                             getattr(self._player, "last_error", "") or "Nenhuma saída de áudio disponível.")

    def _save_settings(self, settings: AppSettings) -> None:
        try:
            self._settings_manager.save(settings)
        except (SettingsError, OSError) as exc:
            self._show_error("Não foi possível salvar as configurações", str(exc))
            return
        self._controller.apply_settings(settings)
        self.monitor_page.apply_settings(settings)
        self.settings_page.mark_saved(settings)
        self.toast.show_message("Configurações salvas e aplicadas.")

    def _clear_history(self) -> None:
        self._go_to(1)
        self.history_page.confirm_clear()

    def _camera_in_use(self) -> int | None:
        c = self._controller
        if c.is_running and not c.using_video_file:
            return c.settings.camera.camera_index
        return None

    def show_startup_warnings(self, warnings: list[str]) -> None:
        if warnings:
            QMessageBox.warning(self, "Avisos de inicialização", "\n\n".join(warnings))

    def closeEvent(self, event: QCloseEvent) -> None:  # noqa: N802
        self._ticker.stop()
        self.settings_page.shutdown()
        self._controller.shutdown()
        event.accept()

    def sizeHint(self) -> QSize:  # noqa: N802
        return QSize(1360, 860)
