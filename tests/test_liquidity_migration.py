import sqlite3

from src.repositories.migration_runner import apply_valuation_migration


def test_liquidity_migration_is_additive_and_rerunnable(tmp_path):
    db_path = tmp_path / "missing" / "fresh.db"
    first = apply_valuation_migration(str(db_path))
    second = apply_valuation_migration(str(db_path))
    assert first["applied"] is True
    assert second["applied"] is False
    with sqlite3.connect(db_path) as conn:
        tables = {
            row[0] for row in conn.execute(
                "SELECT name FROM sqlite_master WHERE type='table'"
            )
        }
    assert {"cbc_m1b_monthly", "market_turnover_daily"}.issubset(tables)
    assert {"forward_eps_observations", "pe_scenarios", "valuation_approvals"}.issubset(tables)
