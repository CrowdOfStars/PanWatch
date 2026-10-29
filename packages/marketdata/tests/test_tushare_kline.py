import sys
from types import SimpleNamespace
from unittest.mock import Mock

import pytest

from marketdata import Bar, MarketData, SourceConfig, StaticConfigProvider
from marketdata.errors import VendorError
from marketdata.http import capture_errors
from marketdata.symbol import Symbol
from marketdata.vendors.tushare import TushareKlineVendor


def row(date="20261020", **overrides):
    return dict(trade_date=date, open="10", close=11, high=12, low=9, vol=100, **overrides)


@pytest.fixture
def sdk(monkeypatch):
    records = [row("20261022"), row("20261020"), row("20261021")]
    frame = SimpleNamespace(empty=False, to_dict=lambda orient: records)
    daily = Mock(return_value=frame)
    pro_api = Mock(return_value=SimpleNamespace(daily=daily))
    monkeypatch.setitem(sys.modules, "tushare", SimpleNamespace(pro_api=pro_api))
    monkeypatch.setattr("marketdata.vendors.tushare.throttle", lambda *args: None)
    monkeypatch.delenv("TUSHARE_TOKEN", raising=False)
    return SimpleNamespace(pro_api=pro_api, daily=daily, records=records)


@pytest.mark.parametrize("code,expected", [("600519", "600519.SH"), ("000001", "000001.SZ"), ("920001", "920001.BJ"), ("830001", "830001.BJ")])
def test_parse_sort_trim_and_exchange(sdk, code, expected):
    bars = TushareKlineVendor().fetch([Symbol.parse(code)], {"token": "test-token", "days": 2})
    assert bars == [Bar(date=d, open=10, close=11, high=12, low=9, volume=100)
                    for d in ("2026-10-21", "2026-10-22")]
    assert sdk.pro_api.call_count == 1
    assert sdk.pro_api.call_args.kwargs == {"token": "test-token", "timeout": 10}
    params = sdk.daily.call_args.kwargs
    assert params["ts_code"] == expected
    assert params["start_date"] <= params["end_date"]


def test_token_fallback_and_config_precedence(sdk, monkeypatch):
    monkeypatch.setenv("TUSHARE_TOKEN", "env-token")
    vendor = TushareKlineVendor()
    for config, expected in [({}, "env-token"), ({"token": " "}, "env-token"), ({"token": "configured"}, "configured")]:
        vendor.fetch([Symbol.parse("600519")], config)
        assert sdk.pro_api.call_args.kwargs["token"] == expected


def test_missing_token_and_optional_dependency(sdk, monkeypatch):
    vendor = TushareKlineVendor()
    with pytest.raises(VendorError, match="TUSHARE_TOKEN"):
        vendor.fetch([Symbol.parse("600519")], {})
    monkeypatch.setitem(sys.modules, "tushare", None)
    with pytest.raises(VendorError, match="pip install tushare"):
        vendor.fetch([Symbol.parse("600519")], {"token": "test-token"})


@pytest.mark.parametrize("symbols", [[], [Symbol.parse("AAPL")], [Symbol.parse("00700")]])
def test_unsupported_market_does_not_initialize_sdk(sdk, symbols):
    assert TushareKlineVendor().fetch(symbols, {}) == []
    sdk.pro_api.assert_not_called()


def test_empty_and_malformed_data(sdk):
    vendor = TushareKlineVendor()
    sdk.records.extend([{}, {**row(), "trade_date": "invalid"}, {**row(), "close": float("nan")}])
    with capture_errors() as errors:
        assert len(vendor.fetch([Symbol.parse("600519")], {"token": "test"})) == 3
    assert len(errors) == 3
    sdk.daily.return_value = SimpleNamespace(empty=True)
    with capture_errors() as errors:
        assert vendor.fetch([Symbol.parse("600519")], {"token": "test"}) == []
    assert "返回空" in errors[0]


def test_engine_routes_and_fails_over_without_exposing_token(sdk):
    md = MarketData(config=StaticConfigProvider({"kline": [
        SourceConfig(vendor="tushare", config={"token": "secret-token"}, priority=0),
        SourceConfig(vendor="tencent", priority=1),
    ]}))
    fallback = Mock(return_value=[Bar(date="2026-10-22", open=1, close=1, high=1, low=1, volume=1)])
    md._kline_engine.vendors["tencent"].fetch = fallback
    assert len(md.klines("600519", market="CN", days=2)) == 2
    fallback.assert_not_called()
    sdk.daily.side_effect = RuntimeError("权限不足 secret-token")
    with capture_errors() as errors:
        assert md.klines("600519", market="CN") == fallback.return_value
    assert "权限不足" in errors[0]
    assert "secret-token" not in errors[0]
    assert "secret-token" not in md.health()["tushare"]["last_error"]
