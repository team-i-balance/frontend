"""이벤트 로그와 큐 분류 (§9). 원문은 저장하지 않는다: 유형·건수·사유·큐만.

실제 위반 로그의 암호화 페이로드(SealedBox)는 Phase 5에서 추가한다. 지금은 sealed_payload를 비워 둔다.
"""
from __future__ import annotations

import json
import sqlite3
from datetime import datetime
from pathlib import Path

HIGH = 0.85
REASON_PRIORITY = {"business_need": 3, "approved": 3, "none": 2, "false_positive": 1}
QUEUE_LABELS = {
    "pre_warning": "사전 경고 (검토 X)",
    "tuning": "탐지 개선 (오탐)",
    "candidate": "위반 후보 (선택 검토)",
    "violation": "실제 위반 (필수 검토)",
    "missed": "미탐 의심",
    "regression": "회귀 경고",
}

_SCHEMA = """
CREATE TABLE IF NOT EXISTS events(
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    ts TEXT NOT NULL,
    service TEXT,
    grade TEXT,
    action TEXT,
    reason TEXT,
    detect_types TEXT,
    counts TEXT,
    reverted_types TEXT,
    rule_ids TEXT,
    evasion_flag INTEGER DEFAULT 0,
    queue TEXT,
    sealed_payload BLOB
)"""


def classify(action: str, reason: str | None, max_confidence: float) -> str:
    if action == "missed_report":
        return "missed"
    if action in ("sent_sanitized", "cancelled", "route_internal") and reason is None:
        return "pre_warning"
    if reason == "false_positive":
        return "tuning"
    if reason in ("business_need", "approved"):
        return "candidate"
    if action == "sent_original" and max_confidence >= HIGH:
        return "violation"
    return "candidate"


class EventLog:
    def __init__(self, folder: Path):
        self.folder = folder
        self.export_dir = folder / "logs"
        self.db = sqlite3.connect(folder / "events.db", check_same_thread=False)
        self.db.execute(_SCHEMA)
        self.db.commit()

    def record(self, *, service: str, grade: str, action: str, detect_types=(), counts=None,
               reverted_types=(), reasons=(), max_confidence: float = 0.0, reason: str | None = None,
               rule_ids=()) -> str:
        """reasons: 항목별 되돌림 사유들. 가장 무거운 사유가 이 이벤트의 사유가 된다."""
        if reason is None and reasons:
            reason = max(reasons, key=lambda r: REASON_PRIORITY.get(r, 0))
        effective = None if reason == "none" else reason
        if action == "sent_sanitized" and reasons and effective is None:
            queue = "candidate"  # 사유 없이 되돌린 항목이 있음
        else:
            queue = classify(action, effective, max_confidence)
        self.db.execute(
            "INSERT INTO events(ts, service, grade, action, reason, detect_types, counts, reverted_types,"
            " rule_ids, queue) VALUES (?,?,?,?,?,?,?,?,?,?)",
            (datetime.now().isoformat(timespec="seconds"), service, grade, action, reason,
             json.dumps(sorted(set(detect_types))), json.dumps(counts or {}, ensure_ascii=False),
             json.dumps(sorted(set(reverted_types))), json.dumps(list(rule_ids)), queue),
        )
        self.db.commit()
        return queue

    def stats(self) -> dict:
        rows = self.db.execute("SELECT queue, COUNT(*) FROM events GROUP BY queue").fetchall()
        queues = {q: n for q, n in rows}
        sent = self.db.execute(
            "SELECT SUM(action='sent_sanitized' AND reverted_types='[]'), "
            "SUM(action IN ('sent_sanitized','sent_original')) FROM events").fetchone()
        accepted, total = (sent[0] or 0), (sent[1] or 0)
        return {"queues": queues, "acceptance": (accepted / total) if total else None, "sent": total}

    def export_jsonl(self, day: str | None = None) -> Path:
        """회사 수집 체계가 가져갈 수 있도록 날짜별 JSONL로 내보낸다. 우리 도구는 전송하지 않는다."""
        day = day or datetime.now().strftime("%Y-%m-%d")
        self.export_dir.mkdir(exist_ok=True)
        path = self.export_dir / f"{day}.jsonl"
        cur = self.db.execute("SELECT * FROM events WHERE ts LIKE ? ORDER BY id", (f"{day}%",))
        cols = [c[0] for c in cur.description]
        with path.open("w", encoding="utf-8") as f:
            for row in cur:
                rec = dict(zip(cols, row))
                rec.pop("sealed_payload", None)
                for k in ("detect_types", "counts", "reverted_types", "rule_ids"):
                    rec[k] = json.loads(rec[k]) if rec[k] else None
                f.write(json.dumps(rec, ensure_ascii=False) + "\n")
        return path

    def close(self) -> None:
        self.db.close()
