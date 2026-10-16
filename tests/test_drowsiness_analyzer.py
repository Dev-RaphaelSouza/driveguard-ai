"""Testes do analisador temporal com cenários sintéticos."""

from __future__ import annotations

from dataclasses import replace

import pytest
from synthetic import blinking, closed_eyes, no_face, open_eyes, run

from app.drowsiness_analyzer import (
    DriverState,
    DrowsinessAnalyzer,
    EventKind,
    SignalType,
    _hysteresis_level,
)

CALIBRATION = [open_eyes(8.0)]  # tempo suficiente para a calibração padrão (5 s)


def first_time(results, state):
    return next((r.timestamp for r in results if r.state == state), None)


def events(results, kind):
    return [e for r in results for e in r.events if e.kind == kind]


def signals(results):
    return {reason.signal for r in results for reason in r.reasons}


# --------------------------------------------------------------------------- #
# Comportamento normal
# --------------------------------------------------------------------------- #
def test_starts_in_starting_state(detection_settings):
    analyzer = DrowsinessAnalyzer(detection_settings)
    results = run(analyzer, [no_face(0.5)])
    assert all(r.state == DriverState.STARTING for r in results)


def test_normal_blinking_never_alerts(detection_settings):
    analyzer = DrowsinessAnalyzer(detection_settings)
    results = run(analyzer, blinking(120.0, period=4.0, blink=0.15))

    states = {r.state for r in results[1:]}
    assert states == {DriverState.NORMAL}
    blinks = events(results, EventKind.BLINK)
    assert 28 <= len(blinks) <= 30
    assert results[-1].blink_rate == pytest.approx(15.0, abs=1.5)
    assert results[-1].perclos == pytest.approx(0.15 / 4.0, abs=0.01)


def test_short_closure_is_not_drowsiness(detection_settings):
    analyzer = DrowsinessAnalyzer(detection_settings)
    results = run(analyzer, CALIBRATION + [closed_eyes(1.0), open_eyes(5.0)])
    assert DriverState.DROWSY not in {r.state for r in results}
    assert len(events(results, EventKind.LONG_BLINK)) == 1


# --------------------------------------------------------------------------- #
# Fechamento ocular prolongado, histerese e recuperação
# --------------------------------------------------------------------------- #
def test_long_closure_triggers_drowsiness(detection_settings):
    analyzer = DrowsinessAnalyzer(detection_settings)
    results = run(analyzer, CALIBRATION + [closed_eyes(2.0), open_eyes(10.0)])

    onset = first_time(results, DriverState.DROWSY)
    assert onset == pytest.approx(8.0 + detection_settings.long_closure_seconds, abs=0.05)
    drowsy = next(r for r in results if r.state == DriverState.DROWSY)
    assert drowsy.reasons[0].signal == SignalType.LONG_CLOSURE

    closures = events(results, EventKind.LONG_CLOSURE)
    assert len(closures) == 1
    assert closures[0].duration == pytest.approx(2.0, abs=0.05)


def test_recovery_requires_sustained_normal_signals(detection_settings):
    analyzer = DrowsinessAnalyzer(detection_settings)
    results = run(analyzer, CALIBRATION + [closed_eyes(2.0), open_eyes(10.0)])
    reopen_at = 10.0
    recovery = detection_settings.recovery_seconds

    before = [r for r in results if reopen_at + 0.1 < r.timestamp < reopen_at + recovery - 0.1]
    after = [r for r in results if r.timestamp > reopen_at + recovery + 0.1]
    assert before and all(r.state == DriverState.DROWSY for r in before)
    assert before[-1].recovering_for is not None
    assert after and all(r.state == DriverState.NORMAL for r in after)


def test_detection_is_independent_of_frame_rate(detection_settings):
    onsets = []
    for fps in (12.0, 15.0, 30.0):
        analyzer = DrowsinessAnalyzer(detection_settings)
        results = run(analyzer, CALIBRATION + [closed_eyes(2.5), open_eyes(1.0)], fps=fps)
        onsets.append(first_time(results, DriverState.DROWSY))
    assert None not in onsets
    assert max(onsets) - min(onsets) <= 1.0 / 12.0 + 1e-6


def test_custom_closure_duration_is_respected(detection_settings):
    settings = replace(detection_settings, long_closure_seconds=3.0)
    analyzer = DrowsinessAnalyzer(settings)
    results = run(analyzer, CALIBRATION + [closed_eyes(2.5), open_eyes(2.0)])
    assert DriverState.DROWSY not in {r.state for r in results}


# --------------------------------------------------------------------------- #
# Ausência de rosto e qualidade
# --------------------------------------------------------------------------- #
def test_short_face_dropout_is_tolerated(detection_settings):
    analyzer = DrowsinessAnalyzer(detection_settings)
    results = run(analyzer, CALIBRATION + [no_face(0.5), open_eyes(2.0)])
    assert DriverState.NO_FACE not in {r.state for r in results}
    assert not events(results, EventKind.FACE_LOST)


def test_face_lost_after_grace_period(detection_settings):
    analyzer = DrowsinessAnalyzer(detection_settings)
    results = run(analyzer, CALIBRATION + [no_face(3.0), open_eyes(2.0)])

    onset = first_time(results, DriverState.NO_FACE)
    assert onset == pytest.approx(8.0 + detection_settings.face_lost_grace_seconds, abs=0.05)
    lost = next(r for r in results if r.state == DriverState.NO_FACE)
    assert lost.reasons[0].signal == SignalType.FACE_UNAVAILABLE
    assert lost.state != DriverState.NORMAL

    recovered = events(results, EventKind.FACE_RECOVERED)
    assert len(recovered) == 1
    assert recovered[0].duration == pytest.approx(3.0, abs=0.05)
    assert results[-1].state == DriverState.NORMAL


def test_face_loss_resets_closure_in_progress(detection_settings):
    analyzer = DrowsinessAnalyzer(detection_settings)
    results = run(analyzer, CALIBRATION + [closed_eyes(1.0), no_face(1.5), closed_eyes(1.0), open_eyes(1.0)])
    assert DriverState.DROWSY not in {r.state for r in results}


def test_brief_dropout_does_not_split_a_long_closure(detection_settings):
    analyzer = DrowsinessAnalyzer(detection_settings)
    results = run(analyzer, CALIBRATION + [closed_eyes(0.9), no_face(0.3), closed_eyes(0.9), open_eyes(1.0)])
    assert DriverState.DROWSY in {r.state for r in results}


def test_low_quality_frames_do_not_feed_the_analysis(detection_settings):
    analyzer = DrowsinessAnalyzer(detection_settings)
    results = run(analyzer, CALIBRATION + [closed_eyes(3.0, quality=0.2)])

    assert DriverState.DROWSY not in {r.state for r in results}
    assert results[-1].state == DriverState.NO_FACE
    assert "pouco confiável" in results[-1].reasons[0].text


# --------------------------------------------------------------------------- #
# Calibração
# --------------------------------------------------------------------------- #
def test_calibration_adapts_threshold_to_user(detection_settings):
    # Usuário com olhos naturalmente mais fechados (EAR 0,24).
    calibrated = DrowsinessAnalyzer(detection_settings)
    results = run(calibrated, [open_eyes(8.0, ear=0.24), open_eyes(3.0, ear=0.17)])
    assert results[-1].calibrated
    assert results[-1].baseline_ear == pytest.approx(0.24, abs=1e-6)
    assert DriverState.DROWSY not in {r.state for r in results}

    # Sem calibração, o mesmo usuário seria considerado de olhos fechados.
    manual = DrowsinessAnalyzer(replace(detection_settings, auto_calibration=False))
    results = run(manual, [open_eyes(8.0, ear=0.24), open_eyes(3.0, ear=0.17)])
    assert DriverState.DROWSY in {r.state for r in results}


def test_implausible_calibration_falls_back_to_manual(detection_settings):
    analyzer = DrowsinessAnalyzer(detection_settings)
    results = run(analyzer, [open_eyes(8.0, ear=0.60)])
    assert events(results, EventKind.CALIBRATION_FAILED)
    assert not results[-1].calibrated
    assert results[-1].baseline_ear == detection_settings.manual_baseline_ear


def test_recalibrate_restarts_calibration(detection_settings):
    analyzer = DrowsinessAnalyzer(detection_settings)
    results = run(analyzer, CALIBRATION)
    assert results[-1].calibrated
    analyzer.recalibrate()
    results = run(analyzer, [open_eyes(1.0)], start=8.0)
    assert results[-1].calibrating and not results[-1].calibrated


def test_sensitivity_change_applies_without_losing_calibration(detection_settings):
    analyzer = DrowsinessAnalyzer(detection_settings)
    run(analyzer, CALIBRATION)
    before = analyzer.ear_threshold
    analyzer.update_settings(replace(detection_settings, sensitivity=10))
    assert analyzer.ear_threshold > before
    assert analyzer.ear_threshold == pytest.approx(0.30 * 0.72, abs=1e-6)


# --------------------------------------------------------------------------- #
# PERCLOS, bocejos, cabeça e combinação
# --------------------------------------------------------------------------- #
def test_elevated_perclos_leads_to_attention(detection_settings):
    analyzer = DrowsinessAnalyzer(detection_settings)
    # Fechamentos de 0,4 s (abaixo de "piscada longa") a cada 2 s → PERCLOS ≈ 20 %.
    results = run(analyzer, blinking(90.0, period=2.0, blink=0.4))

    assert results[-1].perclos == pytest.approx(0.20, abs=0.02)
    assert DriverState.ATTENTION in {r.state for r in results}
    assert DriverState.DROWSY not in {r.state for r in results}
    # PERCLOS só é avaliado com cobertura mínima (metade da janela).
    onset = first_time(results, DriverState.ATTENTION)
    assert onset >= 0.5 * detection_settings.perclos_window_seconds
    assert SignalType.ELEVATED_PERCLOS in signals(results)


def test_high_perclos_leads_to_drowsiness(detection_settings):
    analyzer = DrowsinessAnalyzer(detection_settings)
    results = run(analyzer, blinking(60.0, period=1.2, blink=0.4))
    assert DriverState.DROWSY in {r.state for r in results}
    assert SignalType.HIGH_PERCLOS in signals(results)


def _yawns(count: int, gap: float = 12.0) -> list:
    segments = []
    for _ in range(count):
        segments += [open_eyes(3.0, mar=0.8), open_eyes(gap)]
    return segments


def test_yawns_alone_only_raise_attention(detection_settings):
    analyzer = DrowsinessAnalyzer(detection_settings)
    results = run(analyzer, CALIBRATION + _yawns(3))

    assert len(events(results, EventKind.YAWN)) == 3
    assert results[-1].yawns == 3
    states = {r.state for r in results}
    assert DriverState.ATTENTION in states
    assert DriverState.DROWSY not in states


def test_brief_mouth_opening_is_not_a_yawn(detection_settings):
    analyzer = DrowsinessAnalyzer(detection_settings)
    results = run(analyzer, CALIBRATION + [open_eyes(0.8, mar=0.8), open_eyes(3.0)])
    assert not events(results, EventKind.YAWN)


def test_sustained_head_tilt_raises_attention(detection_settings):
    analyzer = DrowsinessAnalyzer(detection_settings)
    results = run(analyzer, CALIBRATION + [open_eyes(6.0, pitch=30.0)])
    assert results[-1].state == DriverState.ATTENTION
    assert SignalType.HEAD_TILT in signals(results)
    assert results[-1].head_deviation == pytest.approx(30.0, abs=1e-6)


def test_head_tilt_is_relative_to_calibrated_posture(detection_settings):
    # Câmera montada abaixo do rosto: pitch constante de 25° é a postura normal.
    analyzer = DrowsinessAnalyzer(detection_settings)
    results = run(analyzer, [open_eyes(14.0, pitch=25.0)])
    assert DriverState.ATTENTION not in {r.state for r in results}


def test_combined_signals_escalate_to_drowsiness(detection_settings):
    analyzer = DrowsinessAnalyzer(detection_settings)
    results = run(analyzer, CALIBRATION + _yawns(3) + [open_eyes(8.0, pitch=30.0)])
    assert DriverState.DROWSY in {r.state for r in results}
    assert SignalType.COMBINED in signals(results)

    no_combo = DrowsinessAnalyzer(replace(detection_settings, combine_signals=False))
    results = run(no_combo, CALIBRATION + _yawns(3) + [open_eyes(8.0, pitch=30.0)])
    assert DriverState.DROWSY not in {r.state for r in results}


def test_attention_requires_confirmation_period(detection_settings):
    settings = replace(detection_settings, confirmation_seconds=4.0)
    analyzer = DrowsinessAnalyzer(settings)
    results = run(analyzer, CALIBRATION + [open_eyes(5.0, pitch=30.0)])
    # inclinação ativa após 2 s + 4 s de confirmação > 5 s observados
    assert DriverState.ATTENTION not in {r.state for r in results}


def test_hysteresis_level_helper():
    thresholds = (0.15, 0.30)
    assert _hysteresis_level(0.10, thresholds, 0) == 0
    assert _hysteresis_level(0.16, thresholds, 0) == 1
    assert _hysteresis_level(0.14, thresholds, 1) == 1   # acima do limiar de saída (0,1275)
    assert _hysteresis_level(0.12, thresholds, 1) == 0
    assert _hysteresis_level(0.27, thresholds, 2) == 2   # acima de 0,255
    assert _hysteresis_level(0.24, thresholds, 2) == 1
