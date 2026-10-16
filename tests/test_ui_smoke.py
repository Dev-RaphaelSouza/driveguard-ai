"""Teste de fumaça da interface completa (sem janela visível, sem webcam).

Exercita a janela real com o controlador real; apenas a câmera e o detector
são substituídos por falsos que simulam um motorista fechando os olhos.
"""

from __future__ import annotations

import json
import time

from synthetic import FakeEngine, FakePlayer, FakeSource

from app.alert_manager import DROWSINESS_MESSAGE
from app.database import EventDatabase, EventType
from app.drowsiness_analyzer import DriverState
from app.main_window import MainWindow
from app.monitoring_controller import MonitoringController
from app.settings_manager import SettingsManager
from config.settings import AppSettings


def pump(qapp, condition, timeout=8.0):
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        qapp.processEvents()
        if condition():
            return True
        time.sleep(0.01)
    return False


def test_main_window_full_session(qapp, tmp_path):
    settings = AppSettings()
    settings.detection.calibration_seconds = 2.0
    settings.camera.process_fps = 30
    db = EventDatabase(tmp_path / "driveguard.db")
    manager = SettingsManager(tmp_path / "settings.json")
    player = FakePlayer()
    controller = MonitoringController(
        settings, db, player,
        engine_factory=lambda s: FakeEngine(closed_from=3.0, closed_to=6.0),
        source_factory=lambda s: FakeSource(),
    )
    window = MainWindow(controller, manager, db, tmp_path, player)
    window.show()
    page = window.monitor_page

    # Sessão: inicia, recebe quadros, detecta o fechamento prolongado.
    controller.start()
    assert pump(qapp, lambda: page.video._image is not None)
    assert pump(qapp, lambda: controller.state == DriverState.DROWSY)
    assert page.banner.showing
    assert page.banner._title.text() == DROWSINESS_MESSAGE
    assert "SONOLÊNCIA" in window.windowTitle()
    assert window.alerts_pill._text.text() == "1 alerta"

    # Silenciar mantém o monitoramento ativo.
    window._toggle_mute()
    assert controller.muted and controller.is_running
    assert page.mute_button.isChecked()
    window._toggle_mute()
    assert not controller.muted

    controller.stop()
    assert not controller.is_running
    assert not page.start_button.property("running")

    # Histórico mostra o evento registrado.
    window._go_to(1)
    types = {r.event_type for r in window.history_page.model.records}
    assert EventType.DROWSINESS in types

    # Configurações: alterar, salvar e persistir.
    window._go_to(2)
    settings_page = window.settings_page
    assert not settings_page.save_button.isEnabled()
    settings_page.sensitivity.setValue(8)
    assert settings_page.save_button.isEnabled()
    settings_page._save()
    saved = json.loads((tmp_path / "settings.json").read_text(encoding="utf-8"))
    assert saved["detection"]["sensitivity"] == 8
    assert controller.settings.detection.sensitivity == 8
    assert not settings_page.save_button.isEnabled()

    window.close()
    db.close()


def test_privacy_mode_hides_video_but_keeps_analysis(qapp, tmp_path):
    settings = AppSettings()
    settings.privacy.show_preview = False
    db = EventDatabase(":memory:")
    controller = MonitoringController(settings, db, FakePlayer(),
                                      engine_factory=lambda s: FakeEngine(),
                                      source_factory=lambda s: FakeSource())
    window = MainWindow(controller, SettingsManager(tmp_path / "s.json"), db, tmp_path, FakePlayer())
    results = []
    controller.analysis_updated.connect(results.append)
    controller.start()
    assert pump(qapp, lambda: len(results) > 10)
    assert window.monitor_page.video._image is None   # nenhum quadro exibido
    controller.stop()
    window.close()
    db.close()
