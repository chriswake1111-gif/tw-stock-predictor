"""Authoritative positive-proof readiness evaluator for Installed Data Operations."""

from __future__ import annotations

import sqlite3
from typing import Any

from src.domain.installed_data_operations import InstalledReadiness


def evaluate_installed_readiness(
    conn: sqlite3.Connection,
    as_of_date: str | None = None,
) -> tuple[InstalledReadiness, dict[str, Any]]:
    """Evaluates positive domain proof across the five required pillars:

    1. Calendar session proof (accepted trading calendar entries exist)
    2. Identity epoch proof (universe instruments with identity_epoch >= 1)
    3. Listing/Trading state proof (lifecycle or operational events recorded)
    4. Eligible EOD proof (valid EOD close observations/snapshots exist)
    5. Venue turnover proof (market_turnover_daily contains both TWSE and TPEx turnover)

    CBC M1B is supplementary/nonblocking.
    Returns:
        tuple[InstalledReadiness, dict[str, Any]]: The evaluated readiness state and detailed metrics.
    """
    calendar_count = 0
    latest_calendar_trading_date: str | None = None
    try:
        row = conn.execute("SELECT COUNT(*) FROM trading_calendar_revisions").fetchone()
        calendar_count = int(row[0]) if row else 0

        # Query latest trading date in calendar
        if as_of_date:
            row_cal = conn.execute(
                """
                SELECT MAX(trade_date) FROM trading_calendar_revisions
                WHERE session_status = 'trading' AND trade_date <= ?
                """,
                (as_of_date,),
            ).fetchone()
        else:
            row_cal = conn.execute(
                """
                SELECT MAX(trade_date) FROM trading_calendar_revisions
                WHERE session_status = 'trading'
                """
            ).fetchone()
        if row_cal and row_cal[0]:
            latest_calendar_trading_date = str(row_cal[0])
    except sqlite3.Error:
        pass

    identity_count = 0
    try:
        row = conn.execute(
            "SELECT COUNT(*) FROM universe_instruments WHERE identity_epoch >= 1"
        ).fetchone()
        identity_count = int(row[0]) if row else 0
    except sqlite3.Error:
        pass

    listing_trading_count = 0
    try:
        row_life = conn.execute("SELECT COUNT(*) FROM universe_lifecycle_events").fetchone()
        if row_life and row_life[0]:
            listing_trading_count += int(row_life[0])
    except sqlite3.Error:
        pass

    try:
        row_op = conn.execute("SELECT COUNT(*) FROM universe_operational_state_events").fetchone()
        if row_op and row_op[0]:
            listing_trading_count += int(row_op[0])
    except sqlite3.Error:
        pass

    eod_count = 0
    latest_eod_date: str | None = None
    try:
        # Check source snapshots or observations
        row_snap = conn.execute(
            "SELECT COUNT(*), MAX(source_trade_date) FROM eod_close_source_snapshots WHERE source_trade_date_status = 'valid'"
        ).fetchone()
        if row_snap and row_snap[0] is not None and int(row_snap[0]) > 0:
            eod_count = int(row_snap[0])
            latest_eod_date = str(row_snap[1]) if row_snap[1] else None
        else:
            row_obs = conn.execute(
                "SELECT COUNT(*), MAX(trade_date) FROM eod_close_observations WHERE status = 'available'"
            ).fetchone()
            if row_obs and row_obs[0] is not None:
                eod_count = int(row_obs[0])
                latest_eod_date = str(row_obs[1]) if row_obs[1] else None
    except sqlite3.Error:
        pass

    turnover_count = 0
    try:
        row = conn.execute(
            """
            SELECT COUNT(*) FROM market_turnover_daily
            WHERE twse_turnover_twd IS NOT NULL AND tpex_turnover_twd IS NOT NULL
            """
        ).fetchone()
        turnover_count = int(row[0]) if row else 0
    except sqlite3.Error:
        pass

    cbc_period: str | None = None
    try:
        row = conn.execute(
            "SELECT MAX(period) FROM cbc_m1b_monthly WHERE status = 'available'"
        ).fetchone()
        if row and row[0]:
            cbc_period = str(row[0])
    except sqlite3.Error:
        pass

    details: dict[str, Any] = {
        "calendar_sessions": calendar_count,
        "latest_calendar_trading_date": latest_calendar_trading_date,
        "identity_instruments": identity_count,
        "listing_trading_events": listing_trading_count,
        "eod_observations": eod_count,
        "latest_eod_date": latest_eod_date,
        "turnover_records": turnover_count,
        "cbc_m1b_period": cbc_period,
    }

    # Evaluation
    all_zero = (
        calendar_count == 0
        and identity_count == 0
        and listing_trading_count == 0
        and eod_count == 0
        and turnover_count == 0
    )
    if all_zero:
        return InstalledReadiness.NOT_INITIALIZED, details

    all_pillars_proven = (
        calendar_count > 0
        and identity_count > 0
        and listing_trading_count > 0
        and eod_count > 0
        and turnover_count > 0
    )

    if not all_pillars_proven:
        return InstalledReadiness.PARTIAL, details

    # All pillars proven -> check staleness
    if (
        latest_calendar_trading_date is not None
        and latest_eod_date is not None
        and latest_eod_date < latest_calendar_trading_date
    ):
        return InstalledReadiness.STALE, details

    return InstalledReadiness.READY, details
