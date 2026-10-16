"""Baixa o modelo oficial do MediaPipe Face Landmarker e verifica sua integridade.

Uso (a partir da raiz do projeto):
    python scripts/download_model.py
"""

from __future__ import annotations

import hashlib
import sys
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from config.settings import MODEL_PATH, MODEL_SHA256, MODEL_URL  # noqa: E402


def sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def main() -> int:
    if MODEL_PATH.is_file() and sha256(MODEL_PATH) == MODEL_SHA256:
        print(f"Modelo já presente e íntegro: {MODEL_PATH}")
        return 0
    MODEL_PATH.parent.mkdir(parents=True, exist_ok=True)
    tmp = MODEL_PATH.with_suffix(".download")
    print(f"Baixando {MODEL_URL}")
    try:
        with urllib.request.urlopen(MODEL_URL, timeout=60) as response, open(tmp, "wb") as out:
            while chunk := response.read(64 * 1024):
                out.write(chunk)
    except OSError as exc:
        print(f"Falha no download: {exc}", file=sys.stderr)
        tmp.unlink(missing_ok=True)
        return 1
    digest = sha256(tmp)
    if digest != MODEL_SHA256:
        tmp.unlink(missing_ok=True)
        print(f"Checksum inválido ({digest}); arquivo descartado.", file=sys.stderr)
        return 1
    tmp.replace(MODEL_PATH)
    print(f"Modelo salvo em {MODEL_PATH} ({MODEL_PATH.stat().st_size / 1e6:.1f} MB, SHA-256 verificado)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
