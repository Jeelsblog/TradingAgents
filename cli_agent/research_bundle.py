"""Build a point-in-time research bundle for one ticker, with NO LLM calls.

This assembles everything the TradingAgents analyst agents would normally
fetch -- verified market snapshot, price history, indicators, fundamentals,
news, macro, prediction markets -- into a single markdown file. A CLI coding
agent (Claude Code, Codex, ...) then reasons over that file following the
`trading-analysis` skill, so the multi-agent methodology runs on a flat
subscription instead of per-token API spend.

    python -m cli_agent.research_bundle RELIANCE.NS 2026-09-08
    python -m cli_agent.research_bundle ^NSEI 2026-09-08 --analysts market,news
    python -m cli_agent.research_bundle TCS.NS 2026-09-08 --out /tmp/bundles

Every data pull is wrapped: a vendor error (e.g. FRED key missing, no news)
is written into the bundle verbatim, exactly as the real agents would see it,
rather than aborting the run.
"""

from __future__ import annotations

import argparse
import datetime as dt
import os
import threading
from pathlib import Path

# Loads .env (FRED_API_KEY etc.) and applies TRADINGAGENTS_* overrides.
import tradingagents  # noqa: F401
from tradingagents.dataflows.interface import route_to_vendor
from tradingagents.dataflows.market_data_validator import (
    DEFAULT_SNAPSHOT_INDICATORS,
    build_verified_market_snapshot,
)
from tradingagents.dataflows.symbol_utils import normalize_symbol
from tradingagents.default_config import DEFAULT_CONFIG

try:
    from tradingagents.agents.utils.agent_utils import (
        build_instrument_context,
        resolve_instrument_identity,
    )
except Exception:  # pragma: no cover - keep the bundle usable if utils move
    build_instrument_context = None
    resolve_instrument_identity = None

ANALYSTS = ("market", "news", "fundamentals")
PRICE_LOOKBACK_DAYS = 220          # ~10 months of sessions for trend context
MACRO_SERIES = ("fed_funds_rate", "10y_treasury", "cpi", "unemployment", "yield_curve")
PREDICTION_TOPICS = ("Fed rate cut", "recession 2026", "India economy")
CALL_TIMEOUT_S = 30               # a slow vendor never hangs the whole bundle


def _section(title: str) -> str:
    return f"\n\n{'=' * 78}\n## {title}\n{'=' * 78}\n"


def _try(label: str, fn) -> str:
    """Run a data pull in a watchdog thread; return its text or a visible note.

    Vendor calls (FRED, Polymarket, yfinance) can hang or 30s-retry; the daemon
    thread lets the bundle move on and still exit cleanly.
    """
    box: dict[str, str] = {}

    def _run() -> None:
        try:
            out = fn()
            box["ok"] = str(out).strip() or f"_{label}: empty result_"
        except Exception as exc:  # noqa: BLE001 - the error itself is useful signal
            box["ok"] = f"_{label}: unavailable -- {type(exc).__name__}: {exc}_"

    t = threading.Thread(target=_run, daemon=True)
    t.start()
    t.join(CALL_TIMEOUT_S)
    if t.is_alive():
        return f"_{label}: timed out after {CALL_TIMEOUT_S}s (vendor slow/unreachable)_"
    return box.get("ok", f"_{label}: no result_")


def _daterange(curr_date: str, days: int) -> tuple[str, str]:
    end = dt.date.fromisoformat(curr_date)
    start = end - dt.timedelta(days=days)
    return start.isoformat(), end.isoformat()


def _instrument_context(raw: str, canonical: str) -> str:
    if resolve_instrument_identity is None or build_instrument_context is None:
        return f"Ticker: `{raw}` (canonical `{canonical}`)"
    try:
        identity = resolve_instrument_identity(raw)
    except Exception:  # noqa: BLE001
        identity = {}
    return build_instrument_context(canonical, "stock", identity)


def build_market_section(canonical: str, curr_date: str) -> str:
    out = [_section("MARKET DATA (as of " + curr_date + ")")]
    out.append(
        "> The verified snapshot below is the SOURCE OF TRUTH for any exact "
        "price / band / indicator value. If the price-history table disagrees, "
        "flag the discrepancy -- do not invent a reconciled number.\n"
    )
    out.append("### Verified market snapshot\n")
    out.append(_try("verified snapshot", lambda: build_verified_market_snapshot(canonical, curr_date, 30)))

    start, end = _daterange(curr_date, PRICE_LOOKBACK_DAYS)
    out.append(f"\n### Price history ({start} to {end})\n")
    out.append(_try("price history", lambda: route_to_vendor("get_stock_data", canonical, start, end)))

    indicators = list(DEFAULT_SNAPSHOT_INDICATORS) + ["vwma"]
    out.append("\n### Indicator detail (60-day window per indicator)\n")
    for ind in indicators:
        out.append(f"\n**{ind}**\n")
        out.append(_try(ind, lambda ind=ind: route_to_vendor("get_indicators", canonical, ind, curr_date, 60)))
    return "\n".join(out)


def build_fundamentals_section(canonical: str, curr_date: str) -> str:
    out = [_section("FUNDAMENTALS (point-in-time, as of " + curr_date + ")")]
    out.append(
        "> For an index or ETF, most of these return NO_DATA / withheld -- that "
        "is expected, not a failure. Note it and defer to technical + macro.\n"
    )
    out.append("### Company overview\n")
    out.append(_try("fundamentals overview", lambda: route_to_vendor("get_fundamentals", canonical, curr_date)))
    for name, method in (
        ("Income statement", "get_income_statement"),
        ("Balance sheet", "get_balance_sheet"),
        ("Cash flow", "get_cashflow"),
    ):
        for freq in ("annual", "quarterly"):
            out.append(f"\n### {name} ({freq})\n")
            out.append(_try(f"{name} {freq}", lambda m=method, f=freq: route_to_vendor(m, canonical, f, curr_date)))
    out.append("\n### Insider transactions\n")
    out.append(_try("insider transactions", lambda: route_to_vendor("get_insider_transactions", canonical)))
    return "\n".join(out)


def build_news_section(canonical: str, curr_date: str, prediction_markets: bool = False) -> str:
    out = [_section("NEWS & MACRO (as of " + curr_date + ")")]
    start, end = _daterange(curr_date, DEFAULT_CONFIG.get("global_news_lookback_days", 7) * 2)
    out.append(f"### Ticker news ({start} to {end})\n")
    out.append(_try("ticker news", lambda: route_to_vendor("get_news", canonical, start, end)))

    out.append("\n### Global / macro headlines\n")
    out.append(_try("global news", lambda: route_to_vendor("get_global_news", curr_date, None, None)))

    out.append("\n### Macro indicators (FRED)\n")
    if not os.environ.get("FRED_API_KEY"):
        out.append(
            "_FRED_API_KEY not set -- macro series skipped. Add a free key from "
            "https://fred.stlouisfed.org/docs/api/api_key.html to .env to enable._"
        )
    else:
        for series in MACRO_SERIES:
            out.append(f"\n**{series}**\n")
            out.append(_try(series, lambda s=series: route_to_vendor("get_macro_indicators", s, curr_date, None)))

    if prediction_markets:
        out.append("\n### Prediction markets\n")
        for topic in PREDICTION_TOPICS:
            out.append(f"\n**{topic}**\n")
            out.append(_try(topic, lambda t=topic: route_to_vendor("get_prediction_markets", t, 5)))
    return "\n".join(out)


BUILDERS = {
    "market": build_market_section,
    "fundamentals": build_fundamentals_section,
    "news": build_news_section,
}


def build_bundle(
    raw_ticker: str,
    curr_date: str,
    analysts: list[str],
    prediction_markets: bool = False,
) -> str:
    canonical = normalize_symbol(raw_ticker)
    dt.date.fromisoformat(curr_date)  # validate

    header = [
        f"# Research bundle: {raw_ticker}",
        "",
        f"- Requested ticker: `{raw_ticker}`",
        f"- Canonical (Yahoo) symbol: `{canonical}`",
        f"- Analysis date (point-in-time 'now'): **{curr_date}**",
        f"- Generated: {dt.datetime.now().isoformat(timespec='seconds')}",
        f"- Sections: {', '.join(analysts)}",
        "",
        "## Instrument context",
        "",
        _instrument_context(raw_ticker, canonical),
        "",
        f"> All data below is filtered to on-or-before the analysis date. "
        f"Treat {curr_date} as today. Do not reference anything after it.",
    ]
    body = []
    for a in analysts:
        if a == "news":
            body.append(build_news_section(canonical, curr_date, prediction_markets))
        elif a in BUILDERS:
            body.append(BUILDERS[a](canonical, curr_date))
    return "\n".join(header) + "\n" + "\n".join(body) + "\n"


def main() -> None:
    p = argparse.ArgumentParser(description="Point-in-time research bundle (no LLM calls).")
    p.add_argument("ticker", help="e.g. RELIANCE.NS, ^NSEI, TCS.NS")
    p.add_argument("date", help="analysis date, YYYY-MM-DD (treated as 'now')")
    p.add_argument(
        "--analysts",
        default=",".join(ANALYSTS),
        help=f"comma list from {ANALYSTS} (default: all)",
    )
    p.add_argument("--out", default=str(Path(__file__).parent / "bundles"), help="output directory")
    p.add_argument(
        "--prediction-markets",
        action="store_true",
        help="also pull Polymarket odds (often slow / times out; off by default)",
    )
    args = p.parse_args()

    analysts = [a.strip().lower() for a in args.analysts.split(",") if a.strip()]
    unknown = [a for a in analysts if a not in BUILDERS]
    if unknown:
        p.error(f"unknown analyst(s): {unknown}; choose from {list(BUILDERS)}")

    bundle = build_bundle(args.ticker, args.date, analysts, args.prediction_markets)

    out_dir = Path(args.out)
    out_dir.mkdir(parents=True, exist_ok=True)
    safe = args.ticker.replace("/", "_").replace("\\", "_")
    out_file = out_dir / f"{safe}_{args.date}.md"
    out_file.write_text(bundle, encoding="utf-8")
    print(f"Wrote {out_file}  ({len(bundle):,} chars)")


if __name__ == "__main__":
    main()
