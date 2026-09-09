"""Sanity-check a ticker before committing a full analysis.

    python -m cli_agent.check_ticker RELIANCE.NS TCS.NS SUNLITE-SM.NS

Flags the failure modes that waste an analysis run: missing exchange suffix,
no data, too little history (recent IPOs), SME-platform tickers, thin volume.
It cannot detect ETF NAV-premium dislocations -- check market price vs iNAV
manually for ETFs.
"""

from __future__ import annotations

import sys

import yfinance as yf

MIN_ROWS = 60
THIN_VOLUME = 50_000


def check(symbol: str) -> None:
    label = f"{symbol:16}"
    try:
        h = yf.Ticker(symbol).history(period="1y")
    except Exception as exc:  # noqa: BLE001
        print(f"{label} ERROR   {exc}")
        return

    if h.empty:
        if "." not in symbol:
            hint = f"  -> add an exchange suffix, e.g. {symbol}.NS (NSE) / .BO (BSE)"
        elif symbol.upper().endswith(("-SM.NS", "-SM.BO")):
            hint = "  -> SME/Emerge platform stock; no usable history on Yahoo"
        else:
            hint = ""
        print(f"{label} NO DATA{hint}")
        return

    rows = len(h)
    last = h.index[-1].date()
    close = round(float(h["Close"].iloc[-1]), 2)
    med_vol = int(h["Volume"].median())

    problems = []
    if rows < MIN_ROWS:
        problems.append(f"only {rows} bars in 1y (need >= {MIN_ROWS})")
    if med_vol < THIN_VOLUME:
        problems.append(f"thin: median volume {med_vol:,}/day")

    verdict = "OK   " if not problems else "WEAK "
    note = "" if not problems else "  -- " + "; ".join(problems)
    print(f"{label} {verdict}  {rows} bars | last {last} | close {close} | med vol {med_vol:,}{note}")


def main() -> None:
    symbols = sys.argv[1:]
    if not symbols:
        print(__doc__)
        sys.exit(1)
    for s in symbols:
        check(s)


if __name__ == "__main__":
    main()
