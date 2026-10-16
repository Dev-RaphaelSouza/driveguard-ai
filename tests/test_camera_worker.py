"""Testes da thread de captura e do controlador, sem webcam real.

Fontes e detectores falsos simulam câmera indisponível, desconexão no meio da
sessão, modelo ausente e um motorista que fecha os olhos.
"""

from __future__ import annotations

import time

import pytest
from PySide6.QtCore import Qt
from synthetic import FakeEngine, FakePlayer, FakeSource

import app.camera_worker as camera_worker
from app.camera_worker import CameraStatus, CameraWorker, FrameBudget
from app.database import EventDatabase, EventType
from app.detection_engine import ModelNotFoundError
from app.drowsiness_analyzer import DriverState
from app.monitoring_controller import MonitoringController
from config.settings import AppSettings


def wait_until(qapp, condition, timeout=5.0):
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        qapp.processEvents()
        if condition():
            return True
        time.sleep(0.01)
    return False


def collect(worker):
    """Conecta os sinais diretamente (sem event loop) e acumula as emissões."""
    got = {"status": [], "results": [], "fatal": [], "frames": 0}
    direct = Qt.ConnectionType.DirectConnection
    worker.status_changed.connect(lambda s, m: got["status"].append((s, m)), direct)
    worker.analysis_ready.connect(lambda r: got["results"].append(r), direct)
    worker.fatal_error.connect(lambda m: got["fatal"].append(m), direct)
    return got


def fast_settings():
    settings = AppSettings()
    settings.camera.reconnect_seconds = 1.0
    settings.camera.process_fps = 30
    settings.detection.calibration_seconds = 2.0
    settings.privacy.show_preview = False
    return settings


# --------------------------------------------------------------------------- #
# CameraWorker
# --------------------------------------------------------------------------- #
def test_worker_processes_frames_and_releases_resources(qapp):
    source, engine = FakeSource(), FakeEngine()
    worker = CameraWorker(lambda s: source, fast_settings(), lambda s: engine)
    got = collect(worker)
    worker.start()
    assert wait_until(qapp, lambda: len(got["results"]) >= 10)
    worker.request_stop()
    assert worker.wait(3000)

    assert got["status"][0][0] == CameraStatus.OPENING
    assert (CameraStatus.RUNNING, "Fonte falsa") in got["status"]
    assert got["status"][-1][0] == CameraStatus.STOPPED
    assert source.closed >= 1 and engine.closed
    assert all(r.state in (DriverState.NORMAL, DriverState.STARTING) for r in got["results"])


def test_worker_reports_unavailable_camera_and_retries(qapp):
    source = FakeSource(fail_open=True)
    worker = CameraWorker(lambda s: source, fast_settings(), lambda s: FakeEngine())
    got = collect(worker)
    worker.start()
    assert wait_until(qapp, lambda: source.opened >= 2, timeout=4.0)
    worker.request_stop()
    assert worker.wait(3000)

    errors = [m for s, m in got["status"] if s == CameraStatus.ERROR]
    assert errors and errors[0] == "Câmera falsa indisponível"
    assert got["results"] == []


def test_worker_detects_disconnection_and_reconnects(qapp, monkeypatch):
    monkeypatch.setattr(camera_worker, "FRAME_TIMEOUT_SECONDS", 0.2)
    source = FakeSource(frames_before_disconnect=5)
    worker = CameraWorker(lambda s: source, fast_settings(), lambda s: FakeEngine())
    got = collect(worker)
    worker.start()
    assert wait_until(qapp, lambda: source.opened >= 2, timeout=4.0)
    worker.request_stop()
    assert worker.wait(3000)

    statuses = [s for s, _ in got["status"]]
    assert CameraStatus.ERROR in statuses
    assert statuses.index(CameraStatus.ERROR) > statuses.index(CameraStatus.RUNNING)
    assert source.closed >= 1


def test_missing_model_is_reported_as_fatal(qapp):
    def factory(_settings):
        raise ModelNotFoundError("Modelo não encontrado. Execute 'python scripts/download_model.py'.")

    worker = CameraWorker(lambda s: FakeSource(), fast_settings(), factory)
    got = collect(worker)
    worker.start()
    assert worker.wait(3000)
    assert got["fatal"] and "download_model" in got["fatal"][0]


def test_worker_detects_long_closure_end_to_end(qapp):
    engine = FakeEngine(closed_from=3.0, closed_to=6.0)
    worker = CameraWorker(lambda s: FakeSource(), fast_settings(), lambda s: engine)
    got = collect(worker)
    worker.start()
    assert wait_until(qapp, lambda: any(r.state == DriverState.DROWSY for r in got["results"]), timeout=8.0)
    worker.request_stop()
    assert worker.wait(3000)


def test_frame_budget_limits_frames_in_flight():
    budget = FrameBudget(limit=2)
    assert budget.try_acquire() and budget.try_acquire()
    assert not budget.try_acquire()
    budget.release()
    assert budget.try_acquire()


# --------------------------------------------------------------------------- #
# MonitoringController
# --------------------------------------------------------------------------- #
@pytest.fixture
def db():
    database = EventDatabase(":memory:")
    yield database
    database.close()


def make_controller(db, source, engine, player=None):
    return MonitoringController(fast_settings(), db, player or FakePlayer(),
                                engine_factory=lambda s: engine, source_factory=lambda s: source)


def test_controller_session_lifecycle_and_alert_logging(qapp, db):
    player = FakePlayer()
    controller = make_controller(db, FakeSource(), FakeEngine(closed_from=3.0, closed_to=6.0), player)
    states, alerts = [], []
    controller.state_changed.connect(states.append)
    controller.alert_raised.connect(alerts.append)

    controller.start()
    assert controller.is_running
    assert wait_until(qapp, lambda: DriverState.DROWSY in states, timeout=8.0)
    controller.stop()
    assert not controller.is_running

    assert states[0] == DriverState.STARTING and states[-1] is None
    assert any(a.kind.value == "DROWSINESS" for a in alerts)
    assert controller.alerts_count == 1
    assert player.played, "o alarme sonoro deveria ter sido acionado"
    events = db.query_events(types=[EventType.DROWSINESS])
    assert len(events) == 1
    assert events[0].duration is not None and events[0].duration > 0
    assert "Olhos fechados" in events[0].reason
    summary = db.summary()
    assert summary.sessions == 1 and summary.monitored_seconds > 0


def test_controller_reports_camera_unavailable(qapp, db):
    controller = make_controller(db, FakeSource(fail_open=True), FakeEngine())
    states = []
    controller.state_changed.connect(states.append)
    controller.start()
    assert wait_until(qapp, lambda: DriverState.CAMERA_UNAVAILABLE in states)
    controller.stop()

    failures = db.query_events(types=[EventType.CAMERA_FAILURE])
    assert len(failures) == 1                      # um registro por episódio de falha
    assert DriverState.NORMAL not in states        # falha nunca é exibida como normal


def test_controller_stops_on_fatal_error(qapp, db):
    def failing(_settings):
        raise ModelNotFoundError("modelo ausente")

    controller = MonitoringController(fast_settings(), db, FakePlayer(), engine_factory=failing,
                                      source_factory=lambda s: FakeSource())
    errors = []
    controller.error_occurred.connect(lambda title, detail: errors.append(detail))
    controller.start()
    assert wait_until(qapp, lambda: bool(errors))
    assert not controller.is_running
    assert errors == ["modelo ausente"]


def test_privacy_option_disables_metrics_storage(qapp, db):
    settings = fast_settings()
    settings.privacy.store_event_metrics = False
    controller = MonitoringController(settings, db, FakePlayer(),
                                      engine_factory=lambda s: FakeEngine(closed_from=3.0, closed_to=6.0),
                                      source_factory=lambda s: FakeSource())
    states = []
    controller.state_changed.connect(states.append)
    controller.start()
    assert wait_until(qapp, lambda: DriverState.DROWSY in states, timeout=8.0)
    controller.stop()
    assert all(e.metrics == {} for e in db.query_events())
