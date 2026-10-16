"""Testes da persistência de sessões e eventos."""

from __future__ import annotations

import csv
import sqlite3
from datetime import datetime, timedelta, timezone

import pytest

from app.database import (
    EventDatabase,
    EventType,
    export_csv,
    from_utc_iso,
    to_utc_iso,
)

UTC = timezone.utc
BASE = datetime(2026, 5, 10, 12, 0, 0, tzinfo=UTC)


@pytest.fixture
def db(tmp_path):
    database = EventDatabase(tmp_path / "test.db")
    yield database
    database.close()


def test_write_and_read_event(db):
    session = db.start_session("Câmera 0", started_at=BASE)
    metrics = {"ear": 0.12, "perclos": 0.31}
    event_id = db.log_event(EventType.DROWSINESS, session_id=session, reason="Olhos fechados há 1,8 s",
                            duration=None, metrics=metrics, occurred_at=BASE + timedelta(minutes=5))
    db.set_event_duration(event_id, 4.25)

    [record] = db.query_events()
    assert record.id == event_id
    assert record.session_id == session
    assert record.event_type is EventType.DROWSINESS
    assert record.severity == "critico"
    assert record.duration == pytest.approx(4.25)
    assert record.reason == "Olhos fechados há 1,8 s"
    assert record.metrics == metrics
    assert record.occurred_at == BASE + timedelta(minutes=5)


def test_events_are_ordered_newest_first(db):
    for minutes in (1, 3, 2):
        db.log_event(EventType.YAWN, occurred_at=BASE + timedelta(minutes=minutes))
    times = [r.occurred_at for r in db.query_events()]
    assert times == sorted(times, reverse=True)


def test_filter_by_period_and_type(db):
    db.log_event(EventType.YAWN, occurred_at=BASE - timedelta(days=2))
    db.log_event(EventType.YAWN, occurred_at=BASE)
    db.log_event(EventType.DROWSINESS, occurred_at=BASE + timedelta(hours=1))
    db.log_event(EventType.FACE_LOST, occurred_at=BASE + timedelta(hours=2))

    day = db.query_events(start=BASE - timedelta(hours=1), end=BASE + timedelta(days=1))
    assert len(day) == 3
    only_drowsy = db.query_events(types=[EventType.DROWSINESS])
    assert [r.event_type for r in only_drowsy] == [EventType.DROWSINESS]
    mixed = db.query_events(start=BASE, types=[EventType.YAWN, EventType.FACE_LOST])
    assert {r.event_type for r in mixed} == {EventType.YAWN, EventType.FACE_LOST}
    # Fim do intervalo é exclusivo: o evento exatamente em BASE fica de fora.
    assert [r.occurred_at for r in db.query_events(end=BASE)] == [BASE - timedelta(days=2)]


def test_naive_datetimes_are_treated_as_local_time(db):
    local_now = datetime.now()
    db.log_event(EventType.YAWN, occurred_at=local_now)
    found = db.query_events(start=local_now - timedelta(seconds=1), end=local_now + timedelta(seconds=1))
    assert len(found) == 1


def test_summary_counts_and_monitored_time(db):
    s1 = db.start_session("Câmera 0", started_at=BASE)
    db.log_event(EventType.DROWSINESS, session_id=s1, occurred_at=BASE + timedelta(minutes=1))
    db.log_event(EventType.YAWN, session_id=s1, occurred_at=BASE + timedelta(minutes=2))
    db.end_session(s1, alerts_count=1, ended_at=BASE + timedelta(minutes=30))

    summary = db.summary()
    assert summary.events == 2
    assert summary.drowsiness == 1
    assert summary.sessions == 1
    assert summary.monitored_seconds == pytest.approx(1800.0)


def test_dangling_sessions_are_closed_on_startup(tmp_path):
    path = tmp_path / "crash.db"
    db = EventDatabase(path)
    session = db.start_session("Câmera 0", started_at=BASE)
    db.log_event(EventType.YAWN, session_id=session, occurred_at=BASE + timedelta(minutes=10))
    db.close()  # simula encerramento sem end_session

    reopened = EventDatabase(path)
    summary = reopened.summary()
    reopened.close()
    assert summary.monitored_seconds == pytest.approx(600.0)


def test_clear_history_removes_everything(db):
    session = db.start_session("Câmera 0")
    db.log_event(EventType.YAWN, session_id=session)
    db.log_event(EventType.DROWSINESS, session_id=session)
    assert db.clear_history() == 2
    assert db.query_events() == []
    assert db.summary().sessions == 0


def test_retention_purges_only_old_events(db):
    db.log_event(EventType.YAWN, occurred_at=BASE - timedelta(days=100))
    db.log_event(EventType.YAWN, occurred_at=BASE - timedelta(days=10))
    assert db.purge_older_than(90, now=BASE) == 1
    assert len(db.query_events()) == 1
    assert db.purge_older_than(0, now=BASE) == 0     # 0 = manter sempre


def test_schema_creation_is_idempotent(tmp_path):
    path = tmp_path / "idem.db"
    EventDatabase(path).close()
    db = EventDatabase(path)
    db.log_event(EventType.ATTENTION)
    assert len(db.query_events()) == 1
    db.close()


def test_rejects_database_from_newer_version(tmp_path):
    path = tmp_path / "future.db"
    conn = sqlite3.connect(path)
    conn.execute("PRAGMA user_version = 99")
    conn.close()
    with pytest.raises(RuntimeError, match="versão mais nova"):
        EventDatabase(path)


def test_utc_iso_roundtrip_and_fixed_width():
    moment = datetime(2026, 1, 2, 3, 4, 5, 678000, tzinfo=UTC)
    text = to_utc_iso(moment)
    assert text == "2026-01-02T03:04:05.678Z"
    assert from_utc_iso(text) == moment
    assert len(to_utc_iso(BASE)) == len(text)


def test_export_csv(tmp_path, db):
    db.log_event(EventType.DROWSINESS, reason="teste", duration=2.5, metrics={"ear": 0.1},
                 occurred_at=BASE)
    out = tmp_path / "eventos.csv"
    assert export_csv(db.query_events(), out) == 1
    with open(out, encoding="utf-8-sig", newline="") as fh:
        rows = list(csv.reader(fh, delimiter=";"))
    assert rows[0][0] == "id"
    assert rows[1][2] == "Sonolência provável"
    assert rows[1][4] == "2,50"


def test_in_memory_database():
    db = EventDatabase(":memory:")
    db.log_event(EventType.CAMERA_FAILURE, reason="desconectada")
    assert db.query_events()[0].severity == "sistema"
    db.close()


def test_clear_history_keeps_active_session_valid(db):
    active = db.start_session("Câmera 0")
    db.log_event(EventType.YAWN, session_id=active)
    db.clear_history(keep_session=active)
    # A sessão em andamento continua existindo: novos eventos não violam a chave estrangeira.
    db.log_event(EventType.DROWSINESS, session_id=active)
    assert [r.event_type for r in db.query_events()] == [EventType.DROWSINESS]
    assert db.summary().sessions == 1
