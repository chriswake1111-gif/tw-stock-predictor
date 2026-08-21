"""Read-only/as-of and guarded immutable persistence for Universe Foundation."""

from __future__ import annotations

import json
import sqlite3
import uuid
from contextlib import contextmanager
from datetime import datetime
from typing import Any, Iterator

from src.domain.universe import (
    AvailabilityMode,
    FreshnessMode,
    FreshnessStatus,
    ListingStatus,
    MembershipState,
    TradingState,
    UniverseVenue,
    canonical_symbol_for,
    coerce_venue,
    identity_binding_fingerprint,
    normalize_universe_timestamp,
    parse_source_temporal,
    parse_canonical_symbol,
    validate_knowledge_cutoff_at,
    validate_official_code,
)
from src.domain.valuation import utc_now_timestamp
from src.repositories.migration_runner import apply_valuation_migration
from src.services.universe_write_guard import (
    UniverseOperatorContext,
    UniverseWriteGuard,
)


class UniverseStorageUnavailable(RuntimeError):
    code = "universe_storage_unavailable"


class UniverseIdempotencyConflict(ValueError):
    code = "idempotency_key_reused"


class UniverseIdentityCollision(ValueError):
    code = "identity_collision"


def _json(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


class UniverseRepository:
    """Universe repository.  Construction and reads never run migrations or write rows."""

    def __init__(self, db_path: str = "data/cache.db", *, guard: UniverseWriteGuard | None = None,
                 auto_migrate: bool = False):
        self.db_path = db_path
        self.guard = guard or UniverseWriteGuard()
        if auto_migrate:
            # Explicit opt-in is useful for operator tooling, never used by API reads.
            apply_valuation_migration(db_path)

    def _connect(self) -> sqlite3.Connection:
        try:
            conn = sqlite3.connect(self.db_path, timeout=5)
        except sqlite3.Error as exc:
            raise UniverseStorageUnavailable(str(exc)) from exc
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA foreign_keys = ON")
        try:
            exists = conn.execute(
                "SELECT 1 FROM sqlite_master WHERE type='table' AND name='universe_instruments'"
            ).fetchone()
        except sqlite3.Error as exc:
            conn.close()
            raise UniverseStorageUnavailable(str(exc)) from exc
        if exists is None:
            conn.close()
            raise UniverseStorageUnavailable("phase13 migration is not available")
        return conn

    @contextmanager
    def read_transaction(self) -> Iterator[sqlite3.Connection]:
        conn = self._connect()
        try:
            conn.execute("BEGIN")
            yield conn
        except Exception:
            conn.rollback()
            raise
        finally:
            conn.close()

    @staticmethod
    def _row(row: sqlite3.Row | None) -> dict[str, Any] | None:
        return dict(row) if row else None

    @staticmethod
    def _cutoff_where(alias: str = "r") -> str:
        return f"({alias}.available_at IS NULL OR {alias}.available_at <= ?) AND {alias}.ingested_at <= ?"

    @staticmethod
    def _reference(row: dict[str, Any] | None) -> dict[str, Any] | None:
        if not row:
            return None
        return {
            "instrument_id": row.get("instrument_id"),
            "canonical_symbol": row.get("canonical_symbol"),
            "official_code": row.get("official_code"),
            "venue": row.get("venue"),
            "display_name": row.get("display_name"),
            "security_type": row.get("security_type", "unknown"),
            "listing_status": row.get("listing_status", "unknown"),
            "trading_state": row.get("trading_state", "unknown"),
            "membership_state": row.get("membership_state", "unknown"),
            "source_reference": row.get("source_reference"),
            "mapping_basis": row.get("mapping_basis"),
        }

    @classmethod
    def _safe_dto(cls, revision: dict[str, Any] | None, *, cutoff: str) -> dict[str, Any] | None:
        if not revision:
            return None
        freshness = {
            "freshness": revision.get("freshness_status", FreshnessStatus.UNKNOWN.value),
            "current_complete": bool(revision.get("current_complete", False)),
            "freshness_mode": revision.get("freshness_mode"),
            "availability_mode": revision.get("availability_mode"),
            "latest_visible_state": revision.get("status"),
            "reasons": [revision["reason"]] if revision.get("reason") else [],
        }
        return {
            "status": revision.get("public_status", "partial"),
            "status_policy_version": "universe_status_matrix_v1",
            "knowledge_cutoff_at": cutoff,
            "cutoff_policy": {"type": "aware_timestamp", "no_end_of_day_expansion": True},
            "identity_reference": cls._reference(revision),
            "operational_freshness": freshness,
            "effective": {
                "source_effective_date": revision.get("source_effective_date"),
                "source_effective_at": revision.get("source_effective_at"),
                "available_at": revision.get("available_at"),
                "ingested_at": revision.get("ingested_at"),
            },
            "provenance": {
                "resource_id": revision.get("resource_id"),
                "universe_revision_id": revision.get("universe_revision_id"),
                "instrument_revision_id": revision.get("instrument_revision_id"),
                "source_reference": revision.get("source_reference"),
                "parser_version": revision.get("parser_version"),
            },
            "reasons": [revision["reason"]] if revision.get("reason") else [],
        }

    def _find_revision(self, conn: sqlite3.Connection, *, instrument_id: str,
                       cutoff: str, current: bool = False) -> dict[str, Any] | None:
        cutoff = validate_knowledge_cutoff_at(cutoff)
        if current:
            # Select the actual latest row first.  A blocking latest row must not fall back.
            row = conn.execute(
                """
                SELECT r.*, i.venue, i.official_code, i.identity_epoch, i.identity_binding_fingerprint,
                       i.first_observed_at AS anchor_first_observed_at
                FROM universe_instrument_revisions r
                JOIN universe_instruments i ON i.instrument_id = r.instrument_id
                WHERE r.instrument_id = ?
                ORDER BY r.ingested_at DESC, COALESCE(r.available_at,'' ) DESC, r.revision_number DESC
                LIMIT 1
                """, (instrument_id,)
            ).fetchone()
        else:
            row = conn.execute(
                f"""
                SELECT r.*, i.venue, i.official_code, i.identity_epoch, i.identity_binding_fingerprint,
                       i.first_observed_at AS anchor_first_observed_at
                FROM universe_instrument_revisions r
                JOIN universe_instruments i ON i.instrument_id = r.instrument_id
                WHERE r.instrument_id = ? AND {self._cutoff_where('r')}
                ORDER BY COALESCE(r.available_at,'' ) DESC, r.ingested_at DESC, r.revision_number DESC
                LIMIT 1
                """, (instrument_id, cutoff, cutoff)
            ).fetchone()
        return self._row(row)

    def _decorate(self, revision: dict[str, Any] | None, *, cutoff: str, current: bool) -> dict[str, Any]:
        from src.services.universe_status_service import evaluate_universe_status
        if revision is None:
            result = evaluate_universe_status(None, reasons=("instrument_not_found",))
            return {
                "status": result["status"],
                "status_policy_version": result["status_policy_version"],
                "knowledge_cutoff_at": cutoff,
                "cutoff_policy": {"type": "aware_timestamp", "no_end_of_day_expansion": True},
                "identity_reference": None,
                "operational_freshness": {"freshness": "unknown", "current_complete": False, "reasons": ["instrument_not_found"]},
                "reasons": ["instrument_not_found"],
            }
        reasons: list[str] = []
        status = str(revision.get("status"))
        if status == "awaiting_review":
            reasons.append("source_revision_awaiting_review")
        elif status == "schema_changed":
            reasons.append("source_schema_review_required")
        elif status == "provider_error":
            reasons.append("source_provider_error")
        elif status == "partial":
            reasons.append("source_revision_partial")
        elif status == "revoked":
            reasons.append("source_revision_revoked_without_corrected_revision")
        if revision.get("reason"):
            reasons.append(str(revision["reason"]))
        if revision.get("canonical_symbol") is None:
            reasons.append("canonical_mapping_unverified")
        freshness = revision.get("freshness_status") or "unknown"
        if freshness == "stale":
            reasons.append("freshness_stale")
        elif freshness == "unknown":
            reasons.append("freshness_unknown")
        if current and not revision.get("current_complete"):
            reasons.append("current_freshness_blocked")
        from src.services.universe_status_service import evaluate_universe_status
        result = evaluate_universe_status(self._reference(revision), freshness=freshness,
                                          current_complete=bool(revision.get("current_complete")), reasons=tuple(reasons))
        revision["public_status"] = result["status"]
        revision["reason"] = reasons[0] if reasons else revision.get("reason")
        return {**self._safe_dto(revision, cutoff=cutoff), "reasons": list(dict.fromkeys(reasons))}

    def get_by_instrument_id(self, instrument_id: str, *, knowledge_cutoff_at: str,
                             current: bool = False) -> dict[str, Any]:
        cutoff = validate_knowledge_cutoff_at(knowledge_cutoff_at)
        with self.read_transaction() as conn:
            row = self._find_revision(conn, instrument_id=instrument_id, cutoff=cutoff, current=current)
            return self._decorate(row, cutoff=cutoff, current=current)

    def get_instrument(self, instrument_id: str, *, knowledge_cutoff_at: str, current: bool = False) -> dict[str, Any]:
        return self.get_by_instrument_id(instrument_id, knowledge_cutoff_at=knowledge_cutoff_at, current=current)

    def get_by_canonical(self, canonical_symbol: str, *, knowledge_cutoff_at: str,
                         current: bool = False) -> dict[str, Any]:
        venue, code = parse_canonical_symbol(canonical_symbol)
        cutoff = validate_knowledge_cutoff_at(knowledge_cutoff_at)
        with self.read_transaction() as conn:
            query = """
                SELECT r.*, i.venue, i.official_code, i.identity_epoch, i.identity_binding_fingerprint,
                       i.first_observed_at AS anchor_first_observed_at
                FROM universe_instrument_revisions r
                JOIN universe_instruments i ON i.instrument_id = r.instrument_id
                WHERE i.venue = ? AND i.official_code = ?
            """
            params: list[Any] = [venue.value, code]
            if not current:
                query += f" AND {self._cutoff_where('r')}"
                params += [cutoff, cutoff]
            query += " ORDER BY r.ingested_at DESC, COALESCE(r.available_at,'' ) DESC, r.revision_number DESC LIMIT 1"
            row = self._row(conn.execute(query, params).fetchone())
            return self._decorate(row, cutoff=cutoff, current=current)

    def get_by_symbol(self, canonical_symbol: str, *, knowledge_cutoff_at: str, current: bool = False) -> dict[str, Any]:
        return self.get_by_canonical(canonical_symbol, knowledge_cutoff_at=knowledge_cutoff_at, current=current)

    def find_by_canonical_symbol(self, canonical_symbol: str, *, knowledge_cutoff_at: str, current: bool = False) -> dict[str, Any]:
        return self.get_by_canonical(canonical_symbol, knowledge_cutoff_at=knowledge_cutoff_at, current=current)

    def resolve(self, *, official_code: str, venue: UniverseVenue | str,
                knowledge_cutoff_at: str, current: bool = False) -> dict[str, Any]:
        code = validate_official_code(official_code)
        venue_value = coerce_venue(venue)
        return self.get_by_canonical(canonical_symbol_for(venue_value, code), knowledge_cutoff_at=knowledge_cutoff_at, current=current)

    def resolve_by_code_venue(self, *, official_code: str, venue: UniverseVenue | str,
                              knowledge_cutoff_at: str, current: bool = False) -> dict[str, Any]:
        return self.resolve(official_code=official_code, venue=venue, knowledge_cutoff_at=knowledge_cutoff_at, current=current)

    def list_instruments(self, *, knowledge_cutoff_at: str, query: str | None = None,
                         venue: UniverseVenue | str | None = None, security_type: str | None = None,
                         listing_status: ListingStatus | str | None = None, limit: int = 25,
                         cursor: str | None = None, current: bool = False) -> dict[str, Any]:
        cutoff = validate_knowledge_cutoff_at(knowledge_cutoff_at)
        if not 1 <= int(limit) <= 100:
            raise ValueError("limit must be between 1 and 100")
        venue_value = coerce_venue(venue).value if venue else None
        listing_value = ListingStatus(str(listing_status).lower()).value if listing_status else None
        with self.read_transaction() as conn:
            # Window selection keeps one latest visible revision per identity.
            visibility = "" if current else f"AND {self._cutoff_where('r')}"
            params: list[Any] = []
            clauses = ["1=1"]
            if venue_value:
                clauses.append("i.venue = ?"); params.append(venue_value)
            if security_type:
                clauses.append("r.security_type = ?"); params.append(security_type)
            if listing_value:
                clauses.append("r.listing_status = ?"); params.append(listing_value)
            if query:
                clauses.append("(r.official_code LIKE ? OR COALESCE(r.canonical_symbol,'') LIKE ? OR COALESCE(r.display_name,'') LIKE ?)")
                term = f"%{query.strip().upper()}%"; params += [term, term, term]
            # cursor is an opaque, cutoff/filter-bound JSON token.
            if cursor:
                try:
                    token = json.loads(cursor)
                    if not isinstance(token, dict):
                        raise ValueError("cursor_query_mismatch")
                    if (
                        token.get("cutoff") != cutoff
                        or token.get("venue") != venue_value
                        or token.get("query") != (query or "")
                        or token.get("security_type") != (security_type or "")
                        or token.get("listing_status") != (listing_value or "")
                        or token.get("order") != "venue ASC, canonical_symbol ASC, instrument_id ASC"
                    ):
                        raise ValueError("cursor_query_mismatch")
                    clauses.append("(i.venue, COALESCE(r.canonical_symbol,''), i.instrument_id) > (?, ?, ?)")
                    params += [token["venue_key"], token["canonical_key"], token["instrument_id"]]
                except (json.JSONDecodeError, KeyError, TypeError):
                    raise ValueError("cursor_query_mismatch")
            if not current:
                params += [cutoff, cutoff]
            sql = f"""
                WITH ranked AS (
                    SELECT r.*, i.venue AS anchor_venue, i.official_code AS anchor_code,
                           i.identity_epoch, i.identity_binding_fingerprint,
                           ROW_NUMBER() OVER (PARTITION BY r.instrument_id ORDER BY r.ingested_at DESC, COALESCE(r.available_at,'') DESC, r.revision_number DESC) AS rn
                    FROM universe_instrument_revisions r JOIN universe_instruments i ON i.instrument_id=r.instrument_id
                    WHERE {' AND '.join(clauses)} {visibility}
                )
                SELECT * FROM ranked WHERE rn=1
                ORDER BY anchor_venue ASC, COALESCE(canonical_symbol,'') ASC, instrument_id ASC LIMIT ?
            """
            params.append(int(limit) + 1)
            rows = [dict(row) for row in conn.execute(sql, params).fetchall()]
        items = []
        for row in rows[:limit]:
            row["venue"] = row.pop("anchor_venue", row.get("venue"))
            row["official_code"] = row.pop("anchor_code", row.get("official_code"))
            items.append(self._decorate(row, cutoff=cutoff, current=current))
        next_cursor = None
        if len(rows) > limit and items:
            last = rows[limit - 1]
            next_cursor = json.dumps({"cutoff": cutoff, "venue": venue_value, "query": query or "",
                                      "security_type": security_type or "", "listing_status": listing_value or "",
                                      "order": "venue ASC, canonical_symbol ASC, instrument_id ASC",
                                      "venue_key": last["anchor_venue"], "canonical_key": last.get("canonical_symbol") or "",
                                      "instrument_id": last["instrument_id"]}, separators=(",", ":"))
        scoped = [venue_value] if venue_value else ["TWSE", "TPEX"]
        from src.services.universe_status_service import compose_list_status
        return {"status": compose_list_status(items, scoped), "status_policy_version": "universe_status_matrix_v1",
                "knowledge_cutoff_at": cutoff, "cutoff_policy": {"type": "aware_timestamp", "no_end_of_day_expansion": True},
                "items": items, "per_venue_status": {v: self._venue_status(items, v) for v in scoped}, "next_cursor": next_cursor,
                "order": "venue ASC, canonical_symbol ASC, instrument_id ASC", "limit": int(limit)}

    def search(self, **kwargs: Any) -> dict[str, Any]:
        return self.list_instruments(**kwargs)

    @staticmethod
    def _venue_status(items: list[dict[str, Any]], venue: str) -> str:
        rows = [item for item in items if (item.get("identity_reference") or {}).get("venue") == venue]
        if not rows:
            return "insufficient_data"
        if any(item["status"] == "needs_human_input" for item in rows): return "needs_human_input"
        if any(item["status"] == "partial" for item in rows): return "partial"
        if any(item["status"] == "available" for item in rows): return "available"
        return "insufficient_data"

    # ----- Guarded mutation paths used by operator/CLI ingestion -----
    def _require_context(self, context: UniverseOperatorContext | None) -> UniverseOperatorContext:
        return self.guard.require_enabled(context)

    def allocate_instrument(self, *, venue: UniverseVenue | str, official_code: str,
                            source_identity: str, first_observed_at: str, source_reference: str,
                            context: UniverseOperatorContext | None = None,
                            continuity_proven: bool = False) -> dict[str, Any]:
        ctx = self._require_context(context)
        venue_value = coerce_venue(venue)
        code = validate_official_code(official_code)
        first_observed = normalize_universe_timestamp(first_observed_at, "first_observed_at")
        source_identity = str(source_identity).strip()
        source_reference = str(source_reference).strip()
        if not source_identity or not source_reference:
            raise ValueError("source_identity and source_reference are required")
        with self._connect() as conn:
            conn.execute("BEGIN IMMEDIATE")
            binding = identity_binding_fingerprint(venue_value, code, 1, source_identity)
            existing = conn.execute("SELECT * FROM universe_instruments WHERE identity_binding_fingerprint=?", (binding,)).fetchone()
            if existing:
                return {**dict(existing), "created": False}
            same = conn.execute("SELECT * FROM universe_instruments WHERE venue=? AND official_code=? ORDER BY identity_epoch DESC LIMIT 1", (venue_value.value, code)).fetchone()
            epoch = 1
            if same:
                if continuity_proven:
                    return {**dict(same), "created": False, "continuity_reused": True}
                terminated = conn.execute("SELECT 1 FROM universe_lifecycle_events WHERE instrument_id=? AND event_type='terminated' AND status='accepted' LIMIT 1", (same["instrument_id"],)).fetchone()
                if not terminated:
                    raise UniverseIdentityCollision("identity_collision")
                epoch = int(same["identity_epoch"]) + 1
            binding = identity_binding_fingerprint(venue_value, code, epoch, source_identity)
            instrument_id = f"uinstr_{binding[:24]}"
            conn.execute("INSERT INTO universe_instruments VALUES (?,?,?,?,?,?,?,?,?,?)", (instrument_id, venue_value.value, code, epoch, binding, first_observed, source_reference, source_identity, None, utc_now_timestamp()))
            return {**dict(conn.execute("SELECT * FROM universe_instruments WHERE instrument_id=?", (instrument_id,)).fetchone()), "created": True, "actor_id": ctx.actor_id}

    def add_revision(self, *, instrument_id: str, resource_id: str, logical_revision_key: str,
                     revision_number: int, payload: dict[str, Any], context: UniverseOperatorContext | None = None,
                     idempotency_key: str | None = None, actor_id: str | None = None) -> dict[str, Any]:
        ctx = self._require_context(context)
        actor = actor_id or ctx.actor_id
        fetched_at = normalize_universe_timestamp(payload["fetched_at"], "fetched_at")
        received_at = normalize_universe_timestamp(payload["received_at"], "received_at")
        ingested_at = normalize_universe_timestamp(payload["ingested_at"], "ingested_at")
        available_at = normalize_universe_timestamp(payload["available_at"], "available_at") if payload.get("available_at") else None
        first_observed_at = normalize_universe_timestamp(payload["first_observed_at"], "first_observed_at") if payload.get("first_observed_at") else None
        source_published_at = payload.get("source_published_at")
        if source_published_at and "T" in str(source_published_at):
            source_published_at = normalize_universe_timestamp(str(source_published_at), "source_published_at")
        if payload.get("source_effective_date"):
            parse_source_temporal(str(payload["source_effective_date"]), "source_effective_date")
        source_effective_at = payload.get("source_effective_at")
        if source_effective_at:
            source_effective_at = normalize_universe_timestamp(str(source_effective_at), "source_effective_at")
        effective_from = payload.get("effective_from")
        effective_to = payload.get("effective_to")
        if effective_from:
            effective_from = normalize_universe_timestamp(str(effective_from), "effective_from")
        if effective_to:
            effective_to = normalize_universe_timestamp(str(effective_to), "effective_to")
        if effective_from and effective_to and effective_from >= effective_to:
            raise ValueError("effective_from must be earlier than effective_to")
        current_complete = (
            payload.get("status", "accepted") == "accepted"
            and bool(payload.get("current_complete", False))
            and payload.get("freshness_status", FreshnessStatus.UNKNOWN.value) == FreshnessStatus.CURRENT.value
            and payload.get("freshness_mode", FreshnessMode.UNKNOWN_WITHOUT_OFFICIAL_CADENCE.value)
            in {FreshnessMode.OFFICIAL_CADENCE_WINDOW.value, FreshnessMode.LICENSED_REFERENCE.value}
        )
        if idempotency_key is not None and not str(idempotency_key).strip():
            raise ValueError("idempotency_key cannot be blank")
        fingerprint = _json(payload)
        import hashlib
        fingerprint = hashlib.sha256(fingerprint.encode()).hexdigest()
        with self._connect() as conn:
            conn.execute("BEGIN IMMEDIATE")
            policy = conn.execute(
                """SELECT p.availability_mode, p.enabled, p.resource_role, r.market, r.logical_resource_key
                   FROM universe_resource_policies p
                   JOIN data_resources r ON r.resource_id = p.resource_id
                   WHERE p.resource_id=?""",
                (resource_id,),
            ).fetchone()
            if policy is None or not int(policy["enabled"]):
                raise ValueError("universe_resource_not_registered")
            payload_venue = coerce_venue(payload["venue"])
            payload_code = validate_official_code(payload["official_code"])
            if str(policy["market"]).upper() != payload_venue.value:
                raise ValueError("universe_resource_venue_mismatch")
            status = payload.get("status", "accepted")
            reason = payload.get("reason")
            publication_evidence_id = payload.get("publication_evidence_id")
            if policy["availability_mode"] == AvailabilityMode.MANUAL_PUBLICATION_EVIDENCE_REQUIRED.value and not publication_evidence_id:
                status = "awaiting_review"
                reason = "manual_publication_evidence_required"
            if status != "accepted":
                current_complete = False
            if idempotency_key:
                old = conn.execute("SELECT * FROM universe_ingestion_idempotency WHERE idempotency_key=?", (idempotency_key,)).fetchone()
                if old:
                    if old["payload_fingerprint"] != fingerprint:
                        raise UniverseIdempotencyConflict("idempotency_key_reused")
                    row = conn.execute("SELECT * FROM universe_revisions WHERE universe_revision_id=?", (old["universe_revision_id"],)).fetchone()
                    return {**dict(row), "created": False, "idempotent": True}
            # A different idempotency key carrying the same payload must bind to
            # the existing immutable revision rather than attempting a new
            # revision (and therefore must not require a new supersession link).
            existing = conn.execute(
                "SELECT * FROM universe_ingestion_idempotency WHERE resource_id=? AND payload_fingerprint=?",
                (resource_id, fingerprint),
            ).fetchone()
            if existing:
                if idempotency_key:
                    try:
                        conn.execute(
                            "INSERT INTO universe_ingestion_idempotency VALUES (?,?,?,?,?,?)",
                            (idempotency_key, fingerprint, resource_id, existing["universe_revision_id"], actor, ingested_at),
                        )
                    except sqlite3.IntegrityError:
                        raise UniverseIdempotencyConflict("idempotency_key_reused") from None
                row = conn.execute(
                    "SELECT * FROM universe_revisions WHERE universe_revision_id=?",
                    (existing["universe_revision_id"],),
                ).fetchone()
                return {**dict(row), "created": False, "idempotent": True}
            previous = conn.execute(
                "SELECT * FROM universe_revisions WHERE resource_id=? AND logical_revision_key=? ORDER BY revision_number DESC LIMIT 1",
                (resource_id, logical_revision_key),
            ).fetchone()
            supersedes = payload.get("supersedes_revision_id")
            if previous and supersedes != previous["universe_revision_id"]:
                raise ValueError("corrected universe revision must supersede the latest revision")
            if previous is None and supersedes is not None:
                raise ValueError("first universe revision cannot supersede another revision")
            canonical_symbol = payload.get("canonical_symbol")
            mapping_basis = payload.get("mapping_basis")
            if canonical_symbol is None and policy["resource_role"] in {"master_snapshot", "corroborating_identity_observation"}:
                canonical_symbol = canonical_symbol_for(payload_venue, payload_code)
                mapping_basis = "approved_resource_scope"
            if canonical_symbol:
                try:
                    canonical_venue, canonical_code = parse_canonical_symbol(canonical_symbol)
                except ValueError:
                    canonical_venue = canonical_code = None
                if canonical_venue is None or canonical_venue is not payload_venue or canonical_code != payload_code:
                    canonical_symbol = None
                    if status == "accepted":
                        reason = "canonical_mapping_unverified"
            listing_status = payload.get("listing_status", ListingStatus.UNKNOWN.value)
            trading_state = payload.get("trading_state", TradingState.UNKNOWN.value)
            membership_state = payload.get("membership_state", MembershipState.UNKNOWN.value)
            if status != "accepted":
                listing_status = ListingStatus.UNKNOWN.value
                trading_state = TradingState.UNKNOWN.value
                membership_state = MembershipState.PARTIAL.value if status == "partial" else MembershipState.BLOCKED.value
            previous_instrument = conn.execute(
                "SELECT instrument_revision_id FROM universe_instrument_revisions WHERE instrument_id=? ORDER BY revision_number DESC LIMIT 1",
                (instrument_id,),
            ).fetchone()
            instrument_supersedes = previous_instrument["instrument_revision_id"] if previous_instrument else None
            revision_id = f"urev_{uuid.uuid4().hex}"
            conn.execute("""INSERT INTO universe_revisions
                    (universe_revision_id,resource_id,logical_revision_key,revision_number,source_published_at,source_effective_date,fetched_at,received_at,first_observed_at,available_at,ingested_at,status,reason,payload_sha256,schema_fingerprint,parser_version,source_reference,publication_evidence_id,supersedes_revision_id)
                    VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""", (
                    revision_id, resource_id, logical_revision_key, int(revision_number), source_published_at, payload.get("source_effective_date"), fetched_at, received_at, first_observed_at, available_at, ingested_at, status, reason, fingerprint, payload.get("schema_fingerprint"), payload.get("parser_version"), payload.get("source_reference"), publication_evidence_id, payload.get("supersedes_revision_id")))
            conn.execute("""INSERT INTO universe_instrument_revisions
                    (instrument_revision_id,instrument_id,universe_revision_id,resource_id,revision_number,venue,official_code,canonical_symbol,mapping_basis,security_type,display_name,listing_status,trading_state,membership_state,source_effective_date,source_effective_at,source_published_at,first_observed_at,received_at,fetched_at,available_at,ingested_at,availability_mode,freshness_mode,freshness_status,current_complete,coverage_complete,status,reason,source_reference,payload_sha256,schema_fingerprint,parser_version,effective_from,effective_to,supersedes_revision_id)
                    VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""", (
                    f"uirev_{uuid.uuid4().hex}", instrument_id, revision_id, resource_id, int(revision_number), payload_venue.value, payload_code, canonical_symbol, mapping_basis, payload.get("security_type","unknown"), payload.get("display_name"), listing_status, trading_state, membership_state, payload.get("source_effective_date"), source_effective_at, source_published_at, first_observed_at, received_at, fetched_at, available_at, ingested_at, payload.get("availability_mode", AvailabilityMode.CONSERVATIVE_FIRST_OBSERVED.value), payload.get("freshness_mode", FreshnessMode.UNKNOWN_WITHOUT_OFFICIAL_CADENCE.value), payload.get("freshness_status", FreshnessStatus.UNKNOWN.value), int(current_complete), int(bool(payload.get("coverage_complete", False))), status, reason, payload.get("source_reference"), fingerprint, payload.get("schema_fingerprint"), payload.get("parser_version"), effective_from, effective_to, instrument_supersedes))
            if idempotency_key:
                try:
                    conn.execute("INSERT INTO universe_ingestion_idempotency VALUES (?,?,?,?,?,?)", (idempotency_key, fingerprint, resource_id, revision_id, actor, ingested_at))
                except sqlite3.IntegrityError:
                    raise UniverseIdempotencyConflict("idempotency_key_reused") from None
            row = conn.execute("SELECT * FROM universe_revisions WHERE universe_revision_id=?", (revision_id,)).fetchone()
            return {**dict(row), "created": existing is None, "idempotent": False}

    def add_lifecycle_event(self, *, instrument_id: str, event_type: str, available_at: str,
                            ingested_at: str, source_reference: str, reason: str,
                            context: UniverseOperatorContext | None = None, event_date: str | None = None,
                            effective_at: str | None = None, status: str = "accepted") -> dict[str, Any]:
        ctx = self._require_context(context)
        if event_type not in {"listed", "terminated", "resumed", "successor"}:
            raise ValueError("unsupported lifecycle event")
        available = normalize_universe_timestamp(available_at, "available_at")
        ingested = normalize_universe_timestamp(ingested_at, "ingested_at")
        effective = normalize_universe_timestamp(effective_at, "effective_at") if effective_at else None
        if not source_reference.strip() or not reason.strip():
            raise ValueError("source_reference and reason are required")
        event_id = f"ulife_{uuid.uuid4().hex}"
        with self._connect() as conn:
            conn.execute("BEGIN IMMEDIATE")
            conn.execute("INSERT INTO universe_lifecycle_events VALUES (?,?,?,?,?,?,?,?,?,?)",
                         (event_id, instrument_id, event_type, event_date, effective, available, ingested, source_reference.strip(), status, reason.strip()))
            return {**dict(conn.execute("SELECT * FROM universe_lifecycle_events WHERE lifecycle_event_id=?", (event_id,)).fetchone()), "actor_id": ctx.actor_id}

    def add_operational_event(self, *, instrument_id: str, trading_state: str, available_at: str,
                              ingested_at: str, source_reference: str, reason: str,
                              context: UniverseOperatorContext | None = None, effective_at: str | None = None,
                              status: str = "accepted") -> dict[str, Any]:
        ctx = self._require_context(context)
        if trading_state not in {"normal", "suspended", "altered", "periodic", "managed", "unknown"}:
            raise ValueError("unsupported trading_state")
        available = normalize_universe_timestamp(available_at, "available_at")
        ingested = normalize_universe_timestamp(ingested_at, "ingested_at")
        effective = normalize_universe_timestamp(effective_at, "effective_at") if effective_at else None
        if not source_reference.strip() or not reason.strip():
            raise ValueError("source_reference and reason are required")
        event_id = f"uoper_{uuid.uuid4().hex}"
        with self._connect() as conn:
            conn.execute("BEGIN IMMEDIATE")
            conn.execute("INSERT INTO universe_operational_state_events VALUES (?,?,?,?,?,?,?,?,?)",
                         (event_id, instrument_id, trading_state, effective, available, ingested, source_reference.strip(), status, reason.strip()))
            return {**dict(conn.execute("SELECT * FROM universe_operational_state_events WHERE operational_event_id=?", (event_id,)).fetchone()), "actor_id": ctx.actor_id}

    def add_alias_event(self, *, from_instrument_id: str, to_instrument_id: str, alias_code: str,
                        alias_venue: UniverseVenue | str, alias_type: str, available_at: str,
                        ingested_at: str, source_reference: str, reason: str,
                        context: UniverseOperatorContext | None = None, effective_at: str | None = None) -> dict[str, Any]:
        ctx = self._require_context(context)
        if from_instrument_id == to_instrument_id:
            raise ValueError("alias event must link distinct instrument anchors")
        if alias_type not in {"successor", "previous_code", "official_alias"}:
            raise ValueError("unsupported alias_type")
        venue = coerce_venue(alias_venue)
        code = validate_official_code(alias_code)
        available = normalize_universe_timestamp(available_at, "available_at")
        ingested = normalize_universe_timestamp(ingested_at, "ingested_at")
        effective = normalize_universe_timestamp(effective_at, "effective_at") if effective_at else None
        if not source_reference.strip() or not reason.strip():
            raise ValueError("source_reference and reason are required")
        event_id = f"ualias_{uuid.uuid4().hex}"
        with self._connect() as conn:
            conn.execute("BEGIN IMMEDIATE")
            conn.execute("INSERT INTO universe_identity_alias_events VALUES (?,?,?,?,?,?,?,?,?,?,?)",
                         (event_id, from_instrument_id, to_instrument_id, code, venue.value, alias_type, effective, available, ingested, source_reference.strip(), reason.strip()))
            return {**dict(conn.execute("SELECT * FROM universe_identity_alias_events WHERE alias_event_id=?", (event_id,)).fetchone()), "actor_id": ctx.actor_id}


class UniverseIdentityRepository:
    def __init__(self, db_path: str = "data/cache.db", *, repository: UniverseRepository | None = None,
                 guard: UniverseWriteGuard | None = None):
        self.repository = repository or UniverseRepository(db_path, guard=guard)

    def allocate(self, **kwargs: Any) -> dict[str, Any]:
        return self.repository.allocate_instrument(**kwargs)

    def get_or_create(self, **kwargs: Any) -> dict[str, Any]:
        return self.allocate(**kwargs)

    def resolve(self, **kwargs: Any) -> dict[str, Any]:
        return self.repository.resolve(**kwargs)


class UniverseIngestionRepository:
    def __init__(self, db_path: str = "data/cache.db", *, repository: UniverseRepository | None = None,
                 guard: UniverseWriteGuard | None = None):
        self.repository = repository or UniverseRepository(db_path, guard=guard)

    def add_revision(self, **kwargs: Any) -> dict[str, Any]:
        return self.repository.add_revision(**kwargs)


__all__ = [
    "UniverseIdentityCollision", "UniverseIdentityRepository", "UniverseIdempotencyConflict",
    "UniverseIngestionRepository", "UniverseRepository", "UniverseStorageUnavailable",
]
