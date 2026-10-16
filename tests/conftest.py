"""Configuração comum dos testes."""

from __future__ import annotations

import os

# Permite criar widgets/objetos Qt sem janela (CI e terminais sem display).
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import pytest  # noqa: E402

from config.settings import AlertSettings, DetectionSettings  # noqa: E402


@pytest.fixture
def detection_settings() -> DetectionSettings:
    return DetectionSettings()


@pytest.fixture
def alert_settings() -> AlertSettings:
    return AlertSettings()


@pytest.fixture(scope="session")
def qapp():
    from PySide6.QtWidgets import QApplication

    app = QApplication.instance() or QApplication([])
    yield app
