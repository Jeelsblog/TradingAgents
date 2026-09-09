---
name: trading-analysis
description: >-
  Run the full TradingAgents multi-agent methodology (analysts -> bull/bear
  debate -> research manager -> trader -> risk debate -> portfolio manager) on
  a single ticker for a point-in-time date, using a research bundle instead of
  per-token LLM API calls. Trigger when the user asks to "analyze <ticker>",
  "run trading analysis", "what's the call on <stock>", or names a ticker + a
  date. Produces the same report tree as `python -m cli.main`.
---

# Trading analysis (subscription-priced)

You reproduce the TradingAgents pipeline yourself, reasoning over a pre-built
data bundle. No `tradingagents` LLM calls — you ARE the analyst team.

## Inputs

`/trading-analysis <TICKER> <YYYY-MM-DD> [analysts] [--rounds N]`

- **TICKER** — Yahoo symbol with exchange suffix (`RELIANCE.NS`, `^NSEI`, `TCS.NS`). If the user gives a bare Indian name, add `.NS`.
- **DATE** — the point-in-time "now". All data is filtered to on-or-before this date; never reference anything after it.
- **analysts** — subset of `market,news,fundamentals` (default all three). Skip `news` only if the user asks.
- **--rounds N** — bull/bear and risk debate rounds (default 1).

## Step 0 — Pre-flight

```bash
source .venv/bin/activate
python -m cli_agent.check_ticker <TICKER>
```

- `NO DATA` / `WEAK` (few bars / thin volume / SME) → stop and tell the user; don't burn effort on a run that can't produce a sound call.
- For an **ETF**, also warn that you can't see NAV premium/discount — flag it in the final report.

## Step 1 — Build the bundle

```bash
python -m cli_agent.research_bundle <TICKER> <DATE> --analysts <analysts>
```

Read the written file fully (`cli_agent/bundles/<TICKER>_<DATE>.md`). It has:
`Instrument context`, `MARKET DATA` (verified snapshot + price history + per-indicator tables), `FUNDAMENTALS` (point-in-time statements; profile withheld by design), `NEWS & MACRO`.

**The "Verified market snapshot" block is the source of truth** for any exact price / band / RSI / MACD / MA value. If the price-history table disagrees, flag the discrepancy — never invent a reconciled number. Do not claim a historically-validated bounce or an exact % move unless the bundle's dated rows support it.

## Step 2 — Analyst reports

Write each as its own markdown report, detailed and evidence-based, ending with a Markdown summary table and a line `FINAL TRANSACTION PROPOSAL: **BUY/HOLD/SELL**`.

### Market Analyst
Pick **up to 8 complementary** indicators for the current regime (trend / momentum / volatility / volume — don't double up, e.g. not both rsi and stochrsi). For each, read its value and trajectory from the bundle. Cover: trend structure (10 EMA / 50 SMA / 200 SMA, price vs each), momentum (RSI level + path, MACD line/signal/histogram + whether it's widening or converging), volatility (Bollinger position, ATR regime and stop implications), volume confirmation (VWMA vs price). Give explicit resistance/support levels. Note data caveats (e.g. 50 SMA == 200 SMA ⇒ short history; anomalous single bars).

### Fundamentals Analyst
Work the annual + quarterly income statement, balance sheet, cash flow, and insider transactions. Trace multi-year trends (revenue, margins, EBITDA, net income, FCF, debt, equity, inventory, cash). Call out inflections and inconsistencies. **For an index/ETF**: state that company-style fundamentals don't apply, note it's expected, and defer to technical + macro. Profile/valuation being "withheld" is the point-in-time guard, not a failure.

### News Analyst
Summarize ticker news and global/macro headlines relevant to the instrument. Ground macro claims in the FRED tables if present; if `FRED_API_KEY` was not set, say macro data was unavailable rather than guessing. For an Indian instrument, note when global feeds carry little India-relevant signal and recommend NSE/BSE filings + Indian financial media as supplements. No fabricated figures.

## Step 3 — Research debate (`--rounds N`, default 1)

- **Bull Researcher** — evidence-based case for the position: growth, edge, positive indicators; engage the bear's points directly, conversational, not a data dump.
- **Bear Researcher** — case against: risks, competitive weakness, negative indicators; rebut the bull specifically.
- Repeat for N rounds, each side responding to the last.

## Step 4 — Research Manager

Evaluate the debate on merits (not who spoke last). Output:
- **Recommendation** — exactly one of: **Buy / Overweight / Hold / Underweight / Sell**. Choose Hold when evidence is balanced, conflicting, ambiguous, or thin — don't manufacture a direction.
- **Rationale** — which arguments won and why, cite specifics.
- **Strategic Actions** — numbered, concrete.

## Step 5 — Trader

Turn the plan into a proposal, grounding price levels in the Market Analyst's price structure (current price, support/resistance, ATR):
- **Action** — Buy / Sell / Hold
- **Reasoning**
- **Entry Price** — absolute price level in the quote currency (not a %, not a range), or omit
- **Stop Loss** — absolute price level
- **Position Sizing** — explicit
- `FINAL TRANSACTION PROPOSAL: **BUY/SELL/HOLD**`

## Step 6 — Risk debate (`--rounds N`)

Three analysts argue over the Trader's proposal, each rebutting the other two:
- **Aggressive** — champion upside / high-reward positioning.
- **Conservative** — capital preservation, downside, volatility.
- **Neutral** — balanced; challenge both extremes.

## Step 7 — Portfolio Manager (final)

Synthesize the risk debate. Output:
- **Rating** — exactly one of **Buy / Overweight / Hold / Underweight / Sell** (Hold if genuinely balanced/ambiguous).
- **Executive Summary** — the actionable call in 3-5 sentences (exit/trim/add %, levels, horizon).
- **Investment Thesis** — grounded in specific analyst evidence.
- **Price Target**, **Time Horizon**.
- If ETF: restate the NAV-premium blind spot.

## Step 8 — Write the report tree

Create `reports/<TICKER>_<UTC timestamp YYYYMMDD_HHMMSS>/` and write:

```
1_analysts/market.md          1_analysts/news.md          1_analysts/fundamentals.md
2_research/bull.md   2_research/bear.md   2_research/manager.md
3_trading/trader.md
4_risk/aggressive.md   4_risk/conservative.md   4_risk/neutral.md
5_portfolio/decision.md
complete_report.md            # all of the above concatenated with section headers
```

Match the format of any existing folder under `reports/`. Then give the user:
the final **Rating**, the one-paragraph thesis, key levels, horizon, and any data caveats (FRED missing, thin news, ETF premium, anomalous bars).

## Batch

For several tickers, loop Steps 0-8 per ticker and finish with a summary table
(ticker → rating → target → one-line why). Reuse one bundle per ticker.

## Cost note

The only spend here is your own agent turns. If the user wants the true
independent-agent debate with the LangGraph orchestrator, that's
`python -m cli.main` (Anthropic API, ~$0.50-1.50/run) — offer it only for a
final high-stakes second opinion.
