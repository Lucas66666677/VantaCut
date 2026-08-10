"""Server-side market-data adapters, Redis cache, and deterministic technical indicators."""
from __future__ import annotations

import json
import re
from datetime import date
from typing import Any

import httpx

from app.core.config import settings


class FinanceDataError(RuntimeError):
    pass


def _number(value: Any) -> float:
    return float(str(value).replace(",", "").replace("--", "0"))


def _months(start: date, end: date) -> list[date]:
    year, month, output = start.year, start.month, []
    while (year, month) <= (end.year, end.month):
        output.append(date(year, month, 1)); month += 1
        if month == 13: year, month = year + 1, 1
    return output


def _parse_twse_date(value: str) -> date:
    year, month, day = (int(item) for item in value.split("/")); return date(year + 1911, month, day)


def fetch_twse_history(symbol: str, start: date, end: date) -> list[dict[str, Any]]:
    if not re.fullmatch(r"\d{4,6}", symbol):
        raise FinanceDataError("TWSE symbols must be a 4–6 digit listed-stock or ETF code")
    candles: dict[date, dict[str, Any]] = {}
    headers = {"User-Agent": "AI-Video-Editor/1.0 market-data-worker"}
    with httpx.Client(timeout=settings.finance_twse_timeout_seconds, headers=headers, follow_redirects=False) as client:
        for month in _months(start, end):
            response = client.get("https://www.twse.com.tw/exchangeReport/STOCK_DAY", params={"response": "json", "date": month.strftime("%Y%m%d"), "stockNo": symbol})
            response.raise_for_status(); payload = response.json()
            if payload.get("stat") != "OK": continue
            for row in payload.get("data", []):
                if len(row) < 9: continue
                trading_day = _parse_twse_date(str(row[0]))
                if start <= trading_day <= end:
                    candles[trading_day] = {"timestamp": trading_day.isoformat(), "open": _number(row[3]), "high": _number(row[4]), "low": _number(row[5]), "close": _number(row[6]), "volume": _number(row[1])}
    if not candles: raise FinanceDataError("TWSE returned no daily data for this symbol/date range")
    return [candles[key] for key in sorted(candles)]


def fetch_yahoo_compatible_history(symbol: str, start: date, end: date) -> list[dict[str, Any]]:
    """Call only a configured, licensed compatible provider—never an undocumented Yahoo endpoint."""
    if not settings.finance_yahoo_compatible_base_url or not settings.finance_yahoo_compatible_api_key:
        raise FinanceDataError("A licensed YAHOO_COMPATIBLE provider URL and API key are required")
    base = settings.finance_yahoo_compatible_base_url.rstrip("/")
    if not base.startswith("https://"): raise FinanceDataError("Finance provider must use HTTPS")
    response = httpx.get(f"{base}/history", params={"symbol": symbol, "start": start.isoformat(), "end": end.isoformat(), "interval": "1d"}, headers={"Authorization": f"Bearer {settings.finance_yahoo_compatible_api_key}"}, timeout=20, follow_redirects=False)
    response.raise_for_status(); payload = response.json(); candles = payload.get("candles", [])
    if not isinstance(candles, list) or not candles: raise FinanceDataError("Compatible provider returned no candles")
    return [{"timestamp": str(item["timestamp"]), "open": float(item["open"]), "high": float(item["high"]), "low": float(item["low"]), "close": float(item["close"]), "volume": float(item.get("volume", 0))} for item in candles]


def fetch_history_cached(market: str, symbol: str, start: date, end: date) -> list[dict[str, Any]]:
    """Cache public TWSE history; cache licensed data only when the contract permits it."""
    should_cache = market == "twse" or settings.finance_yahoo_cache_allowed
    cache_key = f"finance:history:{market}:{symbol}:{start.isoformat()}:{end.isoformat()}"
    client = None
    if should_cache:
        try:
            import redis

            client = redis.Redis.from_url(settings.redis_url, decode_responses=True, socket_connect_timeout=1)
            cached = client.get(cache_key)
            if cached:
                parsed = json.loads(cached)
                if isinstance(parsed, list):
                    return parsed
        except Exception:
            client = None

    candles = fetch_twse_history(symbol, start, end) if market == "twse" else fetch_yahoo_compatible_history(symbol, start, end)
    if client is not None:
        try:
            client.setex(cache_key, settings.finance_cache_ttl_seconds, json.dumps(candles, separators=(",", ":")))
        except Exception:
            pass
    return candles


def _ema(values: list[float], length: int) -> list[float | None]:
    output: list[float | None] = [None] * len(values); multiplier = 2 / (length + 1); current: float | None = None
    for index, value in enumerate(values):
        current = value if current is None else (value - current) * multiplier + current
        if index >= length - 1: output[index] = current
    return output


def enrich_indicators(candles: list[dict[str, Any]]) -> list[dict[str, Any]]:
    closes = [float(item["close"]) for item in candles]; sma = lambda n: [sum(closes[max(0, index - n + 1):index + 1]) / n if index >= n - 1 else None for index in range(len(closes))]
    sma20, sma60, fast, slow = sma(20), sma(60), _ema(closes, 12), _ema(closes, 26)
    macd = [fast[index] - slow[index] if fast[index] is not None and slow[index] is not None else None for index in range(len(closes))]
    signal = _ema([item or 0 for item in macd], 9)
    gains, losses = [0.0], [0.0]
    for index in range(1, len(closes)):
        delta = closes[index] - closes[index - 1]; gains.append(max(delta, 0)); losses.append(max(-delta, 0))
    rsi: list[float | None] = [None] * len(closes)
    for index in range(14, len(closes)):
        average_gain, average_loss = sum(gains[index - 13:index + 1]) / 14, sum(losses[index - 13:index + 1]) / 14
        rsi[index] = 100.0 if average_loss == 0 else 100 - 100 / (1 + average_gain / average_loss)
    return [{**candle, "indicators": {"sma20": sma20[index], "sma60": sma60[index], "rsi14": rsi[index], "macd": macd[index], "macd_signal": signal[index], "macd_histogram": macd[index] - signal[index] if macd[index] is not None and signal[index] is not None else None}} for index, candle in enumerate(candles)]
