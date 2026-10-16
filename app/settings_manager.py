"""Leitura e gravação persistente das configurações (JSON).

* Gravação atômica (arquivo temporário + ``os.replace``): uma queda de energia
  durante o salvamento não corrompe o arquivo existente.
* Arquivo corrompido é preservado como ``.corrompido`` e a aplicação inicia
  com os valores padrão, informando o problema ao usuário.
"""

from __future__ import annotations

import json
import logging
import os
from pathlib import Path

from config.settings import AppSettings, validate_settings

log = logging.getLogger(__name__)


class SettingsError(ValueError):
    """Configuração inválida rejeitada no salvamento."""


class SettingsManager:
    def __init__(self, path: Path | str) -> None:
        self.path = Path(path)
        self.warnings: list[str] = []

    def load(self) -> AppSettings:
        """Carrega as configurações; nunca lança exceção por arquivo ruim."""
        self.warnings = []
        if not self.path.exists():
            return AppSettings()
        try:
            data = json.loads(self.path.read_text(encoding="utf-8"))
        except (OSError, UnicodeDecodeError, json.JSONDecodeError) as exc:
            backup = self.path.with_suffix(self.path.suffix + ".corrompido")
            try:
                os.replace(self.path, backup)
            except OSError:
                pass
            self.warnings.append(
                f"Não foi possível ler as configurações ({exc}). Valores padrão restaurados; "
                f"o arquivo original foi preservado em '{backup.name}'."
            )
            log.warning(self.warnings[-1])
            return AppSettings()
        settings, warnings = AppSettings.from_dict(data)
        self.warnings.extend(warnings)
        for warning in warnings:
            log.warning("Configuração: %s", warning)
        return settings

    def save(self, settings: AppSettings) -> None:
        errors = validate_settings(settings)
        if errors:
            raise SettingsError("\n".join(errors))
        self.path.parent.mkdir(parents=True, exist_ok=True)
        tmp = self.path.with_suffix(self.path.suffix + ".tmp")
        tmp.write_text(json.dumps(settings.to_dict(), indent=2, ensure_ascii=False), encoding="utf-8")
        os.replace(tmp, self.path)
        log.info("Configurações salvas em %s", self.path)

    def reset(self) -> AppSettings:
        """Restaura e grava os valores padrão."""
        settings = AppSettings()
        self.save(settings)
        return settings
