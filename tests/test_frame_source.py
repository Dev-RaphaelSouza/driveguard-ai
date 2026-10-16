"""Testes das origens de vídeo (sem depender de webcam)."""

from __future__ import annotations

import cv2
import numpy as np

from app.frame_source import FrameSource, OpenCVCameraSource, VideoFileSource


def _write_video(path, frames=6, size=(64, 48)):
    writer = cv2.VideoWriter(str(path), cv2.VideoWriter_fourcc(*"MJPG"), 30.0, size)
    for i in range(frames):
        writer.write(np.full((size[1], size[0], 3), i * 20, np.uint8))
    writer.release()


def test_sources_implement_protocol(tmp_path):
    assert isinstance(VideoFileSource(tmp_path / "x.avi"), FrameSource)
    assert isinstance(OpenCVCameraSource(0), FrameSource)


def test_video_file_reads_and_loops(tmp_path):
    path = tmp_path / "clip.avi"
    _write_video(path, frames=6)
    source = VideoFileSource(path, loop=True, realtime=False)
    assert source.open()
    frames = [source.read() for _ in range(10)]  # mais quadros que o vídeo: deve repetir
    source.close()
    assert all(f is not None and f.shape == (48, 64, 3) for f in frames)
    assert not source.is_open()


def test_video_file_without_loop_ends(tmp_path):
    path = tmp_path / "clip.avi"
    _write_video(path, frames=3)
    source = VideoFileSource(path, loop=False, realtime=False)
    assert source.open()
    frames = [source.read() for _ in range(5)]
    source.close()
    assert frames[-1] is None
    assert "Fim do arquivo" in source.last_error


def test_still_image_source(tmp_path):
    path = tmp_path / "rosto.png"
    cv2.imwrite(str(path), np.full((40, 30, 3), 128, np.uint8))
    source = VideoFileSource(path, realtime=False)
    assert source.open()
    a, b = source.read(), source.read()
    source.close()
    assert a.shape == (40, 30, 3)
    assert a is not b  # cada leitura entrega uma cópia independente


def test_missing_file_reports_error(tmp_path):
    source = VideoFileSource(tmp_path / "nao_existe.mp4")
    assert not source.open()
    assert "não encontrado" in source.last_error
    assert source.read() is None


def test_unavailable_camera_reports_clear_error():
    source = OpenCVCameraSource(index=9, backend="dshow")
    assert not source.open()
    assert "câmera 9" in source.last_error.lower()
    assert source.read() is None
    source.close()  # fechar duas vezes é seguro
    source.close()
