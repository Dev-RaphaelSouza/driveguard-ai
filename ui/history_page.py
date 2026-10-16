"""Tela de histórico: filtros, resumo, tabela de eventos, exportação e limpeza."""

from __future__ import annotations

from collections.abc import Callable
from datetime import datetime, time, timedelta

from PySide6.QtCore import QAbstractTableModel, QDate, QModelIndex, QRectF, Qt, Signal
from PySide6.QtGui import QColor, QFont, QFontMetrics, QPainter
from PySide6.QtWidgets import (
    QComboBox,
    QDateEdit,
    QFileDialog,
    QHBoxLayout,
    QHeaderView,
    QLabel,
    QMessageBox,
    QStackedLayout,
    QStyle,
    QStyledItemDelegate,
    QStyleOptionViewItem,
    QTableView,
    QVBoxLayout,
    QWidget,
)

from app.database import EventDatabase, EventRecord, EventType, export_csv
from ui.components import Card, MetricTile, make_button, styled_label
from ui.icons import pixmap
from ui.styles import Palette, qcolor

SEVERITY_COLORS = {
    "critico": Palette.DANGER,
    "atencao": Palette.WARN,
    "info": Palette.INFO,
    "sistema": Palette.SYSTEM,
}

METRIC_LABELS = (
    ("ear", "EAR", lambda v: f"{v:.2f}".replace(".", ",")),
    ("perclos", "PERCLOS", lambda v: f"{v * 100:.0f}%"),
    ("fechamento_s", "fech.", lambda v: f"{v:.1f} s".replace(".", ",")),
    ("bocejos", "bocejos", lambda v: f"{v}"),
    ("desvio_cabeca_graus", "cabeça", lambda v: f"{v:.0f}°"),
    ("qualidade", "qual.", lambda v: f"{v * 100:.0f}%"),
)

PERIODS = ("Hoje", "Últimos 7 dias", "Últimos 30 dias", "Todo o período", "Personalizado")


def format_duration(seconds: float | None) -> str:
    if seconds is None:
        return "—"
    if seconds < 60:
        return f"{seconds:.1f} s".replace(".", ",")
    minutes, sec = divmod(int(round(seconds)), 60)
    if minutes < 60:
        return f"{minutes} min {sec:02d} s"
    hours, minutes = divmod(minutes, 60)
    return f"{hours} h {minutes:02d} min"


def metrics_summary(metrics: dict) -> str:
    parts = []
    for key, label, formatter in METRIC_LABELS:
        value = metrics.get(key)
        if value is None or (key in ("bocejos",) and not value):
            continue
        parts.append(f"{label} {formatter(value)}")
    return "  ·  ".join(parts) if parts else "—"


class EventTableModel(QAbstractTableModel):
    HEADERS = ("DATA E HORA", "TIPO", "DURAÇÃO", "MOTIVO", "MÉTRICAS")

    def __init__(self) -> None:
        super().__init__()
        self._rows: list[EventRecord] = []

    def set_records(self, records: list[EventRecord]) -> None:
        self.beginResetModel()
        self._rows = records
        self.endResetModel()

    @property
    def records(self) -> list[EventRecord]:
        return self._rows

    def rowCount(self, parent=QModelIndex()) -> int:  # noqa: N802
        return 0 if parent.isValid() else len(self._rows)

    def columnCount(self, parent=QModelIndex()) -> int:  # noqa: N802
        return len(self.HEADERS)

    def headerData(self, section, orientation, role=Qt.ItemDataRole.DisplayRole):  # noqa: N802
        if orientation == Qt.Orientation.Horizontal and role == Qt.ItemDataRole.DisplayRole:
            return self.HEADERS[section]
        return None

    def data(self, index: QModelIndex, role=Qt.ItemDataRole.DisplayRole):
        if not index.isValid():
            return None
        rec = self._rows[index.row()]
        col = index.column()
        if role == Qt.ItemDataRole.DisplayRole:
            if col == 0:
                return rec.occurred_at.astimezone().strftime("%d/%m/%Y  %H:%M:%S")
            if col == 1:
                return rec.event_type.label
            if col == 2:
                return format_duration(rec.duration)
            if col == 3:
                return rec.reason or "—"
            return metrics_summary(rec.metrics)
        if role == Qt.ItemDataRole.ToolTipRole:
            if col == 4 and rec.metrics:
                return "\n".join(f"{k}: {v}" for k, v in rec.metrics.items())
            if col == 3:
                return rec.reason
        if role == Qt.ItemDataRole.ForegroundRole and col in (2, 4):
            return QColor(Palette.TEXT_MUTED)
        if role == Qt.ItemDataRole.UserRole:
            return SEVERITY_COLORS.get(rec.severity, Palette.TEXT_MUTED)
        return None


class PillDelegate(QStyledItemDelegate):
    """Desenha o tipo do evento como uma etiqueta colorida."""

    def paint(self, painter: QPainter, option: QStyleOptionViewItem, index: QModelIndex) -> None:
        if option.state & QStyle.StateFlag.State_Selected:
            painter.fillRect(option.rect, qcolor(Palette.SURFACE_3))
        text = index.data(Qt.ItemDataRole.DisplayRole) or ""
        color = index.data(Qt.ItemDataRole.UserRole) or Palette.TEXT_MUTED
        font = QFont(option.font)
        font.setPixelSize(11)
        font.setWeight(QFont.Weight.Bold)
        fm = QFontMetrics(font)
        width = fm.horizontalAdvance(text) + 26
        rect = QRectF(option.rect.left() + 12, option.rect.center().y() - 11, width, 22)
        painter.save()
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        painter.setPen(Qt.PenStyle.NoPen)
        painter.setBrush(qcolor(color, 0.14))
        painter.drawRoundedRect(rect, 11, 11)
        painter.setBrush(qcolor(color))
        painter.drawEllipse(rect.left() + 10, rect.center().y() - 3, 6, 6)
        painter.setFont(font)
        painter.setPen(qcolor(color))
        painter.drawText(rect.adjusted(20, 0, -6, 0), Qt.AlignmentFlag.AlignVCenter, text)
        painter.restore()


class HistoryPage(QWidget):
    notice = Signal(str)

    def __init__(self, database: EventDatabase,
                 active_session: Callable[[], int | None] = lambda: None,
                 parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setObjectName("Page")
        self._db = database
        self._active_session = active_session

        root = QVBoxLayout(self)
        root.setContentsMargins(24, 20, 24, 22)
        root.setSpacing(16)

        # ---- filtros ----------------------------------------------------- #
        filters = Card()
        row = QHBoxLayout()
        row.setSpacing(10)
        filters.body.addLayout(row)
        self.period = QComboBox()
        self.period.addItems(PERIODS)
        self.period.setCurrentIndex(1)
        self.date_from = QDateEdit(calendarPopup=True)
        self.date_to = QDateEdit(calendarPopup=True)
        for edit in (self.date_from, self.date_to):
            edit.setDisplayFormat("dd/MM/yyyy")
            edit.setDate(QDate.currentDate())
        self.date_from.setDate(QDate.currentDate().addDays(-7))
        self.type_filter = QComboBox()
        self.type_filter.addItem("Todos os tipos", None)
        for event_type in EventType:
            self.type_filter.addItem(event_type.label, event_type)

        for label, widget in (("Período", self.period), ("De", self.date_from),
                              ("Até", self.date_to), ("Tipo", self.type_filter)):
            box = QVBoxLayout()
            box.setSpacing(5)
            box.addWidget(styled_label(label, "Faint"))
            box.addWidget(widget)
            row.addLayout(box)
        row.addStretch(1)
        self.refresh_button = make_button("Atualizar", "refresh")
        self.export_button = make_button("Exportar CSV", "download")
        self.clear_button = make_button("Limpar histórico", "trash", "Danger", "#FCA5A5")
        for button in (self.refresh_button, self.export_button, self.clear_button):
            row.addWidget(button, 0, Qt.AlignmentFlag.AlignBottom)
        root.addWidget(filters)

        # ---- resumo ------------------------------------------------------ #
        stats = QHBoxLayout()
        stats.setSpacing(12)
        self.stat_events = MetricTile("Eventos no período")
        self.stat_drowsy = MetricTile("Alertas de sonolência")
        self.stat_sessions = MetricTile("Sessões")
        self.stat_time = MetricTile("Tempo monitorado")
        for tile in (self.stat_events, self.stat_drowsy, self.stat_sessions, self.stat_time):
            tile.value.setObjectName("StatValue")
            stats.addWidget(tile)
        root.addLayout(stats)

        # ---- tabela ------------------------------------------------------ #
        table_card = Card()
        table_card.body.setContentsMargins(4, 4, 4, 4)
        self.model = EventTableModel()
        self.table = QTableView()
        self.table.setModel(self.model)
        self.table.setItemDelegateForColumn(1, PillDelegate(self.table))
        self.table.verticalHeader().setVisible(False)
        self.table.verticalHeader().setDefaultSectionSize(46)
        self.table.setSelectionBehavior(QTableView.SelectionBehavior.SelectRows)
        self.table.setSelectionMode(QTableView.SelectionMode.SingleSelection)
        self.table.setEditTriggers(QTableView.EditTrigger.NoEditTriggers)
        self.table.setShowGrid(False)
        self.table.setWordWrap(False)
        self.table.setFocusPolicy(Qt.FocusPolicy.NoFocus)
        header = self.table.horizontalHeader()
        header.setHighlightSections(False)
        header.setDefaultAlignment(Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter)
        for col, width in enumerate((180, 240, 110)):
            header.setSectionResizeMode(col, QHeaderView.ResizeMode.Fixed)
            header.resizeSection(col, width)
        header.setSectionResizeMode(3, QHeaderView.ResizeMode.Stretch)
        header.setSectionResizeMode(4, QHeaderView.ResizeMode.Stretch)

        self.empty = QWidget()
        empty_layout = QVBoxLayout(self.empty)
        empty_layout.setAlignment(Qt.AlignmentFlag.AlignCenter)
        icon_label = QLabel()
        icon_label.setPixmap(pixmap("history", Palette.TEXT_FAINT, 40))
        icon_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        empty_layout.addWidget(icon_label)
        title = styled_label("Nenhum evento no período selecionado", "H2")
        title.setAlignment(Qt.AlignmentFlag.AlignCenter)
        empty_layout.addWidget(title)
        hint = styled_label("Os eventos aparecem aqui durante e após as sessões de monitoramento.", "Muted")
        hint.setAlignment(Qt.AlignmentFlag.AlignCenter)
        empty_layout.addWidget(hint)

        self._stack = QStackedLayout()
        self._stack.addWidget(self.table)
        self._stack.addWidget(self.empty)
        table_card.body.addLayout(self._stack)
        root.addWidget(table_card, 1)

        privacy = styled_label(
            "Somente metadados são armazenados (data, tipo, duração, motivo e métricas numéricas). "
            "Nenhuma imagem ou vídeo é gravado.", "Faint", wrap=True)
        root.addWidget(privacy)

        self.period.currentIndexChanged.connect(self._on_period_changed)
        self.date_from.dateChanged.connect(self.refresh)
        self.date_to.dateChanged.connect(self.refresh)
        self.type_filter.currentIndexChanged.connect(self.refresh)
        self.refresh_button.clicked.connect(self.refresh)
        self.export_button.clicked.connect(self._export)
        self.clear_button.clicked.connect(self.confirm_clear)
        self._on_period_changed()

    # ------------------------------------------------------------------ #
    def _range(self) -> tuple[datetime | None, datetime | None]:
        today = datetime.combine(datetime.now().date(), time.min)
        index = self.period.currentIndex()
        if index == 0:
            return today, today + timedelta(days=1)
        if index == 1:
            return today - timedelta(days=6), None
        if index == 2:
            return today - timedelta(days=29), None
        if index == 3:
            return None, None
        start = datetime.combine(self.date_from.date().toPython(), time.min)
        end = datetime.combine(self.date_to.date().toPython(), time.min) + timedelta(days=1)
        return start, end

    def _on_period_changed(self) -> None:
        custom = self.period.currentIndex() == len(PERIODS) - 1
        self.date_from.setEnabled(custom)
        self.date_to.setEnabled(custom)
        self.refresh()

    def refresh(self) -> None:
        start, end = self._range()
        event_type = self.type_filter.currentData()
        types = [event_type] if event_type is not None else None
        try:
            records = self._db.query_events(start, end, types)
            summary = self._db.summary(start, end)
        except Exception as exc:  # noqa: BLE001
            QMessageBox.critical(self, "Erro ao ler o histórico", str(exc))
            return
        self.model.set_records(records)
        self._stack.setCurrentIndex(0 if records else 1)
        self.stat_events.set_value(str(summary.events), "registros", None)
        self.stat_drowsy.set_value(str(summary.drowsiness), "sonolência provável",
                                   Palette.DANGER if summary.drowsiness else None)
        self.stat_sessions.set_value(str(summary.sessions), "iniciadas no período", None)
        self.stat_time.set_value(format_duration(summary.monitored_seconds) if summary.sessions else "—",
                                 "de monitoramento", None)
        self.export_button.setEnabled(bool(records))

    def _export(self) -> None:
        default = f"driveguard_eventos_{datetime.now():%Y%m%d_%H%M}.csv"
        path, _ = QFileDialog.getSaveFileName(self, "Exportar eventos", default, "CSV (*.csv)")
        if not path:
            return
        try:
            count = export_csv(self.model.records, path)
        except OSError as exc:
            QMessageBox.critical(self, "Falha ao exportar", str(exc))
            return
        self.notice.emit(f"{count} eventos exportados para {path}")

    def confirm_clear(self) -> None:
        box = QMessageBox(self)
        box.setIcon(QMessageBox.Icon.Warning)
        box.setWindowTitle("Limpar histórico")
        box.setText("Apagar todo o histórico de eventos e sessões?")
        box.setInformativeText("Esta ação não pode ser desfeita. As configurações não são afetadas.")
        confirm = box.addButton("Apagar histórico", QMessageBox.ButtonRole.DestructiveRole)
        box.addButton("Cancelar", QMessageBox.ButtonRole.RejectRole)
        box.setDefaultButton(box.buttons()[-1])
        box.exec()
        if box.clickedButton() is confirm:
            self.clear_history()

    def clear_history(self) -> None:
        try:
            deleted = self._db.clear_history(keep_session=self._active_session())
        except Exception as exc:  # noqa: BLE001
            QMessageBox.critical(self, "Falha ao limpar histórico", str(exc))
            return
        self.refresh()
        self.notice.emit(f"Histórico apagado ({deleted} eventos removidos).")
