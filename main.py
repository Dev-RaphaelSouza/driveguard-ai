"""Ponto de entrada do DriveGuard AI.

Uso:
    python main.py                    # interface gráfica com a webcam configurada
    python main.py --camera 1         # força outra câmera nesta execução
    python main.py --video teste.mp4  # analisa um arquivo de vídeo/imagem (testes)
    python main.py --autostart        # já abre monitorando
    python main.py --check            # diagnóstico do ambiente, sem interface
"""

from __future__ import annotations

import argparse
import logging
import os
import platform
import sys
import time
from logging.handlers import RotatingFileHandler
from pathlib import Path

from config.settings import APP_NAME, APP_VERSION, MODEL_PATH, data_dir

log = logging.getLogger("driveguard")


def parse_args(argv: list[str] | None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(prog="driveguard", description=f"{APP_NAME} {APP_VERSION}")
    parser.add_argument("--camera", type=int, metavar="N", help="índice da câmera para esta execução")
    parser.add_argument("--video", type=Path, metavar="ARQUIVO",
                        help="usa um arquivo de vídeo ou imagem no lugar da webcam (testes)")
    parser.add_argument("--data-dir", type=Path, metavar="PASTA",
                        help="pasta para banco, configurações e logs")
    parser.add_argument("--autostart", action="store_true", help="inicia o monitoramento ao abrir")
    parser.add_argument("--check", action="store_true", help="executa o diagnóstico e sai")
    parser.add_argument("--debug", action="store_true", help="log detalhado")
    return parser.parse_args(argv)


def setup_logging(data_path: Path, debug: bool) -> None:
    data_path.mkdir(parents=True, exist_ok=True)
    level = logging.DEBUG if debug else logging.INFO
    fmt = logging.Formatter("%(asctime)s %(levelname)-7s %(name)s: %(message)s")
    file_handler = RotatingFileHandler(data_path / "driveguard.log", maxBytes=1_000_000,
                                       backupCount=2, encoding="utf-8")
    file_handler.setFormatter(fmt)
    handlers: list[logging.Handler] = [file_handler]
    if sys.stderr is not None:  # executável sem console (PyInstaller) não tem stderr
        console = logging.StreamHandler()
        console.setFormatter(fmt)
        handlers.append(console)
    logging.basicConfig(level=level, handlers=handlers, force=True)

    def excepthook(exc_type, exc, tb):  # exceções em slots Qt não derrubam a aplicação
        log.critical("Exceção não tratada", exc_info=(exc_type, exc, tb))

    sys.excepthook = excepthook


# --------------------------------------------------------------------------- #
# Diagnóstico
# --------------------------------------------------------------------------- #
def run_check(settings, data_path: Path) -> int:
    """Verifica dependências, modelo, áudio, pasta de dados e câmera."""
    from importlib.metadata import PackageNotFoundError, version

    ok = True

    def report(label: str, passed: bool, detail: str = "") -> None:
        nonlocal ok
        ok &= passed
        print(f"  [{'OK ' if passed else 'FALHA'}] {label}{(' — ' + detail) if detail else ''}")

    print(f"{APP_NAME} {APP_VERSION} — diagnóstico\n")
    print(f"  Python {platform.python_version()} ({platform.system()} {platform.release()})")
    for package in ("mediapipe", "opencv-contrib-python", "PySide6-Essentials", "numpy", "sounddevice"):
        try:
            print(f"  {package:<22} {version(package)}")
        except PackageNotFoundError:
            report(package, False, "não instalado")
    print()

    from app.detection_engine import DetectionEngine, verify_model
    if MODEL_PATH.is_file():
        report("Modelo Face Landmarker", verify_model(MODEL_PATH), str(MODEL_PATH))
    else:
        report("Modelo Face Landmarker", False, "ausente — execute: python scripts/download_model.py")

    try:
        probe = data_path / ".write_test"
        probe.write_text("ok", encoding="utf-8")
        probe.unlink()
        report("Pasta de dados gravável", True, str(data_path))
    except OSError as exc:
        report("Pasta de dados gravável", False, str(exc))

    from app.sound import AudioPlayer
    player = AudioPlayer()
    report("Saída de áudio", player.available, player.backend_name if player.available else player.last_error)
    player.shutdown()

    from app.frame_source import OpenCVCameraSource
    cam = settings.camera
    source = OpenCVCameraSource(cam.camera_index, cam.frame_width, cam.frame_height, cam.capture_fps, cam.backend)
    started = time.perf_counter()
    if not source.open():
        report(f"Câmera {cam.camera_index}", False, source.last_error)
    else:
        report(f"Câmera {cam.camera_index}", True,
               f"{source.description} aberta em {time.perf_counter() - started:.2f} s")
        if MODEL_PATH.is_file():
            engine = DetectionEngine(MODEL_PATH, settings.detection.min_detection_confidence)
            faces, frames, busy = 0, 0, 0.0
            deadline = time.monotonic() + 3.0
            while time.monotonic() < deadline:
                frame = source.read()
                if frame is None:
                    continue
                t0 = time.perf_counter()
                obs = engine.process(frame, time.monotonic())
                busy += time.perf_counter() - t0
                frames += 1
                faces += obs.metrics.face_detected
            engine.close()
            detail = (f"{faces}/{frames} quadros com rosto, {1000 * busy / max(frames, 1):.1f} ms/quadro "
                      "(nenhuma imagem foi salva)")
            report("Detecção facial (3 s)", frames > 0, detail)
        source.close()

    print("\nResultado:", "tudo certo." if ok else "há itens com falha (veja acima).")
    return 0 if ok else 1


# --------------------------------------------------------------------------- #
# Interface gráfica
# --------------------------------------------------------------------------- #
def run_gui(args: argparse.Namespace, settings, manager, data_path: Path) -> int:
    from PySide6.QtCore import QTimer
    from PySide6.QtWidgets import QApplication, QMessageBox

    from app.database import EventDatabase
    from app.main_window import MainWindow
    from app.monitoring_controller import MonitoringController
    from app.sound import AudioPlayer
    from ui.icons import app_icon
    from ui.styles import build_stylesheet, pick_fonts

    if sys.platform == "win32":
        try:  # ícone próprio na barra de tarefas (em vez do ícone do Python)
            import ctypes
            ctypes.windll.shell32.SetCurrentProcessExplicitAppUserModelID("DriveGuardAI.Desktop")
        except Exception:  # noqa: BLE001
            pass

    app = QApplication(sys.argv[:1])
    app.setApplicationName(APP_NAME)
    app.setApplicationVersion(APP_VERSION)
    app.setStyle("Fusion")
    text_font, display_font = pick_fonts()
    app.setStyleSheet(build_stylesheet(text_font, display_font))
    app.setWindowIcon(app_icon())

    warnings = list(manager.warnings)
    try:
        database = EventDatabase(data_path / "driveguard.db")
    except Exception as exc:  # noqa: BLE001
        QMessageBox.critical(None, APP_NAME, f"Não foi possível abrir o banco de dados:\n{exc}")
        return 1
    purged = database.purge_older_than(settings.privacy.history_retention_days)
    if purged:
        log.info("%s eventos antigos removidos pela política de retenção", purged)

    player = AudioPlayer()
    if not player.available:
        warnings.append(f"Áudio indisponível: {player.last_error} Os alertas serão apenas visuais.")
    if not MODEL_PATH.is_file():
        warnings.append("O modelo de detecção facial não foi encontrado. Execute "
                        "'python scripts/download_model.py' antes de iniciar o monitoramento.")

    video = args.video
    if video is not None and not video.is_file():
        QMessageBox.critical(None, APP_NAME, f"Arquivo de vídeo não encontrado:\n{video}")
        return 1

    controller = MonitoringController(settings, database, player, video_path=video)
    window = MainWindow(controller, manager, database, data_path, player)
    window.show()
    if args.autostart:
        QTimer.singleShot(300, controller.start)
    if warnings:
        QTimer.singleShot(400, lambda: window.show_startup_warnings(warnings))
    code = app.exec()
    database.close()
    log.info("Aplicação encerrada (código %s)", code)
    return code


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv)
    if args.data_dir is not None:
        os.environ["DRIVEGUARD_DATA_DIR"] = str(args.data_dir)
    data_path = data_dir()
    setup_logging(data_path, args.debug)
    log.info("%s %s iniciando (dados em %s)", APP_NAME, APP_VERSION, data_path)

    from app.settings_manager import SettingsManager
    manager = SettingsManager(data_path / "settings.json")
    settings = manager.load()
    if args.camera is not None:
        settings.camera.camera_index = args.camera

    if args.check:
        return run_check(settings, data_path)
    return run_gui(args, settings, manager, data_path)


if __name__ == "__main__":
    sys.exit(main())
