"""Testes da política de alertas e da síntese de som."""

from __future__ import annotations

from dataclasses import replace

import numpy as np
import pytest
from synthetic import FakeClock, FakePlayer, result

from app.alert_manager import DROWSINESS_MESSAGE, AlertKind, AlertManager
from app.drowsiness_analyzer import DriverState
from app.sound import SoundKind, synthesize, to_wav_bytes

D = DriverState


def feed(manager, states_by_time, step=1 / 30):
    """Alimenta o manager com estados constantes por intervalos: [(duração, estado), ...]."""
    alerts, t = [], 0.0
    for duration, state in states_by_time:
        for _ in range(int(round(duration / step))):
            alerts += manager.process(result(state, t))
            t += step
    return alerts


def test_drowsiness_triggers_visual_and_sound_alert(alert_settings):
    player = FakePlayer()
    manager = AlertManager(alert_settings, player)
    alerts = manager.process(result(D.DROWSY, 10.0))

    assert len(alerts) == 1
    alert = alerts[0]
    assert alert.kind == AlertKind.DROWSINESS
    assert alert.message == DROWSINESS_MESSAGE
    assert alert.sound_played and alert.new_episode
    assert player.played == [(SoundKind.DROWSINESS, alert_settings.volume)]
    assert manager.drowsiness_count == 1


def test_sound_is_not_repeated_every_frame(alert_settings):
    player = FakePlayer()
    manager = AlertManager(replace(alert_settings, alert_interval_seconds=8.0), player)
    alerts = feed(manager, [(20.0, D.DROWSY)])

    # 20 s contínuos com intervalo de 8 s → sons em t=0, 8 e 16.
    assert len(player.played) == 3
    assert len(alerts) == 3
    assert sum(a.new_episode for a in alerts) == 1
    assert manager.drowsiness_count == 1


def test_minimum_interval_between_episodes(alert_settings):
    player = FakePlayer()
    manager = AlertManager(replace(alert_settings, alert_interval_seconds=10.0), player)
    alerts = feed(manager, [(1.0, D.DROWSY), (3.0, D.NORMAL), (1.0, D.DROWSY)])

    assert manager.drowsiness_count == 2           # dois episódios registrados
    assert [a.new_episode for a in alerts] == [True, True]
    assert len(player.played) == 1                 # mas só um som (intervalo mínimo)
    assert alerts[1].sound_played is False


def test_mute_suppresses_sound_but_not_alerts(alert_settings):
    clock = FakeClock(100.0)
    player = FakePlayer()
    manager = AlertManager(alert_settings, player, clock=clock)
    manager.mute(minutes=5)

    assert manager.muted
    assert manager.mute_remaining == pytest.approx(300.0)
    alerts = manager.process(result(D.DROWSY, 100.0))
    assert alerts and alerts[0].sound_played is False
    assert player.played == []
    assert manager.drowsiness_count == 1

    clock.now = 401.0                               # silêncio expirou
    assert not manager.muted
    alerts = manager.process(result(D.DROWSY, 401.0))
    assert alerts[0].sound_played
    assert len(player.played) == 1


def test_mute_stops_current_sound_and_unmute(alert_settings):
    player = FakePlayer()
    manager = AlertManager(alert_settings, player, clock=FakeClock())
    manager.mute()
    assert player.stopped == 1
    manager.unmute()
    assert not manager.muted


def test_sound_can_be_disabled(alert_settings):
    player = FakePlayer()
    manager = AlertManager(replace(alert_settings, sound_enabled=False), player)
    alerts = manager.process(result(D.DROWSY, 0.0))
    assert alerts[0].kind == AlertKind.DROWSINESS
    assert player.played == []


def test_volume_setting_is_applied(alert_settings):
    player = FakePlayer()
    manager = AlertManager(replace(alert_settings, volume=0.3), player)
    manager.process(result(D.DROWSY, 0.0))
    assert player.played[0][1] == pytest.approx(0.3)


def test_unavailable_audio_still_produces_visual_alert(alert_settings):
    manager = AlertManager(alert_settings, FakePlayer(available=False))
    alerts = manager.process(result(D.DROWSY, 0.0))
    assert alerts[0].kind == AlertKind.DROWSINESS and not alerts[0].sound_played


def test_face_lost_alert_is_distinct_and_delayed(alert_settings):
    player = FakePlayer()
    settings = replace(alert_settings, face_lost_alert_seconds=5.0)
    manager = AlertManager(settings, player)

    assert manager.process(result(D.NO_FACE, 1.0, unreliable=2.0)) == []
    alerts = manager.process(result(D.NO_FACE, 4.0, unreliable=5.0))
    assert [a.kind for a in alerts] == [AlertKind.FACE_LOST]
    assert player.played[0][0] == SoundKind.MONITORING
    assert manager.drowsiness_count == 0            # não é alerta de sonolência

    # Mesmo episódio: não alerta de novo antes do intervalo.
    assert manager.process(result(D.NO_FACE, 5.0, unreliable=6.0)) == []


def test_attention_alert_once_per_episode_without_sound_by_default(alert_settings):
    player = FakePlayer()
    manager = AlertManager(alert_settings, player)
    alerts = feed(manager, [(5.0, D.ATTENTION)])
    assert [a.kind for a in alerts] == [AlertKind.ATTENTION]
    assert player.played == []

    manager = AlertManager(replace(alert_settings, attention_sound=True), player)
    manager.process(result(D.ATTENTION, 0.0))
    assert player.played == [(SoundKind.ATTENTION, alert_settings.volume)]


def test_camera_failure_alerts_once_until_recovered(alert_settings):
    player = FakePlayer()
    manager = AlertManager(alert_settings, player, clock=FakeClock())
    first = manager.camera_failure("Câmera desconectada")
    assert first is not None and first.kind == AlertKind.CAMERA_FAILURE
    assert manager.camera_failure("Câmera desconectada") is None
    manager.camera_recovered()
    assert manager.camera_failure("De novo") is not None
    assert [k for k, _ in player.played] == [SoundKind.MONITORING, SoundKind.MONITORING]


def test_test_alert_plays_even_when_muted(alert_settings):
    player = FakePlayer()
    manager = AlertManager(alert_settings, player, clock=FakeClock())
    manager.mute()
    alert = manager.test()
    assert alert.kind == AlertKind.TEST and alert.sound_played


def test_reset_clears_session_counters(alert_settings):
    manager = AlertManager(alert_settings, FakePlayer())
    manager.process(result(D.DROWSY, 0.0))
    manager.reset()
    assert manager.drowsiness_count == 0


# --------------------------------------------------------------------------- #
# Síntese de som
# --------------------------------------------------------------------------- #
@pytest.mark.parametrize("kind", list(SoundKind))
def test_synthesized_sounds_are_valid(kind):
    samples = synthesize(kind)
    assert samples.dtype == np.float32
    assert samples.ndim == 1
    assert 0.3 < len(samples) / 44_100 < 3.0
    assert np.max(np.abs(samples)) == pytest.approx(0.9, abs=1e-3)
    assert abs(float(samples[0])) < 0.05             # rampa de entrada evita estalo


def test_alarm_is_longer_and_louder_than_attention_chime():
    alarm, chime = synthesize(SoundKind.DROWSINESS), synthesize(SoundKind.ATTENTION)
    rms = lambda s: float(np.sqrt(np.mean(s.astype(np.float64) ** 2)))  # noqa: E731
    assert len(alarm) > len(chime)
    assert rms(alarm) > 2 * rms(chime)


def test_wav_export_has_riff_header():
    data = to_wav_bytes(synthesize(SoundKind.MONITORING))
    assert data[:4] == b"RIFF" and data[8:12] == b"WAVE"
