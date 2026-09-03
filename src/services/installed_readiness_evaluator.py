"""Authoritative positive-proof readiness evaluator for Installed Data Operations."""

from __future__ import annotations

import sqlite3
from typing import Any

from src.domain.installed_data_operations import InstalledReadiness


def evaluate_installed_readiness(
    conn: sqlite3.Connection,
    as_of_date: str | None = None,
) -> tuple[InstalledReadiness, dict[str, Any]]:
    """Evaluates positive domain proof across the five required coherent pillars:

    1. Calendar session proof (positive trading sessions exist)
    2. Identity epoch proof (universe instruments with identity_epoch >= 1)
    3. Listing AND Trading state proof (both listed lifecycle AND normal operational events exist)
    4. Coherent EOD & Universe proof (eligible public EOD close observations exist that bind to active identity)
    5. Venue turnover proof (market_turnover_daily contains both TWSE and TPEx turnover)

    CBC M1B is supplementary/nonblocking.
    Returns:
        tuple[InstalledReadiness, dict[str, Any]]: The evaluated readiness state and detailed metrics.
    """
    calendar_count = 0
    latest_calendar_trading_date: str | None = None
    try:
        if as_of_date:
            row_cal = conn.execute(
                """
                SELECT COUNT(*), MAX(trade_date) FROM trading_calendar_revisions
                WHERE session_status IN ('trading', 'special') AND status = 'available' AND trade_date <= ?
                """,
                (as_of_date,),
            ).fetchone()
        else:
            row_cal = conn.execute(
                """
                SELECT COUNT(*), MAX(trade_date) FROM trading_calendar_revisions
                WHERE session_status IN ('trading', 'special') AND status = 'available'
                """
            ).fetchone()
        if row_cal and row_cal[0]:
            calendar_count = int(row_cal[0])
            latest_calendar_trading_date = str(row_cal[1]) if row_cal[1] else None
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

    listing_count = 0
    try:
        row_life = conn.execute(
            "SELECT COUNT(*) FROM universe_lifecycle_events WHERE event_type = 'listed' AND status = 'accepted'"
        ).fetchone()
        if row_life and row_life[0]:
            listing_count = int(row_life[0])
    except sqlite3.Error:
        pass

    operational_count = 0
    try:
        row_op = conn.execute(
            "SELECT COUNT(*) FROM universe_operational_state_events WHERE trading_state = 'normal' AND status = 'accepted'"
        ).fetchone()
        if row_op and row_op[0]:
            operational_count = int(row_op[0])
    except sqlite3.Error:
        pass

    eod_count = 0
    latest_eod_date: str | None = None
    coherent_eod_universe_count = 0
    try:
        if as_of_date:
            row_obs = conn.execute(
                """
                SELECT COUNT(*), MAX(trade_date) FROM eod_close_observations
                WHERE observation_status = 'available' AND public_eligibility_status = 'eligible'
                  AND close_value IS NOT NULL AND trade_date <= ?
                """,
                (as_of_date,),
            ).fetchone()
        else:
            row_obs = conn.execute(
                """
                SELECT COUNT(*), MAX(trade_date) FROM eod_close_observations
                WHERE observation_status = 'available' AND public_eligibility_status = 'eligible'
                  AND close_value IS NOT NULL
                """
            ).fetchone()
        if row_obs and row_obs[0] is not None:
            eod_count = int(row_obs[0])
            latest_eod_date = str(row_obs[1]) if row_obs[1] else None

        # Coherent binding: verify that eligible observations on latest_eod_date bind to active instruments
        if latest_eod_date:
            row_coherent = conn.execute(
                """
                SELECT COUNT(DISTINCT o.official_code)
                FROM eod_close_observations o
                JOIN universe_instruments u ON u.instrument_id = o.instrument_id
                WHERE o.trade_date = ?
                  AND o.observation_status = 'available'
                  AND o.public_eligibility_status = 'eligible'
                  AND o.close_value IS NOT NULL
                  AND u.identity_epoch >= 1
                """,
                (latest_eod_date,),
            ).fetchone()
            if row_coherent and row_coherent[0]:
                coherent_eod_universe_count = int(row_coherent[0])
    except sqlite3.Error:
        pass

    turnover_count = 0
    latest_turnover_date: str | None = None
    try:
        if as_of_date:
            row_turn = conn.execute(
                """
                SELECT COUNT(*), MAX(trade_date) FROM market_turnover_daily
                WHERE twse_turnover_twd IS NOT NULL AND tpex_turnover_twd IS NOT NULL
                  AND trade_date <= ?
                """,
                (as_of_date,),
            ).fetchone()
        else:
            row_turn = conn.execute(
                """
                SELECT COUNT(*), MAX(trade_date) FROM market_turnover_daily
                WHERE twse_turnover_twd IS NOT NULL AND tpex_turnover_twd IS NOT NULL
                """
            ).fetchone()
        if row_turn and row_turn[0]:
            turnover_count = int(row_turn[0])
            latest_turnover_date = str(row_turn[1]) if row_turn[1] else None
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
        "listing_events": listing_count,
        "operational_events": operational_count,
        "listing_trading_events": listing_count + operational_count,
        "eod_observations": eod_count,
        "latest_eod_date": latest_eod_date,
        "coherent_eod_universe_count": coherent_eod_universe_count,
        "turnover_records": turnover_count,
        "latest_turnover_date": latest_turnover_date,
        "cbc_m1b_period": cbc_period,
    }

    all_zero = (
        calendar_count == 0
        and identity_count == 0
        and listing_count == 0
        and operational_count == 0
        and eod_count == 0
        and turnover_count == 0
    )
    if all_zero:
        return InstalledReadiness.NOT_INITIALIZED, details

    all_pillars_proven = (
        calendar_count > 0
        and identity_count > 0
        and listing_count > 0
        and operational_count > 0
        and eod_count > 0
        and coherent_eod_universe_count > 0
        and turnover_count > 0
    )

    if not all_pillars_proven:
        return InstalledReadiness.PARTIAL, details

    # Coherent current-vs-stale evaluation:
    # If the latest closed calendar trading date is newer than our latest EOD date, state is STALE.
    if (
        latest_calendar_trading_date is not None
        and latest_eod_date is not None
        and latest_eod_date < latest_calendar_trading_date
    ):
        return InstalledReadiness.STALE, details

    return InstalledReadiness.READY, details
