# -*- coding: utf-8 -*-
"""卡片库 (card store)：直卡提链绑卡用的卡片信息。

支持:
  - 内置测试卡 (本次试验: 4000 0000 0000 0002 / 12/30 / 123)
  - SQLite 持久化 (cards 表), 可增删查
  - 轮询取卡 (按需调用, 每张卡可配置 max_uses)
"""
from __future__ import annotations

import json
import os
import random
import sqlite3
import threading
import time
from pathlib import Path
from typing import Any

_DB_PATH = os.environ.get("MIN_CARDS_DB", "")
if not _DB_PATH:
    _DB_PATH = str(Path(__file__).resolve().parent.parent / "cards.db")

# 内置测试卡 (本次试验) — 测试卡号属于敏感数据, 开源版占位, 运行时从环境变量注入
_BUILTIN_CARDS: list[dict[str, Any]] = [
    {
        "number": os.environ.get("MIN_TEST_CARD_NUMBER", "4000000000000002"),
        "exp_month": os.environ.get("MIN_TEST_CARD_EXP_MONTH", "12"),
        "exp_year": os.environ.get("MIN_TEST_CARD_EXP_YEAR", "30"),
        "cvc": os.environ.get("MIN_TEST_CARD_CVC", "123"),
        "name": "TEST CARD",
        "brand": "visa",
        "source": "builtin_test",
        "max_uses": 10,
        "uses": 0,
        "note": "test card placeholder",
    },
]

_schema = """
CREATE TABLE IF NOT EXISTS cards (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    number TEXT NOT NULL,
    exp_month TEXT NOT NULL,
    exp_year TEXT NOT NULL,
    cvc TEXT NOT NULL,
    name TEXT DEFAULT '',
    brand TEXT DEFAULT '',
    source TEXT DEFAULT '',
    max_uses INTEGER DEFAULT 10,
    uses INTEGER DEFAULT 0,
    note TEXT DEFAULT '',
    created_at TEXT DEFAULT (datetime('now')),
    updated_at TEXT DEFAULT (datetime('now'))
);
"""


class CardStore:
    """卡片库: SQLite 持久化 + 内置测试卡注入。"""

    def __init__(self, db_path: str = "") -> None:
        self.db_path = db_path or _DB_PATH
        self._lock = threading.Lock()
        self._ensure_schema()
        self._seed_builtin()

    def _connect(self) -> sqlite3.Connection:
        conn = sqlite3.connect(self.db_path, timeout=10)
        conn.row_factory = sqlite3.Row
        return conn

    def _ensure_schema(self) -> None:
        with self._lock, self._connect() as conn:
            conn.execute(_schema)
            conn.commit()

    def _seed_builtin(self) -> None:
        """内置测试卡不存在则注入。"""
        with self._lock, self._connect() as conn:
            cur = conn.execute("SELECT COUNT(*) FROM cards WHERE source='builtin_test'")
            if cur.fetchone()[0] == 0:
                for c in _BUILTIN_CARDS:
                    conn.execute(
                        "INSERT INTO cards (number, exp_month, exp_year, cvc, name, brand, source, max_uses, uses, note)"
                        " VALUES (?,?,?,?,?,?,?,?,?,?)",
                        (c["number"], c["exp_month"], c["exp_year"], c["cvc"],
                         c["name"], c["brand"], c["source"], c["max_uses"], c["uses"], c["note"]),
                    )
                conn.commit()

    # ---- 查询 ----

    def list_cards(self) -> list[dict[str, Any]]:
        with self._lock, self._connect() as conn:
            rows = conn.execute("SELECT * FROM cards ORDER BY id").fetchall()
        return [dict(r) for r in rows]

    def get_card(self, card_id: int) -> dict[str, Any] | None:
        with self._lock, self._connect() as conn:
            row = conn.execute("SELECT * FROM cards WHERE id=?", (card_id,)).fetchone()
        return dict(row) if row else None

    def pickup_card(self) -> dict[str, Any] | None:
        """取一张可用卡 (uses < max_uses), 并 uses+1。"""
        with self._lock, self._connect() as conn:
            row = conn.execute(
                "SELECT * FROM cards WHERE uses < max_uses ORDER BY uses ASC, id ASC LIMIT 1"
            ).fetchone()
            if row is None:
                return None
            conn.execute("UPDATE cards SET uses = uses + 1, updated_at=datetime('now') WHERE id=?", (row["id"],))
            conn.commit()
            return dict(row)

    # ---- 写入 ----

    def add_card(self, card: dict[str, Any]) -> int:
        with self._lock, self._connect() as conn:
            cur = conn.execute(
                "INSERT INTO cards (number, exp_month, exp_year, cvc, name, brand, source, max_uses, uses, note)"
                " VALUES (?,?,?,?,?,?,?,?,?,?)",
                (str(card["number"]), str(card.get("exp_month", "")), str(card.get("exp_year", "")),
                 str(card.get("cvc", "")), str(card.get("name", "")), str(card.get("brand", "")),
                 str(card.get("source", "manual")), int(card.get("max_uses", 10)), int(card.get("uses", 0)),
                 str(card.get("note", ""))),
            )
            conn.commit()
            return int(cur.lastrowid)

    def delete_card(self, card_id: int) -> bool:
        with self._lock, self._connect() as conn:
            cur = conn.execute("DELETE FROM cards WHERE id=?", (card_id,))
            conn.commit()
            return cur.rowcount > 0

    def bulk_delete_cards(self, card_ids: list[int]) -> int:
        """批量删除卡片，返回实际删除条数。"""
        if not card_ids:
            return 0
        with self._lock, self._connect() as conn:
            placeholders = ",".join("?" * len(card_ids))
            cur = conn.execute(
                f"DELETE FROM cards WHERE id IN ({placeholders})", card_ids)
            conn.commit()
            return cur.rowcount

    def reset_uses(self) -> int:
        with self._lock, self._connect() as conn:
            cur = conn.execute("UPDATE cards SET uses=0, updated_at=datetime('now')")
            conn.commit()
            return cur.rowcount


card_store = CardStore()
