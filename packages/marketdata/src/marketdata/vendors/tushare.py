"""Tushare Pro A 股未复权日线；可选 SDK，凭证按请求注入，不写全局 Token。"""
from __future__ import annotations

import os
from datetime import datetime, timedelta
from math import isfinite

from marketdata.errors import VendorError
from marketdata.http import record_error, throttle
from marketdata.symbol import Market, Symbol, _cn_exchange
from marketdata.types import Bar
from marketdata.vendors.base import KlineVendor
from marketdata.vendors.kline import _days


class TushareKlineVendor(KlineVendor):
    name = "tushare"
    supports_markets = {"CN"}

    def fetch(self, symbols: list[Symbol], config: dict) -> list[Bar]:
        if not symbols or symbols[0].market != Market.CN:
            return []
        if len(symbols) != 1:
            raise VendorError("Tushare K线仅支持单个股票代码")
        token = str(config.get("token") or "").strip() or os.environ.get("TUSHARE_TOKEN", "").strip()
        if not token:
            raise VendorError("Tushare token 未配置：请填写数据源 Token 或设置 TUSHARE_TOKEN")
        try:
            import tushare as ts
        except ImportError as exc:
            raise VendorError("tushare 未安装，执行 `pip install tushare` 后启用") from exc

        sym = symbols[0]
        days = max(1, _days(config))
        end = datetime.now()
        try:
            throttle("api.tushare.pro", 0.2)
            pro = ts.pro_api(token=token, timeout=10)
            frame = pro.daily(
                ts_code=f"{sym.code}.{_cn_exchange(sym.code).upper()}",
                start_date=(end - timedelta(days=days * 2)).strftime("%Y%m%d"),
                end_date=end.strftime("%Y%m%d"),
            )
        except Exception as exc:
            # Engine 会把异常写入日志及测试结果，不让上游异常泄露 Token。
            message = str(exc).replace(token, "[REDACTED]")
            raise VendorError(f"Tushare daily 调用失败: {message}") from None
        if frame is None or frame.empty:
            record_error("Tushare daily 返回空数据")
            return []

        bars: list[Bar] = []
        for row in frame.to_dict("records"):
            try:
                date = datetime.strptime(str(row["trade_date"]), "%Y%m%d").strftime("%Y-%m-%d")
                values = [float(row[key]) for key in ("open", "close", "high", "low", "vol")]
                if not all(isfinite(value) for value in values):
                    raise ValueError("non-finite OHLCV")
                bars.append(Bar(date=date, open=values[0], close=values[1],
                                high=values[2], low=values[3], volume=values[4]))
            except (KeyError, TypeError, ValueError):
                record_error("Tushare daily 行数据无效，已跳过")
        return sorted(bars, key=lambda bar: bar.date)[-days:]
