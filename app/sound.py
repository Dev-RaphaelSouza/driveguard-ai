"""Síntese e reprodução dos sons de alerta.

Os sons são gerados matematicamente com NumPy (não dependem de arquivos de
áudio externos) e tocados em uma thread dedicada, para que nem a interface nem
a análise de vídeo sejam bloqueadas.

Backends, em ordem de preferência:
1. ``sounddevice`` (PortAudio) — já instalado como dependência do MediaPipe.
2. ``winsound`` (biblioteca padrão do Windows) — sem controle de interrupção.
"""

from __future__ import annotations

import io
import logging
import queue
import threading
import wave
from enum import Enum
from typing import Protocol

import numpy as np

log = logging.getLogger(__name__)

SAMPLE_RATE = 44_100


class SoundKind(Enum):
    DROWSINESS = "drowsiness"   # alarme alto de dois tons alternados
    MONITORING = "monitoring"   # dois bipes descendentes (falha de monitoramento)
    ATTENTION = "attention"     # toque curto e suave


# --------------------------------------------------------------------------- #
# Síntese
# --------------------------------------------------------------------------- #
def _tone(freq: float, duration: float, sr: int = SAMPLE_RATE,
          harmonics: tuple[tuple[int, float], ...] = ((1, 1.0),),
          attack: float = 0.006, release: float = 0.025) -> np.ndarray:
    n = int(duration * sr)
    t = np.arange(n, dtype=np.float64) / sr
    wave_ = sum(amp * np.sin(2 * np.pi * freq * h * t) for h, amp in harmonics)
    wave_ /= sum(amp for _, amp in harmonics)
    envelope = np.ones(n)
    a, r = int(attack * sr), int(release * sr)
    if a:
        envelope[:a] = np.linspace(0.0, 1.0, a)
    if r:
        envelope[-r:] = np.linspace(1.0, 0.0, r)  # rampas evitam estalos
    return wave_ * envelope


def _silence(duration: float, sr: int = SAMPLE_RATE) -> np.ndarray:
    return np.zeros(int(duration * sr))


def synthesize(kind: SoundKind, sr: int = SAMPLE_RATE) -> np.ndarray:
    """Gera o som (mono, float32, pico 0,9) correspondente a ``kind``."""
    if kind is SoundKind.DROWSINESS:
        # Harmônicos ímpares dão um timbre "quadrado" mais penetrante que um seno.
        timbre = ((1, 1.0), (3, 0.35), (5, 0.18))
        parts = []
        for i in range(8):
            parts.append(_tone(988.0 if i % 2 == 0 else 1319.0, 0.16, sr, timbre))
            parts.append(_silence(0.03, sr))
        signal = np.concatenate(parts)
    elif kind is SoundKind.MONITORING:
        timbre = ((1, 1.0), (2, 0.25))
        signal = np.concatenate([
            _tone(784.0, 0.18, sr, timbre), _silence(0.08, sr),
            _tone(523.0, 0.30, sr, timbre, release=0.08),
        ])
    else:
        n = int(0.6 * sr)
        t = np.arange(n) / sr
        signal = np.sin(2 * np.pi * 880.0 * t) * np.exp(-t * 7.0)
        signal[: int(0.004 * sr)] *= np.linspace(0, 1, int(0.004 * sr))
    peak = float(np.max(np.abs(signal))) or 1.0
    return (signal / peak * 0.9).astype(np.float32)


def to_wav_bytes(samples: np.ndarray, sr: int = SAMPLE_RATE) -> bytes:
    """Converte amostras float em um arquivo WAV PCM 16 bits em memória."""
    pcm = (np.clip(samples, -1.0, 1.0) * 32767).astype("<i2")
    buffer = io.BytesIO()
    with wave.open(buffer, "wb") as wav:
        wav.setnchannels(1)
        wav.setsampwidth(2)
        wav.setframerate(sr)
        wav.writeframes(pcm.tobytes())
    return buffer.getvalue()


# --------------------------------------------------------------------------- #
# Reprodução
# --------------------------------------------------------------------------- #
class SoundPlayer(Protocol):
    """Interface usada pelo AlertManager (facilita testes com um player falso)."""

    @property
    def available(self) -> bool: ...

    def play(self, kind: SoundKind, volume: float) -> bool: ...

    def stop(self) -> None: ...


class _SounddeviceBackend:
    name = "sounddevice"

    def __init__(self) -> None:
        import sounddevice as sd

        sd.query_devices(kind="output")  # lança exceção se não houver saída de áudio
        self._sd = sd

    def play(self, samples: np.ndarray, sr: int) -> None:
        self._sd.play(samples, sr)
        self._sd.wait()

    def stop(self) -> None:
        self._sd.stop()


class _WinsoundBackend:
    name = "winsound"

    def __init__(self) -> None:
        import winsound

        self._ws = winsound

    def play(self, samples: np.ndarray, sr: int) -> None:
        self._ws.PlaySound(to_wav_bytes(samples, sr), self._ws.SND_MEMORY)

    def stop(self) -> None:
        self._ws.PlaySound(None, self._ws.SND_PURGE)


class AudioPlayer:
    """Toca sons em uma thread própria, sem bloquear quem chama ``play``."""

    def __init__(self, prefer: str = "auto") -> None:
        self._cache: dict[SoundKind, np.ndarray] = {}
        self._queue: queue.Queue[tuple[SoundKind, float] | None] = queue.Queue(maxsize=3)
        self._last_error = ""
        self._backends = self._create_backends(prefer)
        self._thread = threading.Thread(target=self._loop, name="AudioPlayer", daemon=True)
        self._thread.start()

    def _create_backends(self, prefer: str) -> list:
        candidates = {"sounddevice": _SounddeviceBackend, "winsound": _WinsoundBackend}
        order = [prefer] if prefer in candidates else []
        order += [name for name in candidates if name not in order]
        backends, errors = [], []
        for name in order:
            try:
                backends.append(candidates[name]())
            except Exception as exc:  # noqa: BLE001 - qualquer falha torna o backend indisponível
                errors.append(f"{name}: {exc}")
        if not backends:
            self._last_error = "Nenhuma saída de áudio disponível (" + "; ".join(errors) + ")."
            log.error(self._last_error)
        else:
            log.info("Áudio: usando backend %s", backends[0].name)
        return backends

    @property
    def available(self) -> bool:
        return bool(self._backends)

    @property
    def backend_name(self) -> str:
        return self._backends[0].name if self._backends else "indisponível"

    @property
    def last_error(self) -> str:
        return self._last_error

    def samples(self, kind: SoundKind) -> np.ndarray:
        if kind not in self._cache:
            self._cache[kind] = synthesize(kind)
        return self._cache[kind]

    def play(self, kind: SoundKind, volume: float) -> bool:
        if not self._backends:
            return False
        try:
            self._queue.put_nowait((kind, max(0.0, min(1.0, volume))))
            return True
        except queue.Full:
            return False  # já há sons suficientes na fila; evita acúmulo

    def stop(self) -> None:
        self._drain()
        for backend in self._backends:
            try:
                backend.stop()
            except Exception:  # noqa: BLE001
                pass

    def shutdown(self) -> None:
        self.stop()
        try:
            self._queue.put_nowait(None)
        except queue.Full:
            pass
        self._thread.join(timeout=2.0)

    def _drain(self) -> None:
        try:
            while True:
                self._queue.get_nowait()
        except queue.Empty:
            pass

    def _loop(self) -> None:
        while True:
            item = self._queue.get()
            if item is None:
                return
            kind, volume = item
            data = self.samples(kind) * volume
            for backend in list(self._backends):
                try:
                    backend.play(data, SAMPLE_RATE)
                    break
                except Exception as exc:  # noqa: BLE001 - dispositivo removido, driver etc.
                    self._last_error = f"Falha ao tocar som via {backend.name}: {exc}"
                    log.warning(self._last_error)
