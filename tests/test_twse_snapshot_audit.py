import pandas as pd

from tools.audit_twse_snapshot import audit_month, parse_twse_stock_day


def test_twse_parser_and_audit_detect_missing_official_day_and_zero_volume_row():
    payload = {
        "stat": "OK",
        "data": [
            ["114/08/01", "50,445,289", "2,076,702,924", "41.80", "42.25", "40.60", "40.70", "-1.95", "27,806", ""],
            ["114/08/04", "64,385,687", "2,435,916,421", "39.55", "39.55", "37.05", "37.85", "-2.85", "28,013", ""],
        ],
    }
    snapshot = pd.DataFrame([
        {"date": "2025-08-04", "volume": 0, "close": 37.5},
    ])

    audit = audit_month("1301.TW", "2025-08", snapshot, parse_twse_stock_day(payload))

    assert audit["status"] == "quality_warning"
    assert audit["missing_official_dates"] == ["2025-08-01"]
    assert audit["zero_volume_provider_dates"] == ["2025-08-04"]
    assert audit["official_missing_rows"][0]["close"] == 40.70
