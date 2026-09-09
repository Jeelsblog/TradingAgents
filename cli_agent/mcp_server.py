"""tradingagents_mcp -- point-in-time market/fundamental/news data as MCP tools.

Exposes the TradingAgents dataflow layer (with its look-ahead guards intact)
so any MCP client -- Claude Code, Codex, Cursor -- can run the analyst
methodology on a flat subscription instead of per-token API spend. Every tool
is read-only and makes NO LLM call.

Run (stdio):
    python -m cli_agent.mcp_server

Register in Claude Code via .mcp.json (see cli_agent/README.md).
"""

from __future__ import annotations

import datetime as dt
import os

from mcp.server.mcpserver import MCPServer

# Loads .env (FRED_API_KEY etc.) and applies TRADINGAGENTS_* overrides.
import tradingagents  # noqa: F401
from cli_agent._data import daterange, guarded
from tradingagents.dataflows.interface import route_to_vendor
from tradingagents.dataflows.market_data_validator import build_verified_market_snapshot
from tradingagents.dataflows.symbol_utils import normalize_symbol

try:
    from tradingagents.agents.utils.agent_utils import (
        build_instrument_context,
        resolve_instrument_identity,
    )
except Exception:  # pragma: no cover
    build_instrument_context = None
    resolve_instrument_identity = None

server = MCPServer("tradingagents_mcp")

_STATEMENTS = {
    "income": "get_income_statement",
    "balance": "get_balance_sheet",
    "cashflow": "get_cashflow",
}
_READ_ONLY = {"readOnlyHint": True, "destructiveHint": False, "idempotentHint": True, "openWorldHint": True}


def _valid_date(date: str) -> None:
    dt.date.fromisoformat(date)  # raises ValueError with a clear message


@server.tool(name="trading_check_ticker", annotations={"title": "Validate a ticker", **_READ_ONLY})
def trading_check_ticker(symbol: str) -> str:
    """Pre-flight a ticker before a full analysis.

    Flags: missing exchange suffix, dead/unknown symbol, too little history
    (recent IPO), NSE/BSE SME-platform tickers, thin volume. Cannot detect an
    ETF trading at a NAV premium/discount -- check that manually for ETFs.

    Args:
        symbol: Yahoo symbol, e.g. "RELIANCE.NS", "^NSEI", "TCS.NS".

    Returns:
        One line: "OK" / "WEAK" / "NO DATA" with bar count, last date, last
        close, median daily volume, and any problems.
    """
    import yfinance as yf

    try:
        h = yf.Ticker(symbol).history(period="1y")
    except Exception as exc:  # noqa: BLE001
        return f"{symbol}: ERROR {exc}"
    if h.empty:
        if "." not in symbol:
            return f"{symbol}: NO DATA -- add an exchange suffix, e.g. {symbol}.NS / {symbol}.BO"
        if symbol.upper().endswith(("-SM.NS", "-SM.BO")):
            return f"{symbol}: NO DATA -- SME/Emerge platform stock; no usable history on Yahoo"
        return f"{symbol}: NO DATA -- unknown or delisted symbol"

    rows = len(h)
    last = h.index[-1].date()
    close = round(float(h["Close"].iloc[-1]), 2)
    med_vol = int(h["Volume"].median())
    problems = []
    if rows < 60:
        problems.append(f"only {rows} bars in 1y (need >= 60)")
    if med_vol < 50_000:
        problems.append(f"thin: median volume {med_vol:,}/day")
    verdict = "OK" if not problems else "WEAK"
    tail = "" if not problems else "  -- " + "; ".join(problems)
    return f"{symbol}: {verdict}  {rows} bars | last {last} | close {close} | med vol {med_vol:,}{tail}"


@server.tool(name="trading_resolve_symbol", annotations={"title": "Resolve to canonical symbol", **_READ_ONLY})
def trading_resolve_symbol(symbol: str) -> str:
    """Resolve a user/broker symbol to its canonical Yahoo symbol and identity.

    Args:
        symbol: e.g. "XAUUSD", "reliance.ns", "0700.HK".

    Returns:
        Markdown with the canonical symbol and, when resolvable, the company
        name / sector / industry / exchange. Use this identity in every
        downstream tool call and in the report.
    """
    canonical = normalize_symbol(symbol)
    lines = [f"- Requested: `{symbol}`", f"- Canonical Yahoo symbol: `{canonical}`"]
    if resolve_instrument_identity and build_instrument_context:
        try:
            identity = resolve_instrument_identity(symbol) or {}
        except Exception:  # noqa: BLE001
            identity = {}
        if identity:
            lines.append("- " + build_instrument_context(canonical, "stock", identity))
    return "\n".join(lines)


@server.tool(name="trading_verified_snapshot", annotations={"title": "Verified market snapshot", **_READ_ONLY})
def trading_verified_snapshot(symbol: str, date: str, look_back_days: int = 30) -> str:
    """Deterministic ground-truth snapshot -- the SOURCE OF TRUTH for exact
    price / Bollinger / RSI / MACD / moving-average values.

    Returns the latest OHLCV row on or before `date`, the common indicator set,
    and recent closes. Rows after `date` are excluded (point-in-time). If any
    other tool disagrees with this, flag the discrepancy -- do not invent a
    reconciled number.

    Args:
        symbol: Yahoo symbol.
        date: analysis date, YYYY-MM-DD, treated as "now".
        look_back_days: recent trading rows to include (default 30).
    """
    _valid_date(date)
    return guarded("verified snapshot", lambda: build_verified_market_snapshot(normalize_symbol(symbol), date, look_back_days))


@server.tool(name="trading_price_history", annotations={"title": "OHLCV price history", **_READ_ONLY})
def trading_price_history(symbol: str, start_date: str, end_date: str) -> str:
    """Daily OHLCV over a date range (inclusive), point-in-time filtered.

    Args:
        symbol: Yahoo symbol.
        start_date, end_date: YYYY-MM-DD. Keep end_date <= your analysis date.
    """
    _valid_date(start_date)
    _valid_date(end_date)
    return guarded("price history", lambda: route_to_vendor("get_stock_data", normalize_symbol(symbol), start_date, end_date))


@server.tool(name="trading_indicator", annotations={"title": "Technical indicator series", **_READ_ONLY})
def trading_indicator(symbol: str, indicator: str, date: str, look_back_days: int = 60) -> str:
    """One technical indicator's recent values, ending at `date`.

    Args:
        symbol: Yahoo symbol.
        indicator: one of close_10_ema, close_50_sma, close_200_sma, rsi, boll,
            boll_ub, boll_lb, macd, macds, macdh, atr, vwma (or another
            stockstats name). Call once per indicator.
        date: analysis date, YYYY-MM-DD.
        look_back_days: window length (default 60).
    """
    _valid_date(date)
    return guarded(indicator, lambda: route_to_vendor("get_indicators", normalize_symbol(symbol), indicator, date, look_back_days))


@server.tool(name="trading_fundamentals", annotations={"title": "Company fundamentals overview", **_READ_ONLY})
def trading_fundamentals(symbol: str, date: str) -> str:
    """Company profile + basic financials as of `date`.

    For an index or ETF this returns NO_DATA / withheld -- expected, not a
    failure; defer to technical + macro. Present-day profile values are
    withheld on purpose to avoid look-ahead.

    Args:
        symbol: Yahoo symbol.
        date: analysis date, YYYY-MM-DD.
    """
    _valid_date(date)
    return guarded("fundamentals", lambda: route_to_vendor("get_fundamentals", normalize_symbol(symbol), date))


@server.tool(name="trading_financial_statement", annotations={"title": "Financial statement", **_READ_ONLY})
def trading_financial_statement(symbol: str, statement: str, freq: str = "annual", date: str | None = None) -> str:
    """A point-in-time financial statement.

    Args:
        symbol: Yahoo symbol.
        statement: "income", "balance", or "cashflow".
        freq: "annual" or "quarterly" (default annual).
        date: analysis date, YYYY-MM-DD -- statements published after it are dropped.
    """
    if statement not in _STATEMENTS:
        return f"Error: statement must be one of {list(_STATEMENTS)}, got {statement!r}"
    if freq not in ("annual", "quarterly"):
        return f"Error: freq must be 'annual' or 'quarterly', got {freq!r}"
    if date:
        _valid_date(date)
    method = _STATEMENTS[statement]
    return guarded(f"{statement} {freq}", lambda: route_to_vendor(method, normalize_symbol(symbol), freq, date))


@server.tool(name="trading_insider_transactions", annotations={"title": "Insider transactions", **_READ_ONLY})
def trading_insider_transactions(symbol: str) -> str:
    """Recent insider buy/sell filings for a company (empty is normal for many
    valid symbols).

    Args:
        symbol: Yahoo symbol.
    """
    return guarded("insider transactions", lambda: route_to_vendor("get_insider_transactions", normalize_symbol(symbol)))


@server.tool(name="trading_ticker_news", annotations={"title": "Ticker news", **_READ_ONLY})
def trading_ticker_news(symbol: str, start_date: str, end_date: str) -> str:
    """News for one ticker over a date range.

    Coverage is thin for Indian small/mid-caps and SME names -- treat an empty
    result as signal (low coverage), not an error, and supplement with NSE/BSE
    filings and Indian financial media.

    Args:
        symbol: Yahoo symbol.
        start_date, end_date: YYYY-MM-DD (end_date <= analysis date).
    """
    _valid_date(start_date)
    _valid_date(end_date)
    return guarded("ticker news", lambda: route_to_vendor("get_news", normalize_symbol(symbol), start_date, end_date))


@server.tool(name="trading_global_news", annotations={"title": "Global / macro headlines", **_READ_ONLY})
def trading_global_news(date: str) -> str:
    """Broad macro / market headlines as of `date` (config-driven lookback).

    Args:
        date: analysis date, YYYY-MM-DD.
    """
    _valid_date(date)
    return guarded("global news", lambda: route_to_vendor("get_global_news", date, None, None))


@server.tool(name="trading_macro", annotations={"title": "FRED macro indicator", **_READ_ONLY})
def trading_macro(indicator: str, date: str) -> str:
    """A macro series from FRED (needs FRED_API_KEY in the environment).

    Args:
        indicator: alias such as "cpi", "core_pce", "unemployment",
            "fed_funds_rate", "10y_treasury", "yield_curve", "real_gdp", "vix",
            or a raw FRED series id (e.g. "CPIAUCSL").
        date: analysis date, YYYY-MM-DD (end of the trailing window).
    """
    _valid_date(date)
    if not os.environ.get("FRED_API_KEY"):
        return (
            "_FRED_API_KEY not set -- macro data unavailable. Add a free key from "
            "https://fred.stlouisfed.org/docs/api/api_key.html to .env._"
        )
    return guarded(indicator, lambda: route_to_vendor("get_macro_indicators", indicator, date, None))


@server.tool(name="trading_prediction_markets", annotations={"title": "Prediction-market odds", **_READ_ONLY})
def trading_prediction_markets(topic: str, limit: int = 5) -> str:
    """Live market-implied probabilities for a forward-looking event
    (Polymarket). Often slow or unreachable -- a timeout note is normal.

    Args:
        topic: e.g. "Fed rate cut", "recession 2026", "India economy".
        limit: max markets (default 5).
    """
    return guarded(topic, lambda: route_to_vendor("get_prediction_markets", topic, limit))


@server.tool(name="trading_daterange", annotations={"title": "Trailing date window", **_READ_ONLY})
def trading_daterange(date: str, days: int) -> str:
    """Helper: the (start, end) ISO dates for a `days`-long window ending at `date`."""
    _valid_date(date)
    start, end = daterange(date, days)
    return f"{start} to {end}"


def main() -> None:
    import argparse

    p = argparse.ArgumentParser(description="tradingagents_mcp server")
    p.add_argument(
        "--http",
        action="store_true",
        help="serve over streamable HTTP instead of stdio (for claude.ai remote "
        "connectors via a tunnel; localhost-only by default)",
    )
    p.add_argument("--host", default="127.0.0.1", help="HTTP bind host (default 127.0.0.1)")
    p.add_argument("--port", type=int, default=8000, help="HTTP port (default 8000)")
    args = p.parse_args()

    if args.http:
        # stateless_http keeps it simple behind a tunnel; path is /mcp.
        server.run("streamable-http", host=args.host, port=args.port, stateless_http=True)
    else:
        server.run("stdio")


if __name__ == "__main__":
    main()

