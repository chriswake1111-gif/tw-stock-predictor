"""Local labels only; reads never refresh prices or create research records."""
import hashlib
import json
import os
import sqlite3
from contextlib import closing
from pathlib import Path

from src.domain.analysis_snapshot import canonical_json
from src.domain.universe import parse_canonical_symbol
from src.domain.valuation import utc_now_timestamp

MAX_SYMBOLS = 5000
MAX_COMMANDS = 20000
MAX_COMMAND_BYTES = 32 * 1024 * 1024


def library_enabled():
    return os.getenv("RESEARCH_LIBRARY_ENABLED", "true").lower() == "true"


def canonical(value):
    parse_canonical_symbol(value)
    return value.strip().upper()


class ResearchLibraryService:
    def __init__(self, db_path):
        self.db_path = str(db_path)

    def _connect(self, write=False):
        conn = sqlite3.connect(Path(self.db_path).resolve().as_uri() + ("?mode=rw" if write else "?mode=ro"), uri=True)
        conn.row_factory = sqlite3.Row
        return conn

    @staticmethod
    def _state(conn, symbol):
        held = conn.execute("SELECT * FROM research_holding_labels WHERE symbol=?", (symbol,)).fetchone()
        favorite = conn.execute("SELECT * FROM research_watchlist_items WHERE symbol=?", (symbol,)).fetchone()
        identity = [dict(held) if held else None, dict(favorite) if favorite else None]
        name = conn.execute("SELECT short_name,display_name FROM universe_instrument_revisions WHERE canonical_symbol=? ORDER BY ingested_at DESC,instrument_revision_id DESC LIMIT 1", (symbol,)).fetchone()
        saved = conn.execute("SELECT created_at,knowledge_cutoff_at FROM daily_research_entries WHERE symbol=? ORDER BY created_at DESC,entry_id DESC LIMIT 1", (symbol,)).fetchone()
        return {"symbol": symbol, "name": (name["short_name"] or name["display_name"]) if name else symbol,
                "held": bool(held and held["is_held"]), "favorite": bool(favorite and favorite["membership_state"] == "active"),
                "last_saved_at": saved["created_at"] if saved else None,
                "saved_cutoff_at": saved["knowledge_cutoff_at"] if saved else None,
                "version": hashlib.sha256(canonical_json(identity).encode()).hexdigest()}

    def state(self, symbol):
        symbol = canonical(symbol)
        if not library_enabled():
            return {"enabled": False}
        with closing(self._connect()) as conn, conn:
            conn.execute("BEGIN")
            return {"enabled": True, **self._state(conn, symbol)}

    def list(self, category="all", after="", limit=30):
        if category not in {"all", "held", "favorites", "researched"} or not 1 <= limit <= 100:
            raise ValueError("invalid_library_query")
        if after:
            after = canonical(after)
        if not library_enabled():
            return {"enabled": False, "items": [], "next_cursor": None}
        parts = []
        if category in {"all", "held"}:
            parts.append("SELECT symbol FROM research_holding_labels WHERE is_held=1")
        if category in {"all", "favorites"}:
            parts.append("SELECT symbol FROM research_watchlist_items WHERE membership_state='active'")
        if category in {"all", "researched"}:
            parts.append("SELECT DISTINCT symbol FROM daily_research_entries")
        with closing(self._connect()) as conn, conn:
            conn.execute("BEGIN")
            rows = conn.execute("SELECT symbol FROM (" + " UNION ".join(parts) + ") WHERE symbol>? ORDER BY symbol LIMIT ?", (after, limit + 1)).fetchall()
            items = [self._state(conn, row["symbol"]) for row in rows[:limit]]
            return {"enabled": True, "items": items, "next_cursor": items[-1]["symbol"] if len(rows) > limit else None}

    def set_label(self, symbol, label, value, version, key):
        symbol = canonical(symbol)
        if not library_enabled():
            raise ValueError("research_library_disabled")
        if label not in {"held", "favorite"} or type(value) is not bool or not 8 <= len(key) <= 128:
            raise ValueError("invalid_library_command")
        request = canonical_json(dict(symbol=symbol, label=label, value=value, version=version))
        with closing(self._connect(write=True)) as conn, conn:
            conn.execute("BEGIN IMMEDIATE")
            prior = conn.execute("SELECT * FROM research_library_commands WHERE command_key=?", (key,)).fetchone()
            if prior:
                if prior["request_json"] != request:
                    raise ValueError("research_idempotency_conflict")
                return json.loads(prior["result_json"])
            state = self._state(conn, symbol)
            if state["version"] != version:
                raise ValueError("research_library_state_conflict")
            known = state["last_saved_at"] or conn.execute("SELECT 1 FROM research_watchlist_items WHERE symbol=?", (symbol,)).fetchone() or conn.execute("SELECT 1 FROM universe_instrument_revisions WHERE canonical_symbol=? AND status IN ('accepted','partial') LIMIT 1", (symbol,)).fetchone()
            if not known:
                raise ValueError("research_library_unknown_symbol")
            if conn.execute("SELECT COUNT(*) FROM research_library_commands").fetchone()[0] >= MAX_COMMANDS:
                raise ValueError("research_library_storage_full")
            existing = conn.execute("SELECT 1 FROM research_holding_labels WHERE symbol=?", (symbol,)).fetchone()
            if not existing and conn.execute("SELECT COUNT(*) FROM research_holding_labels").fetchone()[0] >= MAX_SYMBOLS:
                raise ValueError("research_library_storage_full")
            now = utc_now_timestamp()
            if state[label] != value:
                held = value if label == "held" else state["held"]
                conn.execute("INSERT INTO research_holding_labels VALUES (?,?,1,?) ON CONFLICT(symbol) DO UPDATE SET is_held=excluded.is_held,revision=revision+1,updated_at=excluded.updated_at", (symbol, int(held), now))
                if label == "favorite":
                    old = conn.execute("SELECT 1 FROM research_watchlist_items WHERE symbol=?", (symbol,)).fetchone()
                    membership = "active" if value else "archived"
                    if old:
                        conn.execute("UPDATE research_watchlist_items SET membership_state=?,updated_at=?,archived_at=? WHERE symbol=?", (membership, now, None if value else now, symbol))
                    else:
                        item_id = "research_watchlist_" + hashlib.sha256(symbol.encode()).hexdigest()[:24]
                        conn.execute("INSERT INTO research_watchlist_items VALUES (?,?,?,?,?,?,?)", (item_id, symbol, membership, now, now, None if value else now, "research_review_queue_v1"))
            result = {"enabled": True, **self._state(conn, symbol)}
            result_json = canonical_json(result)
            size = len(request.encode()) + len(result_json.encode()) + len(key.encode()) + len(now.encode())
            used = conn.execute("SELECT COALESCE(SUM(length(CAST(request_json AS BLOB))+length(CAST(result_json AS BLOB))+length(CAST(command_key AS BLOB))+length(CAST(created_at AS BLOB))),0) FROM research_library_commands").fetchone()[0]
            if size > 16 * 1024 or used + size > MAX_COMMAND_BYTES:
                raise ValueError("research_library_storage_full")
            conn.execute("INSERT INTO research_library_commands VALUES (?,?,?,?)", (key, request, result_json, now))
            return result
