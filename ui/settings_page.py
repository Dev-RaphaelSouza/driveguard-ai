"""Tela de configurações.

Os controles numéricos são gerados a partir dos metadados declarados em
``config/settings.py`` (rótulo, faixa, unidade e ajuda). Assim, validação e
interface compartilham a mesma fonte de verdade.
"""

from __future__ import annotations

import copy
from collections.abc import Callable
from pathlib import Path

from PySide6.QtCore import Qt, QThread, Signal
from PySide6.QtWidgets import (
    QComboBox,
    QDoubleSpinBox,
    QFrame,
    QHBoxLayout,
    QLabel,
    QMessageBox,
    QScrollArea,
    QSlider,
    QSpinBox,
    QVBoxLayout,
    QWidget,
)

from app.frame_source import probe_cameras
from config.settings import SECTION_TYPES, AppSettings, DetectionSettings, field_meta, validate_settings
from ui.components import Card, ToggleSwitch, make_button, styled_label
from ui.icons import pixmap
from ui.styles import Palette

RESOLUTIONS = [
    ("640 × 480  (recomendado)", (640, 480)),
    ("960 × 540", (960, 540)),
    ("1280 × 720", (1280, 720)),
    ("1920 × 1080", (1920, 1080)),
]
BACKENDS = [
    ("DirectShow (recomendado)", "dshow"),
    ("Media Foundation", "msmf"),
    ("Automático", "any"),
]
CONTROL_WIDTH = 170


class CameraProbe(QThread):
    """Procura câmeras em segundo plano (abrir dispositivos pode levar segundos)."""

    found = Signal(list)

    def __init__(self, backend: str, skip: set[int], parent=None) -> None:
        super().__init__(parent)
        self._backend, self._skip = backend, skip

    def run(self) -> None:
        self.found.emit(probe_cameras(4, self._backend, self._skip))


class SettingsPage(QWidget):
    settings_saved = Signal(object)        # AppSettings
    test_sound_requested = Signal(float)   # volume ainda não salvo
    clear_history_requested = Signal()
    open_data_dir_requested = Signal()

    def __init__(self, settings: AppSettings, data_path: Path,
                 camera_in_use: Callable[[], int | None], parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setObjectName("Page")
        self._base = copy.deepcopy(settings)
        self._camera_in_use = camera_in_use
        self._bindings: list[tuple[str, str, Callable, Callable]] = []
        self._rows: dict[int, int] = {}
        self._loading = False
        self._probe: CameraProbe | None = None

        outer = QVBoxLayout(self)
        outer.setContentsMargins(0, 0, 0, 0)
        outer.setSpacing(0)

        content = QWidget()
        content.setObjectName("Page")
        layout = QVBoxLayout(content)
        layout.setContentsMargins(24, 20, 24, 24)
        layout.setSpacing(16)
        layout.addWidget(self._experimental_notice())

        columns = QHBoxLayout()
        columns.setSpacing(16)
        left, right = QVBoxLayout(), QVBoxLayout()
        left.setSpacing(16)
        right.setSpacing(16)
        columns.addLayout(left, 1)
        columns.addLayout(right, 1)
        layout.addLayout(columns)

        left.addWidget(self._detection_card())
        left.addWidget(self._confirmation_card())
        left.addWidget(self._yawn_head_card())
        left.addStretch(1)
        right.addWidget(self._alerts_card())
        right.addWidget(self._camera_card())
        right.addWidget(self._quality_card())
        right.addWidget(self._privacy_card(data_path))
        right.addStretch(1)

        scroll = QScrollArea()
        scroll.setWidget(content)
        scroll.setWidgetResizable(True)
        scroll.setFrameShape(QFrame.Shape.NoFrame)
        outer.addWidget(scroll, 1)
        outer.addWidget(self._footer())

        self.load(settings)

    # ------------------------------------------------------------------ #
    # Construção dos cartões
    # ------------------------------------------------------------------ #
    def _experimental_notice(self) -> QFrame:
        frame = QFrame()
        frame.setObjectName("Notice")
        row = QHBoxLayout(frame)
        row.setContentsMargins(16, 12, 16, 12)
        row.setSpacing(12)
        badge = QLabel()
        badge.setPixmap(pixmap("info", Palette.WARN, 20))
        row.addWidget(badge, 0, Qt.AlignmentFlag.AlignTop)
        text = styled_label(
            "Os valores padrão são <b>parâmetros experimentais</b> pensados para webcams comuns — "
            "não são limites médicos nem validados para segurança veicular. Ajuste-os com o "
            "procedimento de avaliação manual descrito na documentação.", wrap=True)
        text.setTextFormat(Qt.TextFormat.RichText)
        row.addWidget(text, 1)
        return frame

    def _detection_card(self) -> Card:
        card = Card("Detecção", "eye")
        self.sensitivity = QSlider(Qt.Orientation.Horizontal)
        self.sensitivity.setRange(1, 10)
        self.sensitivity.setPageStep(1)
        self.sensitivity_value = styled_label("", "H2")
        self.sensitivity_value.setFixedWidth(24)
        box = QWidget()
        box_layout = QHBoxLayout(box)
        box_layout.setContentsMargins(0, 0, 0, 0)
        box_layout.addWidget(self.sensitivity, 1)
        box_layout.addWidget(self.sensitivity_value)
        box.setFixedWidth(CONTROL_WIDTH + 40)
        self.sensitivity_help = self._row(card, "Sensibilidade", "", box)
        self.sensitivity.valueChanged.connect(self._on_sensitivity)
        self._bind("detection", "sensitivity", self.sensitivity.value, self.sensitivity.setValue)

        self._number(card, "detection", "long_closure_seconds")
        self._toggle(card, "detection", "auto_calibration")
        self._number(card, "detection", "calibration_seconds")
        self._number(card, "detection", "manual_baseline_ear")
        return card

    def _confirmation_card(self) -> Card:
        card = Card("Confirmação e janelas temporais", "timer")
        self._number(card, "detection", "confirmation_seconds")
        self._number(card, "detection", "recovery_seconds")
        self._number(card, "detection", "perclos_window_seconds")
        self._number(card, "detection", "perclos_attention", percent=True)
        self._number(card, "detection", "perclos_drowsy", percent=True)
        self._number(card, "detection", "long_blink_seconds")
        self._number(card, "detection", "long_blinks_attention_count")
        self._toggle(card, "detection", "combine_signals")
        return card

    def _yawn_head_card(self) -> Card:
        card = Card("Bocejos e postura da cabeça", "activity")
        self._number(card, "detection", "yawn_mar_threshold")
        self._number(card, "detection", "yawn_min_seconds")
        self._number(card, "detection", "yawns_attention_count")
        self._number(card, "detection", "yawn_window_seconds")
        self._number(card, "detection", "head_tilt_degrees")
        self._number(card, "detection", "head_tilt_seconds")
        return card

    def _alerts_card(self) -> Card:
        card = Card("Alertas", "bell")
        self._toggle(card, "alerts", "sound_enabled")
        self.volume = QSlider(Qt.Orientation.Horizontal)
        self.volume.setRange(0, 100)
        self.volume_value = styled_label("", "Muted")
        self.volume_value.setFixedWidth(40)
        test = make_button("Testar", "volume")
        test.clicked.connect(lambda: self.test_sound_requested.emit(self.volume.value() / 100))
        box = QWidget()
        box_layout = QHBoxLayout(box)
        box_layout.setContentsMargins(0, 0, 0, 0)
        box_layout.addWidget(self.volume, 1)
        box_layout.addWidget(self.volume_value)
        box_layout.addWidget(test)
        box.setFixedWidth(CONTROL_WIDTH + 130)
        self._row(card, "Volume", "Teste o volume com o som real do alarme.", box)
        self.volume.valueChanged.connect(lambda v: self.volume_value.setText(f"{v}%"))
        self.volume.valueChanged.connect(self._changed)
        self._bind("alerts", "volume", lambda: self.volume.value() / 100,
                   lambda v: self.volume.setValue(round(v * 100)))
        self._number(card, "alerts", "alert_interval_seconds")
        self._number(card, "alerts", "mute_minutes")
        self._number(card, "alerts", "face_lost_alert_seconds")
        self._toggle(card, "alerts", "face_lost_sound")
        self._toggle(card, "alerts", "camera_failure_sound")
        self._toggle(card, "alerts", "attention_sound")
        return card

    def _camera_card(self) -> Card:
        card = Card("Câmera", "camera")
        self.camera_combo = QComboBox()
        self.camera_combo.setFixedWidth(CONTROL_WIDTH)
        self.probe_button = make_button("Procurar", "search")
        self.probe_button.clicked.connect(self._probe_cameras)
        box = QWidget()
        box_layout = QHBoxLayout(box)
        box_layout.setContentsMargins(0, 0, 0, 0)
        box_layout.addWidget(self.camera_combo)
        box_layout.addWidget(self.probe_button)
        self.camera_help = self._row(card, "Câmera", "Use “Procurar” para listar as câmeras conectadas.", box)
        self.camera_combo.currentIndexChanged.connect(self._changed)
        self._bind("camera", "camera_index", self.camera_combo.currentData, self._select_camera)

        self.backend_combo = self._combo(BACKENDS)
        self._row(card, field_meta(SECTION_TYPES["camera"], "backend")["label"],
                  field_meta(SECTION_TYPES["camera"], "backend")["help"], self.backend_combo)
        self._bind("camera", "backend", self.backend_combo.currentData,
                   lambda v: self.backend_combo.setCurrentIndex(max(0, self.backend_combo.findData(v))))

        self.resolution_combo = self._combo(RESOLUTIONS)
        self._row(card, "Resolução", "Resoluções maiores custam mais CPU sem ganho relevante "
                  "para o detector, que reduz a imagem internamente.", self.resolution_combo)
        self._bind("camera", "frame_width", lambda: self.resolution_combo.currentData()[0],
                   lambda v: None)
        self._bind("camera", "frame_height", lambda: self.resolution_combo.currentData()[1],
                   lambda v: None)

        self._number(card, "camera", "process_fps")
        self._number(card, "camera", "capture_fps")
        self._toggle(card, "camera", "mirror_preview")
        self._number(card, "camera", "reconnect_seconds")
        return card

    def _quality_card(self) -> Card:
        card = Card("Qualidade da detecção", "shield-check")
        self._number(card, "detection", "min_quality", percent=True)
        self._number(card, "detection", "min_detection_confidence", percent=True)
        self._number(card, "detection", "face_lost_grace_seconds")
        self._number(card, "detection", "eye_hysteresis", percent=True)
        return card

    def _privacy_card(self, data_path: Path) -> Card:
        card = Card("Privacidade e dados", "lock")
        self._toggle(card, "privacy", "show_preview")
        self._toggle(card, "privacy", "draw_landmarks")
        self._toggle(card, "privacy", "store_event_metrics")
        self._number(card, "privacy", "history_retention_days")
        info = styled_label(
            "Todo o processamento é local: nenhuma imagem é enviada pela internet nem gravada "
            "em disco. São armazenados apenas o histórico de eventos (SQLite), as "
            f"configurações (JSON) e o log técnico em:<br><b>{data_path}</b>", "Help", wrap=True)
        info.setTextFormat(Qt.TextFormat.RichText)
        info.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
        card.body.addWidget(self._divider())
        card.body.addWidget(info)
        buttons = QHBoxLayout()
        open_dir = make_button("Abrir pasta de dados", "folder")
        open_dir.clicked.connect(self.open_data_dir_requested)
        clear = make_button("Apagar histórico", "trash", "Danger", "#FCA5A5")
        clear.clicked.connect(self.clear_history_requested)
        buttons.addWidget(open_dir)
        buttons.addWidget(clear)
        buttons.addStretch(1)
        card.body.addLayout(buttons)
        return card

    def _footer(self) -> QFrame:
        footer = QFrame()
        footer.setObjectName("Footer")
        row = QHBoxLayout(footer)
        row.setContentsMargins(24, 12, 24, 12)
        row.setSpacing(10)
        reset = make_button("Restaurar padrões", "refresh", "Ghost", Palette.TEXT_MUTED)
        reset.clicked.connect(self._confirm_reset)
        row.addWidget(reset)
        row.addStretch(1)
        self.dirty_label = styled_label("Alterações não salvas", "")
        self.dirty_label.setStyleSheet(f"color: {Palette.WARN}; font-weight: 600;")
        row.addWidget(self.dirty_label)
        self.discard_button = make_button("Descartar")
        self.discard_button.clicked.connect(lambda: self.load(self._base))
        self.save_button = make_button("Salvar alterações", "check", "Primary", Palette.ON_ACCENT)
        self.save_button.clicked.connect(self._save)
        row.addWidget(self.discard_button)
        row.addWidget(self.save_button)
        return footer

    # ------------------------------------------------------------------ #
    # Helpers de formulário
    # ------------------------------------------------------------------ #
    @staticmethod
    def _divider() -> QFrame:
        line = QFrame()
        line.setObjectName("Divider")
        return line

    def _row(self, card: Card, label: str, help_text: str, control: QWidget) -> QLabel:
        if self._rows.get(id(card)):
            card.body.addWidget(self._divider())
        self._rows[id(card)] = self._rows.get(id(card), 0) + 1
        row = QHBoxLayout()
        row.setSpacing(16)
        texts = QVBoxLayout()
        texts.setSpacing(2)
        title = QLabel(label)
        title.setStyleSheet("font-weight: 600;")
        texts.addWidget(title)
        help_label = styled_label(help_text, "Help", wrap=True)
        help_label.setVisible(bool(help_text))
        texts.addWidget(help_label)
        row.addLayout(texts, 1)
        row.addWidget(control, 0, Qt.AlignmentFlag.AlignVCenter)
        card.body.addLayout(row)
        return help_label

    def _bind(self, section: str, name: str, getter: Callable, setter: Callable) -> None:
        self._bindings.append((section, name, getter, setter))

    def _number(self, card: Card, section: str, name: str, percent: bool = False) -> QWidget:
        section_type = SECTION_TYPES[section]
        meta = field_meta(section_type, name)
        default = getattr(section_type(), name)
        is_int = isinstance(default, int) and not isinstance(default, bool)
        scale = 100.0 if percent else 1.0
        if is_int:
            widget = QSpinBox()
            widget.setRange(int(meta["min"]), int(meta["max"]))
        else:
            widget = QDoubleSpinBox()
            span = meta["max"] - meta["min"]
            decimals = 0 if percent else (2 if span <= 2 else 1)
            widget.setDecimals(decimals)
            widget.setRange(meta["min"] * scale, meta["max"] * scale)
            widget.setSingleStep(1.0 if percent else (0.01 if decimals == 2 else 0.5))
        unit = "%" if percent else meta.get("unit", "")
        if unit:
            widget.setSuffix(f" {unit}")
        widget.setFixedWidth(CONTROL_WIDTH)
        widget.setAlignment(Qt.AlignmentFlag.AlignRight)
        widget.valueChanged.connect(self._changed)
        if is_int:
            getter = lambda: int(widget.value())  # noqa: E731
        else:
            getter = lambda: round(widget.value() / scale, 4)  # noqa: E731
        self._bind(section, name, getter, lambda v: widget.setValue(v * scale))
        self._row(card, meta["label"], meta.get("help", ""), widget)
        return widget

    def _toggle(self, card: Card, section: str, name: str) -> ToggleSwitch:
        meta = field_meta(SECTION_TYPES[section], name)
        switch = ToggleSwitch()
        switch.toggled.connect(self._changed)
        self._bind(section, name, switch.isChecked, switch.setChecked)
        self._row(card, meta["label"], meta.get("help", ""), switch)
        return switch

    def _combo(self, items: list[tuple[str, object]]) -> QComboBox:
        combo = QComboBox()
        for text, data in items:
            combo.addItem(text, data)
        combo.setFixedWidth(CONTROL_WIDTH + 80)
        combo.currentIndexChanged.connect(self._changed)
        return combo

    # ------------------------------------------------------------------ #
    # Estado do formulário
    # ------------------------------------------------------------------ #
    def load(self, settings: AppSettings) -> None:
        """Preenche o formulário (sem marcá-lo como alterado)."""
        self._base = copy.deepcopy(settings)
        self._loading = True
        try:
            self._fill_cameras([], settings.camera.camera_index)
            size = (settings.camera.frame_width, settings.camera.frame_height)
            index = self.resolution_combo.findData(size)
            if index < 0:
                self.resolution_combo.addItem(f"{size[0]} × {size[1]}", size)
                index = self.resolution_combo.count() - 1
            self.resolution_combo.setCurrentIndex(index)
            for section, name, _getter, setter in self._bindings:
                setter(getattr(getattr(settings, section), name))
            self._on_sensitivity(settings.detection.sensitivity)
            self.volume_value.setText(f"{round(settings.alerts.volume * 100)}%")
        finally:
            self._loading = False
        self._set_dirty(False)

    def collect(self) -> AppSettings:
        settings = copy.deepcopy(self._base)
        for section, name, getter, _setter in self._bindings:
            setattr(getattr(settings, section), name, getter())
        return settings

    def _changed(self, *_args) -> None:
        if not self._loading:
            self._set_dirty(self.collect() != self._base)

    def _set_dirty(self, dirty: bool) -> None:
        self.dirty_label.setVisible(dirty)
        self.save_button.setEnabled(dirty)
        self.discard_button.setEnabled(dirty)

    def _on_sensitivity(self, value: int) -> None:
        ratio = DetectionSettings(sensitivity=value).closed_ratio()
        self.sensitivity_value.setText(str(value))
        self.sensitivity_help.setText(
            f"Olho considerado fechado abaixo de {ratio * 100:.0f}% do EAR de referência "
            "(olhos abertos). Valores altos detectam mais fechamentos e mais falsos positivos.")
        self.sensitivity_help.setVisible(True)
        self._changed()

    def _save(self) -> None:
        settings = self.collect()
        errors = validate_settings(settings)
        if errors:
            QMessageBox.warning(self, "Configurações inválidas", "\n".join(errors))
            return
        self.settings_saved.emit(settings)

    def mark_saved(self, settings: AppSettings) -> None:
        self.load(settings)

    def _confirm_reset(self) -> None:
        answer = QMessageBox.question(
            self, "Restaurar padrões",
            "Restaurar todas as configurações para os valores padrão?\n"
            "O histórico de eventos não é afetado.")
        if answer == QMessageBox.StandardButton.Yes:
            self.settings_saved.emit(AppSettings())

    # ------------------------------------------------------------------ #
    # Câmeras
    # ------------------------------------------------------------------ #
    def _fill_cameras(self, found: list, selected: int) -> None:
        in_use = self._camera_in_use()
        self.camera_combo.blockSignals(True)
        self.camera_combo.clear()
        indexes = {cam.index: cam.label for cam in found}
        if in_use is not None:
            indexes.setdefault(in_use, f"Câmera {in_use}  ·  em uso")
        indexes.setdefault(selected, f"Câmera {selected}")
        if not found:
            for i in range(3):
                indexes.setdefault(i, f"Câmera {i}")
        for index in sorted(indexes):
            self.camera_combo.addItem(indexes[index], index)
        self.camera_combo.setCurrentIndex(max(0, self.camera_combo.findData(selected)))
        self.camera_combo.blockSignals(False)

    def _select_camera(self, index: int) -> None:
        position = self.camera_combo.findData(index)
        if position < 0:
            self.camera_combo.addItem(f"Câmera {index}", index)
            position = self.camera_combo.count() - 1
        self.camera_combo.setCurrentIndex(position)

    def _probe_cameras(self) -> None:
        if self._probe is not None and self._probe.isRunning():
            return
        in_use = self._camera_in_use()
        self.probe_button.setEnabled(False)
        self.probe_button.setText("Procurando…")
        self._probe = CameraProbe(self.backend_combo.currentData(), {in_use} if in_use is not None else set(), self)
        self._probe.found.connect(self._on_probe_done)
        self._probe.start()

    def _on_probe_done(self, found: list) -> None:
        selected = self.camera_combo.currentData()
        self._fill_cameras(found, selected if selected is not None else 0)
        self.probe_button.setEnabled(True)
        self.probe_button.setText("Procurar")
        count = len(found) + (1 if self._camera_in_use() is not None else 0)
        self.camera_help.setText(
            f"{count} câmera(s) encontrada(s)." if count else
            "Nenhuma câmera respondeu. Verifique a conexão e as permissões de câmera do Windows.")
        self._changed()

    def shutdown(self) -> None:
        if self._probe is not None:
            self._probe.wait(5000)
