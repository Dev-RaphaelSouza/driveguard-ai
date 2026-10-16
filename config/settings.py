"""Parâmetros configuráveis, caminhos e validação do DriveGuard AI.

IMPORTANTE: todos os limiares de detecção definidos aqui são PARÂMETROS
EXPERIMENTAIS escolhidos como ponto de partida razoável para webcams comuns.
Eles NÃO são limites médicos, normativos ou cientificamente validados para
segurança veicular. Ajuste-os com o procedimento de avaliação manual descrito
em docs/AVALIACAO_MANUAL.md.

Cada campo carrega metadados (rótulo, faixa válida, unidade e ajuda) usados
tanto pela validação quanto pela tela de configurações, evitando duplicar
regras em vários lugares.
"""

from __future__ import annotations

import math
import os
import sys
from dataclasses import asdict, dataclass, field, fields
from pathlib import Path
from typing import Any

APP_NAME = "DriveGuard AI"
APP_VERSION = "1.0.0"
SETTINGS_SCHEMA_VERSION = 1

# Modelo oficial do MediaPipe Face Landmarker (licença Apache 2.0).
MODEL_URL = (
    "https://storage.googleapis.com/mediapipe-models/face_landmarker/"
    "face_landmarker/float16/1/face_landmarker.task"
)
MODEL_SHA256 = "64184e229b263107bc2b804c6625db1341ff2bb731874b0bcc2fe6544e0bc9ff"


# --------------------------------------------------------------------------- #
# Caminhos
# --------------------------------------------------------------------------- #
def is_frozen() -> bool:
    """Indica se a aplicação está empacotada (PyInstaller)."""
    return bool(getattr(sys, "frozen", False))


def resource_root() -> Path:
    """Diretório com recursos somente leitura (assets)."""
    if is_frozen():
        return Path(getattr(sys, "_MEIPASS", Path(sys.executable).parent))
    return Path(__file__).resolve().parent.parent


def data_dir() -> Path:
    """Diretório gravável para banco de dados, configurações e logs.

    Ordem de prioridade:
    1. Variável de ambiente ``DRIVEGUARD_DATA_DIR``.
    2. ``%LOCALAPPDATA%\\DriveGuardAI`` quando empacotado (executável).
    3. Pasta ``data/`` do projeto quando executado a partir do código-fonte.
    """
    env = os.environ.get("DRIVEGUARD_DATA_DIR")
    if env:
        return Path(env).expanduser().resolve()
    if is_frozen():
        base = os.environ.get("LOCALAPPDATA") or str(Path.home())
        return Path(base) / "DriveGuardAI"
    return resource_root() / "data"


ASSETS_DIR = resource_root() / "assets"
ICONS_DIR = ASSETS_DIR / "icons"
MODEL_PATH = ASSETS_DIR / "models" / "face_landmarker.task"


# --------------------------------------------------------------------------- #
# Helpers de metadados
# --------------------------------------------------------------------------- #
def _num(default: float, lo: float, hi: float, label: str, help: str = "", unit: str = ""):
    return field(
        default=default,
        metadata={"min": lo, "max": hi, "label": label, "help": help, "unit": unit},
    )


def _choice(default: str, choices: tuple[str, ...], label: str, help: str = ""):
    return field(default=default, metadata={"choices": choices, "label": label, "help": help})


def _flag(default: bool, label: str, help: str = ""):
    return field(default=default, metadata={"label": label, "help": help})


# --------------------------------------------------------------------------- #
# Seções de configuração
# --------------------------------------------------------------------------- #
@dataclass
class CameraSettings:
    """Preferências de captura de vídeo."""

    camera_index: int = _num(0, 0, 9, "Câmera", "Índice da câmera no sistema (0 = padrão).")
    backend: str = _choice(
        "dshow",
        ("dshow", "msmf", "any"),
        "Backend de captura",
        "DirectShow abre a câmera mais rápido no Windows; MSMF é a alternativa nativa.",
    )
    frame_width: int = _num(640, 320, 1920, "Largura do vídeo", unit="px")
    frame_height: int = _num(480, 240, 1080, "Altura do vídeo", unit="px")
    capture_fps: int = _num(30, 5, 60, "FPS solicitado à câmera", unit="fps")
    process_fps: int = _num(
        20, 5, 30, "Taxa de análise",
        "Quadros analisados por segundo. Valores maiores aumentam a precisão "
        "temporal e o consumo de CPU.",
        "fps",
    )
    mirror_preview: bool = _flag(True, "Espelhar pré-visualização")
    reconnect_seconds: float = _num(
        3.0, 1.0, 30.0, "Intervalo de reconexão",
        "Tempo entre tentativas de reabrir a câmera após uma falha.", "s",
    )


@dataclass
class DetectionSettings:
    """Parâmetros do analisador temporal de sonolência (experimentais)."""

    sensitivity: int = _num(
        6, 1, 10, "Sensibilidade",
        "Define a fração do EAR de referência abaixo da qual o olho é considerado "
        "fechado. Valores altos detectam mais fechamentos (e mais falsos positivos).",
    )
    auto_calibration: bool = _flag(
        True, "Calibração automática",
        "Mede o EAR com olhos abertos nos primeiros segundos de cada sessão.",
    )
    calibration_seconds: float = _num(5.0, 2.0, 30.0, "Duração da calibração", unit="s")
    manual_baseline_ear: float = _num(
        0.30, 0.15, 0.50, "EAR de referência manual",
        "Usado antes da calibração terminar ou quando ela está desativada.",
    )
    eye_hysteresis: float = _num(
        0.10, 0.0, 0.5, "Histerese dos olhos",
        "Margem extra (fração do limiar) exigida para considerar o olho reaberto.",
    )
    long_blink_seconds: float = _num(
        0.5, 0.3, 1.5, "Piscada longa",
        "Fechamentos a partir desta duração são contados como piscadas longas.", "s",
    )
    long_closure_seconds: float = _num(
        1.5, 0.5, 5.0, "Fechamento ocular prolongado",
        "Duração mínima de olhos fechados para indicar sonolência provável.", "s",
    )
    perclos_window_seconds: float = _num(
        60.0, 20.0, 300.0, "Janela do PERCLOS",
        "Janela temporal usada para calcular a proporção de tempo com olhos fechados.", "s",
    )
    perclos_attention: float = _num(0.15, 0.05, 0.60, "PERCLOS para atenção")
    perclos_drowsy: float = _num(0.30, 0.10, 0.90, "PERCLOS para sonolência")
    long_blinks_attention_count: int = _num(
        3, 1, 20, "Piscadas longas para atenção",
        "Quantidade de piscadas longas dentro da janela do PERCLOS.",
    )
    yawn_mar_threshold: float = _num(
        0.50, 0.20, 1.20, "Limiar de abertura da boca (MAR)",
        "Razão de aspecto da boca a partir da qual se considera um possível bocejo.",
    )
    yawn_min_seconds: float = _num(1.5, 0.5, 6.0, "Duração mínima do bocejo", unit="s")
    yawn_window_seconds: float = _num(300.0, 60.0, 1800.0, "Janela de bocejos", unit="s")
    yawns_attention_count: int = _num(3, 1, 10, "Bocejos para atenção")
    head_tilt_degrees: float = _num(
        20.0, 8.0, 45.0, "Inclinação da cabeça",
        "Desvio (graus) em relação à postura calibrada (sinal complementar).", "°",
    )
    head_tilt_seconds: float = _num(2.0, 0.5, 10.0, "Duração da inclinação", unit="s")
    min_quality: float = _num(
        0.5, 0.1, 0.9, "Qualidade mínima da detecção",
        "Quadros abaixo desta qualidade são ignorados pelo analisador.",
    )
    min_detection_confidence: float = _num(
        0.5, 0.3, 0.9, "Confiança mínima do detector",
        "Confiança mínima de detecção/rastreamento repassada ao MediaPipe.",
    )
    face_lost_grace_seconds: float = _num(
        1.0, 0.2, 5.0, "Tolerância à perda do rosto",
        "Falhas mais curtas que isso não alteram o estado.", "s",
    )
    confirmation_seconds: float = _num(
        2.0, 0.0, 10.0, "Período de confirmação",
        "Tempo que um sinal precisa persistir antes de mudar o estado.", "s",
    )
    recovery_seconds: float = _num(
        5.0, 1.0, 30.0, "Período de recuperação",
        "Tempo sem sinais exigido para rebaixar o estado (histerese temporal).", "s",
    )
    combine_signals: bool = _flag(
        True, "Combinar sinais",
        "Dois ou mais sinais de atenção simultâneos elevam o estado para sonolência provável.",
    )

    def closed_ratio(self) -> float:
        """Converte a sensibilidade (1–10) em fração do EAR de referência.

        1 → 0,45 (menos sensível) … 10 → 0,72 (mais sensível).
        """
        return 0.45 + (self.sensitivity - 1) * 0.03


@dataclass
class AlertSettings:
    """Preferências dos alertas sonoros e visuais."""

    sound_enabled: bool = _flag(True, "Alertas sonoros")
    volume: float = _num(0.85, 0.0, 1.0, "Volume")
    alert_interval_seconds: float = _num(
        8.0, 2.0, 120.0, "Intervalo entre alertas",
        "Intervalo mínimo entre sons de alerta; o som se repete neste ritmo enquanto "
        "o estado persistir.", "s",
    )
    mute_minutes: int = _num(5, 1, 60, "Duração do silêncio temporário", unit="min")
    face_lost_alert_seconds: float = _num(
        5.0, 2.0, 60.0, "Alerta de rosto ausente",
        "Tempo sem rosto confiável até emitir o aviso de falha de monitoramento.", "s",
    )
    face_lost_sound: bool = _flag(True, "Som para falha de monitoramento")
    camera_failure_sound: bool = _flag(True, "Som para falha da câmera")
    attention_sound: bool = _flag(False, "Som discreto no estado de atenção")


@dataclass
class PrivacySettings:
    """Preferências de privacidade. Imagens nunca são gravadas em disco."""

    show_preview: bool = _flag(
        True, "Mostrar vídeo na tela",
        "Desative para ocultar a imagem; a análise continua normalmente.",
    )
    draw_landmarks: bool = _flag(True, "Desenhar pontos faciais na pré-visualização")
    store_event_metrics: bool = _flag(
        True, "Salvar métricas nos eventos",
        "Guarda valores numéricos (EAR, PERCLOS, etc.) junto de cada evento.",
    )
    history_retention_days: int = _num(
        90, 0, 3650, "Retenção do histórico",
        "Eventos mais antigos são apagados automaticamente (0 = manter sempre).", "dias",
    )


SECTION_TYPES: dict[str, type] = {
    "camera": CameraSettings,
    "detection": DetectionSettings,
    "alerts": AlertSettings,
    "privacy": PrivacySettings,
}


@dataclass
class AppSettings:
    """Agrupa todas as seções de configuração."""

    camera: CameraSettings = field(default_factory=CameraSettings)
    detection: DetectionSettings = field(default_factory=DetectionSettings)
    alerts: AlertSettings = field(default_factory=AlertSettings)
    privacy: PrivacySettings = field(default_factory=PrivacySettings)

    def to_dict(self) -> dict[str, Any]:
        data = asdict(self)
        data["schema_version"] = SETTINGS_SCHEMA_VERSION
        return data

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> tuple["AppSettings", list[str]]:
        """Cria configurações a partir de um dicionário (ex.: JSON).

        Valores ausentes, de tipo errado ou fora da faixa são substituídos pelo
        padrão e geram um aviso, para que um arquivo corrompido nunca impeça a
        aplicação de iniciar.
        """
        warnings: list[str] = []
        sections: dict[str, Any] = {}
        if not isinstance(data, dict):
            return cls(), ["Arquivo de configuração inválido; usando valores padrão."]
        for name, section_type in SECTION_TYPES.items():
            raw = data.get(name, {})
            if not isinstance(raw, dict):
                warnings.append(f"Seção '{name}' inválida; usando valores padrão.")
                raw = {}
            section, section_warnings = _section_from_dict(section_type, raw)
            sections[name] = section
            warnings.extend(section_warnings)
        settings = cls(**sections)
        cross_errors = _cross_field_errors(settings)
        if cross_errors:
            warnings.extend(cross_errors)
            settings.detection = DetectionSettings()
            warnings.append("Parâmetros de detecção restaurados para o padrão.")
        return settings, warnings


# --------------------------------------------------------------------------- #
# Validação
# --------------------------------------------------------------------------- #
def _coerce(value: Any, default: Any) -> Any:
    """Converte ``value`` para o tipo de ``default`` ou lança ValueError."""
    if isinstance(default, bool):
        if isinstance(value, bool):
            return value
        raise ValueError("esperado verdadeiro/falso")
    if isinstance(default, int):
        if isinstance(value, bool):
            raise ValueError("esperado número inteiro")
        if isinstance(value, int):
            return value
        if isinstance(value, float) and value.is_integer():
            return int(value)
        raise ValueError("esperado número inteiro")
    if isinstance(default, float):
        if isinstance(value, bool) or not isinstance(value, (int, float)):
            raise ValueError("esperado número")
        value = float(value)
        if not math.isfinite(value):
            raise ValueError("número inválido")
        return value
    if isinstance(default, str):
        if isinstance(value, str):
            return value
        raise ValueError("esperado texto")
    raise ValueError("tipo não suportado")


def _field_error(f, value: Any) -> str | None:
    md = f.metadata
    label = md.get("label", f.name)
    try:
        value = _coerce(value, f.default)
    except ValueError as exc:
        return f"{label}: {exc} (recebido {value!r})."
    if "min" in md and not (md["min"] <= value <= md["max"]):
        return f"{label}: {value} fora do intervalo permitido [{md['min']}, {md['max']}]."
    if "choices" in md and value not in md["choices"]:
        return f"{label}: opção '{value}' inválida."
    return None


def _section_from_dict(section_type: type, raw: dict[str, Any]) -> tuple[Any, list[str]]:
    values: dict[str, Any] = {}
    warnings: list[str] = []
    for f in fields(section_type):
        if f.name not in raw:
            continue
        error = _field_error(f, raw[f.name])
        if error:
            warnings.append(error + " Usando o padrão.")
            continue
        values[f.name] = _coerce(raw[f.name], f.default)
    return section_type(**values), warnings


def validate_section(section: Any) -> list[str]:
    """Retorna a lista de erros de uma seção (vazia se válida)."""
    errors = []
    for f in fields(section):
        error = _field_error(f, getattr(section, f.name))
        if error:
            errors.append(error)
    return errors


def _cross_field_errors(settings: AppSettings) -> list[str]:
    det = settings.detection
    errors = []
    if det.perclos_drowsy <= det.perclos_attention:
        errors.append("O PERCLOS para sonolência deve ser maior que o PERCLOS para atenção.")
    if det.long_closure_seconds <= det.long_blink_seconds:
        errors.append("O fechamento ocular prolongado deve ser maior que a piscada longa.")
    return errors


def validate_settings(settings: AppSettings) -> list[str]:
    """Valida todas as seções e as regras entre campos."""
    errors: list[str] = []
    for name in SECTION_TYPES:
        errors.extend(validate_section(getattr(settings, name)))
    errors.extend(_cross_field_errors(settings))
    return errors


def field_meta(section_type: type, name: str) -> dict[str, Any]:
    """Metadados (rótulo, faixa, ajuda, unidade) de um campo — usado pela UI."""
    for f in fields(section_type):
        if f.name == name:
            return dict(f.metadata)
    raise KeyError(name)
