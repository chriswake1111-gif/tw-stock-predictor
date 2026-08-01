from pathlib import Path


def test_no_broker_sdk_or_live_order_dependencies():
    requirements = Path("requirements.txt").read_text(encoding="utf-8").lower()
    forbidden_dependencies = [
        "shioaji", "fugle", "ib_insync", "alpaca-trade-api", "ccxt"
    ]

    assert all(name not in requirements for name in forbidden_dependencies)


def test_strategy_has_no_network_or_broker_imports():
    strategy_source = Path("src/strategy/backtester.py").read_text(encoding="utf-8").lower()

    assert "requests" not in strategy_source
    assert "websocket" not in strategy_source
    assert "historical_backtest_only" in strategy_source


def test_web_frontend_uses_current_research_contract_without_fake_placeholders():
    app_source = Path("src/ui_alert/web/app.js").read_text(encoding="utf-8")
    html_source = Path("src/ui_alert/web/index.html").read_text(encoding="utf-8")

    assert "data.wave_targets" not in app_source
    assert "data.fib_window" not in app_source
    assert "volume_m1b_ratio" not in app_source
    assert "$925.00" not in html_source
    assert "12629" not in html_source
    assert "不連接券商帳戶" in html_source
