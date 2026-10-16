"""Origens de imagem substituíveis.

O restante do sistema depende apenas do protocolo :class:`FrameSource`. Assim,
uma câmera USB, um arquivo de vídeo ou, futuramente, uma câmera veicular com
outro SDK podem ser trocados sem reescrever a detecção ou a interface.
"""

from __future__ import annotations

import logging
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Protocol, runtime_checkable

import cv2
import numpy as np

log = logging.getLogger(__name__)

IMAGE_SUFFIXES = {".jpg", ".jpeg", ".png", ".bmp", ".webp"}

BACKENDS = {
    "dshow": cv2.CAP_DSHOW,
    "msmf": cv2.CAP_MSMF,
    "any": cv2.CAP_ANY,
}


@runtime_checkable
class FrameSource(Protocol):
    """Contrato mínimo de uma origem de quadros (BGR, uint8)."""

    @property
    def description(self) -> str: ...

    @property
    def last_error(self) -> str: ...

    def open(self) -> bool:
        """Abre a origem. Retorna False (e preenche ``last_error``) em caso de falha."""

    def read(self) -> np.ndarray | None:
        """Lê um quadro BGR ou retorna None se nenhum quadro estiver disponível."""

    def close(self) -> None:
        """Libera os recursos. Deve ser seguro chamar mais de uma vez."""

    def is_open(self) -> bool: ...


@dataclass(frozen=True)
class CameraInfo:
    index: int
    width: int
    height: int

    @property
    def label(self) -> str:
        return f"Câmera {self.index}  ·  {self.width}×{self.height}"


class OpenCVCameraSource:
    """Webcam acessada via OpenCV (DirectShow/MSMF no Windows)."""

    def __init__(
        self,
        index: int = 0,
        width: int = 640,
        height: int = 480,
        fps: int = 30,
        backend: str = "dshow",
    ) -> None:
        self.index = index
        self.width = width
        self.height = height
        self.fps = fps
        self.backend = backend if backend in BACKENDS else "any"
        self._cap: cv2.VideoCapture | None = None
        self._last_error = ""
        self._actual_size: tuple[int, int] | None = None

    @property
    def description(self) -> str:
        if self._actual_size:
            w, h = self._actual_size
            return f"Câmera {self.index} · {w}×{h}"
        return f"Câmera {self.index}"

    @property
    def last_error(self) -> str:
        return self._last_error

    def open(self) -> bool:
        self.close()
        try:
            cap = cv2.VideoCapture(self.index, BACKENDS[self.backend])
        except cv2.error as exc:  # pragma: no cover - depende do driver
            self._last_error = f"Erro do OpenCV ao abrir a câmera {self.index}: {exc}"
            return False
        if not cap.isOpened():
            cap.release()
            self._last_error = (
                f"Não foi possível abrir a câmera {self.index}. Verifique se ela está "
                "conectada, se não está em uso por outro aplicativo e se o Windows "
                "permite o acesso à câmera (Configurações › Privacidade › Câmera)."
            )
            return False
        cap.set(cv2.CAP_PROP_FRAME_WIDTH, self.width)
        cap.set(cv2.CAP_PROP_FRAME_HEIGHT, self.height)
        cap.set(cv2.CAP_PROP_FPS, self.fps)
        # Buffer mínimo reduz a latência entre a cena real e a análise.
        cap.set(cv2.CAP_PROP_BUFFERSIZE, 1)
        ok, frame = cap.read()
        if not ok or frame is None:
            cap.release()
            self._last_error = (
                f"A câmera {self.index} abriu, mas não enviou imagens. Ela pode estar "
                "ocupada por outro programa."
            )
            return False
        self._actual_size = (frame.shape[1], frame.shape[0])
        self._cap = cap
        self._last_error = ""
        log.info("Câmera %s aberta (%sx%s, backend=%s)", self.index, *self._actual_size, self.backend)
        return True

    def read(self) -> np.ndarray | None:
        if self._cap is None:
            return None
        try:
            ok, frame = self._cap.read()
        except cv2.error as exc:  # pragma: no cover - depende do driver
            self._last_error = f"Erro de leitura da câmera: {exc}"
            return None
        if not ok or frame is None:
            self._last_error = "A câmera parou de enviar imagens."
            return None
        return frame

    def close(self) -> None:
        if self._cap is not None:
            self._cap.release()
            self._cap = None
            log.info("Câmera %s liberada", self.index)

    def is_open(self) -> bool:
        return self._cap is not None


class VideoFileSource:
    """Arquivo de vídeo (ou imagem estática) usado para testes reprodutíveis.

    Respeita o FPS do arquivo (``realtime=True``) para que o analisador
    temporal observe durações realistas.
    """

    def __init__(self, path: str | Path, loop: bool = True, realtime: bool = True,
                 fallback_fps: float = 30.0) -> None:
        self.path = Path(path)
        self.loop = loop
        self.realtime = realtime
        self.fallback_fps = fallback_fps
        self._cap: cv2.VideoCapture | None = None
        self._image: np.ndarray | None = None
        self._frame_interval = 1.0 / fallback_fps
        self._next_frame_at = 0.0
        self._last_error = ""

    @property
    def description(self) -> str:
        return f"Arquivo: {self.path.name}"

    @property
    def last_error(self) -> str:
        return self._last_error

    def open(self) -> bool:
        self.close()
        if not self.path.is_file():
            self._last_error = f"Arquivo não encontrado: {self.path}"
            return False
        if self.path.suffix.lower() in IMAGE_SUFFIXES:
            image = cv2.imread(str(self.path))
            if image is None:
                self._last_error = f"Não foi possível ler a imagem: {self.path}"
                return False
            self._image = image
        else:
            cap = cv2.VideoCapture(str(self.path))
            if not cap.isOpened():
                cap.release()
                self._last_error = f"Não foi possível abrir o vídeo: {self.path}"
                return False
            fps = cap.get(cv2.CAP_PROP_FPS)
            if fps and 1 <= fps <= 240:
                self._frame_interval = 1.0 / fps
            self._cap = cap
        self._next_frame_at = time.monotonic()
        self._last_error = ""
        return True

    def _pace(self) -> None:
        if not self.realtime:
            return
        now = time.monotonic()
        if self._next_frame_at > now:
            time.sleep(self._next_frame_at - now)
        self._next_frame_at = max(self._next_frame_at + self._frame_interval,
                                  time.monotonic() - self._frame_interval)

    def read(self) -> np.ndarray | None:
        if self._image is not None:
            self._pace()
            return self._image.copy()
        if self._cap is None:
            return None
        self._pace()
        ok, frame = self._cap.read()
        if not ok and self.loop:
            self._cap.set(cv2.CAP_PROP_POS_FRAMES, 0)
            ok, frame = self._cap.read()
        if not ok or frame is None:
            self._last_error = "Fim do arquivo de vídeo."
            return None
        return frame

    def close(self) -> None:
        if self._cap is not None:
            self._cap.release()
            self._cap = None
        self._image = None

    def is_open(self) -> bool:
        return self._cap is not None or self._image is not None


def probe_cameras(max_index: int = 4, backend: str = "dshow",
                  skip: set[int] | None = None) -> list[CameraInfo]:
    """Procura câmeras abrindo os índices 0..max_index.

    O OpenCV não oferece listagem de dispositivos com nomes, então testamos os
    índices. Câmeras em uso (``skip``) não são reabertas.
    """
    found: list[CameraInfo] = []
    skip = skip or set()
    for index in range(max_index + 1):
        if index in skip:
            continue
        cap = cv2.VideoCapture(index, BACKENDS.get(backend, cv2.CAP_ANY))
        try:
            if not cap.isOpened():
                continue
            ok, frame = cap.read()
            if ok and frame is not None:
                found.append(CameraInfo(index, frame.shape[1], frame.shape[0]))
        finally:
            cap.release()
    return found
