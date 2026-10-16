"""Persistência local (SQLite) de sessões de monitoramento e eventos.

Privacidade: somente metadados são gravados (horário, tipo, duração, motivo
e, opcionalmente, métricas numéricas). Nenhuma imagem ou vídeo é armazenado.

Datas são gravadas em UTC no formato ISO 8601 com tamanho fixo, o que permite
comparar e ordenar como texto; a interface converte para o fuso local.
"""

from __future__ import annotations

import csv
import json
import logging
import sqlite3
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from enum import Enum
from pathlib import Path
from typing import Any, Iterable

log = logging.getLogger(__name__)

SCHEMA_VERSION = 1

_SCHEMA = """
CREATE TABLE IF NOT EXISTS sessions (
    id           INTEGER PRIMARY KEY AUTOINCREMENT,
    started_at   TEXT    NOT NULL,
    ended_at     TEXT,
    source       TEXT    NOT NULL DEFAULT '',
    alerts_count INTEGER NOT NULL DEFAULT 0
);
CREATE TABLE IF NOT EXISTS events (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    session_id  INTEGER REFERENCES sessions(id) ON DELETE CASCADE,
    occurred_at TEXT    NOT NULL,
    event_type  TEXT    NOT NULL,
    severity    TEXT    NOT NULL,
    duration_s  REAL,
    reason      TEXT    NOT NULL DEFAULT '',
    metrics     TEXT
);
CREATE INDEX IF NOT EXISTS idx_events_time ON events(occurred_at);
CREATE INDEX IF NOT EXISTS idx_events_type ON events(event_type);
"""


class EventType(Enum):
    DROWSINESS = "SONOLENCIA"
    ATTENTION = "ATENCAO"
    LONG_CLOSURE = "FECHAMENTO_OCULAR"
    YAWN = "BOCEJO"
    FACE_LOST = "ROSTO_AUSENTE"
    CAMERA_FAILURE = "FALHA_CAMERA"

    @property
    def label(self) -> str:
        return EVENT_LABELS[self]

    @property
    def severity(self) -> str:
        return EVENT_SEVERITY[self]


EVENT_LABELS = {
    EventType.DROWSINESS: "Sonolência provável",
    EventType.ATTENTION: "Atenção",
    EventType.LONG_CLOSURE: "Fechamento ocular prolongado",
    EventType.YAWN: "Bocejo",
    EventType.FACE_LOST: "Rosto ausente",
    EventType.CAMERA_FAILURE: "Falha da câmera",
}

EVENT_SEVERITY = {
    EventType.DROWSINESS: "critico",
    EventType.LONG_CLOSURE: "critico",
    EventType.ATTENTION: "atencao",
    EventType.YAWN: "info",
    EventType.FACE_LOST: "sistema",
    EventType.CAMERA_FAILURE: "sistema",
}


@dataclass(frozen=True)
class EventRecord:
    id: int
    session_id: int | None
    occurred_at: datetime  # UTC, com fuso
    event_type: EventType
    severity: str
    duration: float | None
    reason: str
    metrics: dict[str, Any]


@dataclass(frozen=True)
class HistorySummary:
    events: int
    drowsiness: int
    sessions: int
    monitored_seconds: float


def utc_now() -> datetime:
    return datetime.now(timezone.utc)


def to_utc_iso(moment: datetime) -> str:
    """Converte para texto UTC de tamanho fixo. Datas sem fuso são tratadas como locais."""
    return moment.astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%S.%f")[:-3] + "Z"


def from_utc_iso(text: str) -> datetime:
    return datetime.strptime(text, "%Y-%m-%dT%H:%M:%S.%fZ").replace(tzinfo=timezone.utc)


class EventDatabase:
    """Repositório SQLite. Use a partir de uma única thread (a da interface)."""

    def __init__(self, path: Path | str) -> None:
        self.path = str(path)
        memory = self.path == ":memory:"
        if not memory:
            Path(self.path).parent.mkdir(parents=True, exist_ok=True)
        self._conn = sqlite3.connect(self.path)
        self._conn.row_factory = sqlite3.Row
        self._conn.execute("PRAGMA foreign_keys = ON")
        if not memory:
            self._conn.execute("PRAGMA journal_mode = WAL")
        self._migrate()
        self.close_dangling_sessions()

    def _migrate(self) -> None:
        version = self._conn.execute("PRAGMA user_version").fetchone()[0]
        if version > SCHEMA_VERSION:
            raise RuntimeError(
                f"O banco de dados foi criado por uma versão mais nova (schema {version})."
            )
        with self._conn:
            self._conn.executescript(_SCHEMA)
            self._conn.execute(f"PRAGMA user_version = {SCHEMA_VERSION}")

    def close(self) -> None:
        self._conn.close()

    # ------------------------------------------------------------------ #
    # Sessões
    # ------------------------------------------------------------------ #
    def start_session(self, source: str, started_at: datetime | None = None) -> int:
        with self._conn:
            cur = self._conn.execute(
                "INSERT INTO sessions (started_at, source) VALUES (?, ?)",
                (to_utc_iso(started_at or utc_now()), source),
            )
        return int(cur.lastrowid)

    def end_session(self, session_id: int, alerts_count: int,
                    ended_at: datetime | None = None) -> None:
        with self._conn:
            self._conn.execute(
                "UPDATE sessions SET ended_at = ?, alerts_count = ? WHERE id = ?",
                (to_utc_iso(ended_at or utc_now()), alerts_count, session_id),
            )

    def close_dangling_sessions(self) -> int:
        """Fecha sessões sem término (ex.: aplicação encerrada à força)."""
        with self._conn:
            cur = self._conn.execute(
                """UPDATE sessions SET ended_at = COALESCE(
                       (SELECT MAX(occurred_at) FROM events WHERE events.session_id = sessions.id),
                       started_at)
                   WHERE ended_at IS NULL"""
            )
        return cur.rowcount

    # ------------------------------------------------------------------ #
    # Eventos
    # ------------------------------------------------------------------ #
    def log_event(self, event_type: EventType, *, session_id: int | None = None,
                  reason: str = "", duration: float | None = None,
                  metrics: dict[str, Any] | None = None,
                  occurred_at: datetime | None = None) -> int:
        with self._conn:
            cur = self._conn.execute(
                """INSERT INTO events (session_id, occurred_at, event_type, severity,
                                       duration_s, reason, metrics)
                   VALUES (?, ?, ?, ?, ?, ?, ?)""",
                (
                    session_id,
                    to_utc_iso(occurred_at or utc_now()),
                    event_type.value,
                    event_type.severity,
                    None if duration is None else round(float(duration), 3),
                    reason,
                    json.dumps(metrics, ensure_ascii=False) if metrics else None,
                ),
            )
        return int(cur.lastrowid)

    def set_event_duration(self, event_id: int, duration: float) -> None:
        with self._conn:
            self._conn.execute("UPDATE events SET duration_s = ? WHERE id = ?",
                               (round(float(duration), 3), event_id))

    def query_events(self, start: datetime | None = None, end: datetime | None = None,
                     types: Iterable[EventType] | None = None,
                     limit: int = 10_000) -> list[EventRecord]:
        """Eventos no intervalo [start, end) filtrados por tipo, mais recentes primeiro."""
        where, params = self._filters(start, end, types)
        rows = self._conn.execute(
            f"SELECT * FROM events {where} ORDER BY occurred_at DESC, id DESC LIMIT ?",
            (*params, limit),
        ).fetchall()
        return [self._to_record(row) for row in rows]

    def summary(self, start: datetime | None = None, end: datetime | None = None) -> HistorySummary:
        where, params = self._filters(start, end, None)
        counts = self._conn.execute(
            f"""SELECT COUNT(*) AS total,
                       COALESCE(SUM(event_type = ?), 0) AS drowsy
                FROM events {where}""",
            (EventType.DROWSINESS.value, *params),
        ).fetchone()

        s_where, s_params = self._range("started_at", start, end)
        sessions = self._conn.execute(
            f"SELECT started_at, ended_at FROM sessions {s_where}", s_params
        ).fetchall()
        monitored = 0.0
        for row in sessions:
            if row["ended_at"]:
                delta = from_utc_iso(row["ended_at"]) - from_utc_iso(row["started_at"])
                monitored += max(0.0, delta.total_seconds())
        return HistorySummary(int(counts["total"]), int(counts["drowsy"]), len(sessions), monitored)

    def clear_history(self, keep_session: int | None = None) -> int:
        """Apaga todos os eventos e sessões. Retorna quantos eventos foram removidos.

        ``keep_session`` preserva a linha da sessão em andamento (sem seus
        eventos), para que os próximos registros continuem válidos.
        """
        with self._conn:
            deleted = self._conn.execute("DELETE FROM events").rowcount
            self._conn.execute("DELETE FROM sessions WHERE id IS NOT ?", (keep_session,))
        if self.path != ":memory:":
            self._conn.execute("VACUUM")  # remove os dados apagados do arquivo
        return deleted

    def purge_older_than(self, days: int, now: datetime | None = None) -> int:
        """Remove eventos e sessões mais antigos que ``days`` dias (0 = não remove)."""
        if days <= 0:
            return 0
        cutoff = to_utc_iso((now or utc_now()) - timedelta(days=days))
        with self._conn:
            deleted = self._conn.execute("DELETE FROM events WHERE occurred_at < ?", (cutoff,)).rowcount
            self._conn.execute(
                "DELETE FROM sessions WHERE COALESCE(ended_at, started_at) < ?", (cutoff,))
        return deleted

    # ------------------------------------------------------------------ #
    # Internos
    # ------------------------------------------------------------------ #
    @staticmethod
    def _range(column: str, start: datetime | None, end: datetime | None) -> tuple[str, list]:
        clauses, params = [], []
        if start is not None:
            clauses.append(f"{column} >= ?")
            params.append(to_utc_iso(start))
        if end is not None:
            clauses.append(f"{column} < ?")
            params.append(to_utc_iso(end))
        return ("WHERE " + " AND ".join(clauses)) if clauses else "", params

    def _filters(self, start, end, types) -> tuple[str, list]:
        where, params = self._range("occurred_at", start, end)
        types = list(types or [])
        if types:
            placeholders = ", ".join("?" for _ in types)
            where += (" AND " if where else "WHERE ") + f"event_type IN ({placeholders})"
            params += [t.value for t in types]
        return where, params

    @staticmethod
    def _to_record(row: sqlite3.Row) -> EventRecord:
        try:
            metrics = json.loads(row["metrics"]) if row["metrics"] else {}
        except json.JSONDecodeError:
            metrics = {}
        return EventRecord(
            id=row["id"],
            session_id=row["session_id"],
            occurred_at=from_utc_iso(row["occurred_at"]),
            event_type=EventType(row["event_type"]),
            severity=row["severity"],
            duration=row["duration_s"],
            reason=row["reason"],
            metrics=metrics,
        )


def export_csv(records: Iterable[EventRecord], path: Path | str) -> int:
    """Exporta eventos para CSV (UTF-8 com BOM, separador ';' — abre direto no Excel)."""
    count = 0
    with open(path, "w", newline="", encoding="utf-8-sig") as fh:
        writer = csv.writer(fh, delimiter=";")
        writer.writerow(["id", "data_hora_local", "tipo", "severidade", "duracao_s", "motivo", "metricas"])
        for rec in records:
            writer.writerow([
                rec.id,
                rec.occurred_at.astimezone().strftime("%Y-%m-%d %H:%M:%S"),
                rec.event_type.label,
                rec.severity,
                "" if rec.duration is None else f"{rec.duration:.2f}".replace(".", ","),
                rec.reason,
                json.dumps(rec.metrics, ensure_ascii=False) if rec.metrics else "",
            ])
            count += 1
    return count
