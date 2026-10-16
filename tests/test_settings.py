"""Testes de validação e persistência das configurações."""

from __future__ import annotations

import json
from dataclasses import replace

import pytest

from app.settings_manager import SettingsError, SettingsManager
from config.settings import (
    AppSettings,
    DetectionSettings,
    field_meta,
    validate_settings,
)


def test_defaults_are_valid():
    assert validate_settings(AppSettings()) == []


@pytest.mark.parametrize("field,value", [
    ("sensitivity", 0),
    ("sensitivity", 11),
    ("long_closure_seconds", 0.1),
    ("perclos_window_seconds", 1000.0),
    ("min_quality", 1.5),
])
def test_out_of_range_values_are_rejected(field, value):
    settings = AppSettings(detection=replace(DetectionSettings(), **{field: value}))
    errors = validate_settings(settings)
    assert errors and "fora do intervalo" in errors[0]


def test_wrong_types_are_rejected():
    settings = AppSettings()
    settings.alerts.sound_enabled = "sim"  # type: ignore[assignment]
    settings.camera.camera_index = 1.5     # type: ignore[assignment]
    errors = validate_settings(settings)
    assert len(errors) == 2


def test_cross_field_rules():
    det = replace(DetectionSettings(), perclos_attention=0.4, perclos_drowsy=0.3)
    assert any("PERCLOS" in e for e in validate_settings(AppSettings(detection=det)))
    det = replace(DetectionSettings(), long_blink_seconds=1.0, long_closure_seconds=0.8)
    assert any("piscada longa" in e for e in validate_settings(AppSettings(detection=det)))


def test_sensitivity_maps_to_closed_ratio():
    ratios = [replace(DetectionSettings(), sensitivity=s).closed_ratio() for s in range(1, 11)]
    assert ratios == sorted(ratios)
    assert ratios[0] == pytest.approx(0.45)
    assert ratios[-1] == pytest.approx(0.72)


def test_field_metadata_available_for_ui():
    meta = field_meta(DetectionSettings, "long_closure_seconds")
    assert meta["label"] and meta["unit"] == "s"
    assert meta["min"] < DetectionSettings().long_closure_seconds < meta["max"]


def test_save_and_load_roundtrip(tmp_path):
    manager = SettingsManager(tmp_path / "settings.json")
    settings = AppSettings()
    settings.detection.sensitivity = 8
    settings.alerts.volume = 0.4
    settings.camera.backend = "msmf"
    manager.save(settings)

    loaded = manager.load()
    assert loaded == settings
    assert manager.warnings == []
    data = json.loads((tmp_path / "settings.json").read_text(encoding="utf-8"))
    assert data["schema_version"] == 1


def test_missing_file_returns_defaults(tmp_path):
    assert SettingsManager(tmp_path / "nao_existe.json").load() == AppSettings()


def test_corrupted_file_is_preserved_and_defaults_used(tmp_path):
    path = tmp_path / "settings.json"
    path.write_text("{ isto não é json", encoding="utf-8")
    manager = SettingsManager(path)
    assert manager.load() == AppSettings()
    assert manager.warnings
    assert (tmp_path / "settings.json.corrompido").exists()


def test_invalid_values_in_file_fall_back_individually(tmp_path):
    path = tmp_path / "settings.json"
    path.write_text(json.dumps({
        "detection": {"sensitivity": 99, "long_closure_seconds": 2.0, "desconhecido": 1},
        "alerts": {"volume": "alto"},
        "camera": {"backend": "inexistente", "process_fps": 15},
        "privacy": [],
    }), encoding="utf-8")
    manager = SettingsManager(path)
    settings = manager.load()

    assert settings.detection.sensitivity == DetectionSettings().sensitivity
    assert settings.detection.long_closure_seconds == 2.0      # valor válido mantido
    assert settings.alerts.volume == AppSettings().alerts.volume
    assert settings.camera.backend == "dshow"
    assert settings.camera.process_fps == 15
    assert len(manager.warnings) == 4


def test_integer_values_accepted_for_float_fields(tmp_path):
    path = tmp_path / "settings.json"
    path.write_text(json.dumps({"detection": {"long_closure_seconds": 2}}), encoding="utf-8")
    settings = SettingsManager(path).load()
    assert settings.detection.long_closure_seconds == 2.0
    assert isinstance(settings.detection.long_closure_seconds, float)


def test_save_rejects_invalid_settings(tmp_path):
    manager = SettingsManager(tmp_path / "settings.json")
    settings = AppSettings()
    settings.detection.sensitivity = 50
    with pytest.raises(SettingsError):
        manager.save(settings)
    assert not (tmp_path / "settings.json").exists()


def test_reset_restores_defaults(tmp_path):
    manager = SettingsManager(tmp_path / "settings.json")
    custom = AppSettings()
    custom.alerts.volume = 0.1
    manager.save(custom)
    assert manager.reset() == AppSettings()
    assert manager.load() == AppSettings()
