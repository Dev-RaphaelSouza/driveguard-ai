"""Gera as imagens de docs/images usadas no README.

As capturas são da interface REAL (PySide6), mas com dados de DEMONSTRAÇÃO:
o vídeo é uma ilustração vetorial (nenhuma pessoa real) e as métricas/eventos
são sintéticos. Rodam em uma pasta temporária; seus dados não são tocados.

Uso (a partir da raiz do projeto):
    python scripts/make_screenshots.py
"""

from __future__ import annotations

import os
import shutil
import sys
import tempfile
import time
from dataclasses import replace
from datetime import timedelta
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "tests"))
OUT = ROOT / "docs" / "images"

from PySide6.QtCore import QPointF, QRectF, Qt  # noqa: E402
from PySide6.QtGui import QColor, QImage, QLinearGradient, QPainter, QPainterPath, QPen  # noqa: E402
from PySide6.QtWidgets import QApplication  # noqa: E402

from app.database import EventDatabase, EventType, utc_now  # noqa: E402
from app.drowsiness_analyzer import DriverState, Reason, SignalType  # noqa: E402
from app.main_window import MainWindow  # noqa: E402
from app.monitoring_controller import MonitoringController  # noqa: E402
from app.settings_manager import SettingsManager  # noqa: E402
from config.settings import AppSettings  # noqa: E402
from synthetic import result as base_result  # noqa: E402
from ui.styles import build_stylesheet, pick_fonts  # noqa: E402

GREEN, RED = QColor("#22C55E"), QColor("#EF4444")


def illustration(eyes_closed: bool = False, person: bool = True, size=(640, 480)) -> QImage:
    """Ilustração vetorial de um motorista (genérica, sem pessoa real)."""
    w, h = size
    img = QImage(w, h, QImage.Format.Format_RGB888)
    p = QPainter(img)
    p.setRenderHint(QPainter.RenderHint.Antialiasing)
    bg = QLinearGradient(0, 0, 0, h)
    bg.setColorAt(0, QColor("#16222F"))
    bg.setColorAt(1, QColor("#0C131B"))
    p.fillRect(0, 0, w, h, bg)
    # janela e encosto do banco
    p.setPen(Qt.PenStyle.NoPen)
    p.setBrush(QColor("#1C2B3B"))
    p.drawRoundedRect(QRectF(w * 0.06, h * 0.08, w * 0.22, h * 0.34), 10, 10)
    p.setBrush(QColor("#111B26"))
    p.drawRoundedRect(QRectF(w * 0.22, h * 0.46, w * 0.56, h * 0.7), 60, 60)
    if not person:  # cabine vazia (cenário "rosto não detectado")
        p.end()
        return img
    # ombros e pescoço
    p.setBrush(QColor("#2B3E54"))
    shoulders = QPainterPath()
    shoulders.moveTo(w * 0.16, h)
    shoulders.quadTo(w * 0.18, h * 0.74, w * 0.38, h * 0.70)
    shoulders.lineTo(w * 0.62, h * 0.70)
    shoulders.quadTo(w * 0.82, h * 0.74, w * 0.84, h)
    p.drawPath(shoulders)
    p.setBrush(QColor("#C9A58A"))
    p.drawRoundedRect(QRectF(w * 0.44, h * 0.58, w * 0.12, h * 0.16), 18, 18)
    # cabeça
    cx, cy = w / 2, h * 0.40
    p.setBrush(QColor("#D9B79B"))
    p.drawEllipse(QPointF(cx, cy), w * 0.135, h * 0.255)
    p.setBrush(QColor("#2A2321"))
    hair = QPainterPath()
    hair.addEllipse(QPointF(cx, cy - h * 0.10), w * 0.142, h * 0.19)
    mask = QPainterPath()
    mask.addRect(QRectF(0, cy - h * 0.05, w, h))
    p.drawPath(hair.subtracted(mask))
    # olhos
    for ex in (cx - w * 0.055, cx + w * 0.055):
        ey = cy - h * 0.01
        if eyes_closed:
            p.setPen(QPen(QColor("#3A2C28"), 3, Qt.PenStyle.SolidLine, Qt.PenCapStyle.RoundCap))
            lid = QPainterPath(QPointF(ex - 17, ey))
            lid.quadTo(QPointF(ex, ey + 9), QPointF(ex + 17, ey))
            p.drawPath(lid)
        else:
            p.setPen(Qt.PenStyle.NoPen)
            p.setBrush(QColor("#F4F1EC"))
            p.drawEllipse(QPointF(ex, ey), 17, 9)
            p.setBrush(QColor("#3B2A22"))
            p.drawEllipse(QPointF(ex, ey), 6.5, 6.5)
    # sobrancelhas, nariz e boca
    p.setPen(QPen(QColor("#2A2321"), 3.5, Qt.PenStyle.SolidLine, Qt.PenCapStyle.RoundCap))
    for ex in (cx - w * 0.055, cx + w * 0.055):
        p.drawLine(QPointF(ex - 18, cy - h * 0.065), QPointF(ex + 18, cy - h * 0.07))
    p.setPen(QPen(QColor("#B58B73"), 2.5, Qt.PenStyle.SolidLine, Qt.PenCapStyle.RoundCap))
    p.drawLine(QPointF(cx, cy + h * 0.005), QPointF(cx - 5, cy + h * 0.065))
    p.setPen(QPen(QColor("#9B5A55"), 3.5, Qt.PenStyle.SolidLine, Qt.PenCapStyle.RoundCap))
    mouth = QPainterPath(QPointF(cx - 22, cy + h * 0.125))
    mouth.quadTo(QPointF(cx, cy + h * 0.145), QPointF(cx + 22, cy + h * 0.125))
    p.drawPath(mouth)
    # sobreposição do detector (igual à da aplicação: olhos + cantos do rosto)
    color = RED if eyes_closed else GREEN
    p.setPen(QPen(color, 2))
    p.setBrush(Qt.BrushStyle.NoBrush)
    for ex in (cx - w * 0.055, cx + w * 0.055):
        p.drawEllipse(QPointF(ex, cy - h * 0.01), 20, 11 if not eyes_closed else 4)
    p.setPen(QPen(QColor("#2DD4BF"), 3))
    x0, y0, x1, y1 = cx - w * 0.17, cy - h * 0.30, cx + w * 0.17, cy + h * 0.27
    arm = 28
    for px, py, dx, dy in ((x0, y0, 1, 1), (x1, y0, -1, 1), (x0, y1, 1, -1), (x1, y1, -1, -1)):
        p.drawLine(QPointF(px, py), QPointF(px + dx * arm, py))
        p.drawLine(QPointF(px, py), QPointF(px, py + dy * arm))
    p.end()
    return img


def pump(app: QApplication, seconds: float) -> None:
    end = time.monotonic() + seconds
    while time.monotonic() < end:
        app.processEvents()
        time.sleep(0.005)


def feed(page, closed_at_end: bool, calibrated: bool = True, **extra) -> None:
    """Alimenta a tela com 20 s de EAR sintético e atualiza os blocos de métricas."""
    n = 100
    page.sparkline.clear()
    for i in range(n):
        closed = closed_at_end and i >= n - 9
        blink = i % 37 == 0
        ear = 0.09 if closed else (0.12 if blink else 0.29 + 0.01 * ((i * 7) % 5 - 2))
        r = replace(
            base_result(DriverState.DROWSY if closed_at_end else DriverState.NORMAL, 100.0 + i * 0.2),
            ear=ear, ear_threshold=0.18, eyes_closed=ear < 0.18,
            closure_duration=max(0.0, (i - (n - 9)) * 0.2) if closed else 0.0,
            calibrated=calibrated, perclos=extra.get("perclos", 0.06), blink_rate=extra.get("rate", 16.0),
            long_blinks=extra.get("long_blinks", 0), yawns=extra.get("yawns", 0), head_deviation=3.0,
            quality=0.93, reasons=extra.get("reasons", ()),
        )
        page.update_analysis(r)


def main() -> int:
    OUT.mkdir(parents=True, exist_ok=True)
    tmp = Path(tempfile.mkdtemp(prefix="driveguard_shots_"))
    app = QApplication([])
    app.setStyle("Fusion")
    app.setStyleSheet(build_stylesheet(*pick_fonts()))
    settings = AppSettings()
    db = EventDatabase(tmp / "demo.db")
    manager = SettingsManager(tmp / "settings.json")
    controller = MonitoringController(settings, db, None)
    win = MainWindow(controller, manager, db, tmp, None)
    win.setAttribute(Qt.WidgetAttribute.WA_DontShowOnScreen, True)
    win.resize(1440, 900)
    win.show()
    page = win.monitor_page

    def shot(name: str, widget=None) -> None:
        pump(app, 0.5)
        (widget or win).grab().save(str(OUT / f"{name}.png"))
        print("ok", name)

    # 1) Desligado
    shot("monitor-desligado")

    # 2) Normal
    page.set_running(True, "Câmera 0")
    page.set_state(DriverState.NORMAL)
    page.set_frame(illustration())
    page.set_stats(20.0, 12.0)
    feed(page, False, perclos=0.06, rate=16.0)
    win.timer_pill.set("00:12:34", "#2DD4BF")
    win.camera_pill.set("Câmera ativa", "#22C55E", "camera")
    shot("monitor-normal")

    # 3) Atenção
    att = (Reason(SignalType.ELEVATED_PERCLOS, DriverState.ATTENTION, "PERCLOS elevado (17%)"),
           Reason(SignalType.YAWNS, DriverState.ATTENTION, "3 bocejos recentes"))
    page.set_state(DriverState.ATTENTION)
    feed(page, False, perclos=0.17, rate=21.0, long_blinks=3, yawns=3, reasons=att)
    r = replace(base_result(DriverState.ATTENTION, 120.0), reasons=att, perclos=0.17, yawns=3,
                long_blinks=3, blink_rate=21.0, head_deviation=3.0, quality=0.93, calibrated=True)
    page.status.set_state(DriverState.ATTENTION, DriverState.ATTENTION.label,
                          DriverState.ATTENTION.description, [x.text for x in att])
    page.update_analysis(r)
    shot("monitor-atencao")

    # 4) Sonolência provável
    drowsy = (Reason(SignalType.LONG_CLOSURE, DriverState.DROWSY, "Olhos fechados há 1,8 s"),)
    page.set_frame(illustration(eyes_closed=True))
    page.set_state(DriverState.DROWSY)
    feed(page, True, perclos=0.21, rate=19.0, long_blinks=2, reasons=drowsy)
    page.update_analysis(replace(base_result(DriverState.DROWSY, 120.0), reasons=drowsy, eyes_closed=True,
                                 ear=0.09, closure_duration=1.8, perclos=0.21, blink_rate=19.0,
                                 long_blinks=2, head_deviation=3.0, quality=0.93, calibrated=True))
    win.alerts_pill.set("1 alerta", "#EF4444")
    shot("monitor-sonolencia")

    # 5) Rosto não detectado
    page.set_frame(illustration(person=False))
    page.set_state(DriverState.NO_FACE)
    nf = replace(base_result(DriverState.NO_FACE, 120.0, unreliable=7.0), face_reliable=False, ear=None,
                 eyes_closed=None, quality=0.0, perclos=0.06, blink_rate=None,
                 reasons=(Reason(SignalType.FACE_UNAVAILABLE, DriverState.NO_FACE,
                                 "Rosto não detectado pela câmera"),))
    page.update_analysis(nf)
    win.alerts_pill.set("1 alerta", "#EF4444")
    shot("monitor-rosto-ausente")

    # 6) Histórico (dados de demonstração)
    sid = db.start_session("Câmera 0", started_at=utc_now() - timedelta(minutes=95))
    demo = [
        (EventType.DROWSINESS, "Olhos fechados há 1,6 s", 6.2, {"ear": 0.09, "perclos": 0.21, "qualidade": 0.91}, 3),
        (EventType.LONG_CLOSURE, "Olhos fechados por 2,4 s", 2.4, {"ear": 0.08, "fechamento_s": 2.4}, 4),
        (EventType.ATTENTION, "PERCLOS elevado (17%); 3 bocejos recentes", 48.0, {"perclos": 0.17, "bocejos": 3}, 11),
        (EventType.YAWN, "Abertura da boca sustentada", 3.1, {"mar": 0.71, "bocejos": 2}, 17),
        (EventType.FACE_LOST, "Rosto não detectado pela câmera", 7.5, {}, 31),
        (EventType.ATTENTION, "Cabeça inclinada há 2,3 s", 12.0, {"desvio_cabeca_graus": 24.0, "qualidade": 0.88}, 52),
        (EventType.YAWN, "Abertura da boca sustentada", 2.4, {"mar": 0.64, "bocejos": 1}, 67),
        (EventType.CAMERA_FAILURE, "A câmera parou de enviar imagens.", None, {}, 88),
    ]
    for et, reason, dur, met, ago in demo:
        db.log_event(et, session_id=sid, reason=reason, duration=dur, metrics=met,
                     occurred_at=utc_now() - timedelta(minutes=ago))
    db.end_session(sid, 1)
    win._go_to(1)
    shot("historico")

    # 7) Configurações (janela alta para mostrar todos os cartões)
    win._go_to(2)
    win.resize(1440, 1560)
    pump(app, 0.3)
    shot("configuracoes")

    controller.shutdown()
    db.close()
    shutil.rmtree(tmp, ignore_errors=True)
    return 0


if __name__ == "__main__":
    sys.exit(main())
