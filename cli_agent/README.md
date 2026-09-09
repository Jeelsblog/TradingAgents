# cli_agent — run TradingAgents on a coding-agent subscription

The upstream framework drives ~15 LLM agents through the Anthropic (or other)
API, billed per token — roughly $0.50–1.50 per ticker. This directory lets a
CLI coding agent (Claude Code, Codex, …) run the **same methodology** on its
flat subscription instead.

## How it works

Two ways to get the data to your agent — both reuse the framework's own
dataflow layer (look-ahead guards intact) and make **zero LLM calls**:

- **`research_bundle.py`** — one-shot: writes a full point-in-time bundle
  (verified market snapshot, price history, per-indicator tables, annual +
  quarterly statements, ticker + macro news) to a markdown file the agent
  reads. Simple, agent-agnostic.
- **`mcp_server.py`** — an MCP server exposing the same data as ~13 tools
  (`trading_verified_snapshot`, `trading_indicator`, `trading_fundamentals`,
  `trading_ticker_news`, `trading_macro`, …) the agent calls on demand. Works
  in Claude Code, Codex, Cursor — any MCP client.

Then the **`trading-analysis` skill** (`.claude/skills/trading-analysis/`)
walks the agent through every role — Market / Fundamentals / News analyst →
bull vs bear debate → Research Manager → Trader → Aggressive / Conservative /
Neutral risk debate → Portfolio Manager — writing the same
`reports/<ticker>_<timestamp>/` tree the real CLI produces.

**`check_ticker.py`** is a pre-flight: catches missing exchange suffixes,
dead symbols, recent IPOs with too little history, SME-platform tickers, and
thin volume before you spend a run on them.

## Usage

```bash
source .venv/bin/activate

# pre-flight
python -m cli_agent.check_ticker RELIANCE.NS TCS.NS ^NSEI

# build a bundle (writes cli_agent/bundles/<ticker>_<date>.md)
python -m cli_agent.research_bundle RELIANCE.NS 2026-09-08
python -m cli_agent.research_bundle ^NSEI 2026-09-08 --analysts market,news

# then, in Claude Code:
/trading-analysis RELIANCE.NS 2026-09-08
```

The skill runs `check_ticker` and `research_bundle` itself, so
`/trading-analysis <ticker> <date>` is usually all you need.

## MCP server (optional, for incremental tool calls / Codex / Cursor)

```bash
pip install -r cli_agent/requirements.txt      # installs `mcp`
```

**Claude Code** — a `.mcp.json` is committed at the repo root; open Claude Code
here and approve the `tradingagents` server when prompted. It runs
`.venv/bin/python -m cli_agent.mcp_server`, so the venv must exist at `.venv/`.

**Codex** (`~/.codex/config.toml`):

```toml
[mcp_servers.tradingagents]
command = "/abs/path/to/TradingAgents-fork/.venv/bin/python"
args = ["-m", "cli_agent.mcp_server"]
```

Tools are all read-only. `trading_macro` needs `FRED_API_KEY`;
`trading_prediction_markets` is best-effort (Polymarket often times out).

## Notes

- **FRED macro** is skipped unless `FRED_API_KEY` is set (free key:
  <https://fred.stlouisfed.org/docs/api/api_key.html>). Add it to `.env`.
- **Prediction markets** (Polymarket) are off by default — pass
  `--prediction-markets` to include them; they often time out.
- The date is a point-in-time "now": everything is filtered to on-or-before it.
- This trades the framework's true independent multi-agent debate for one agent
  role-playing the sequence. Same data, same prompts, much cheaper. For a
  final high-stakes second opinion, `python -m cli.main` still runs the real
  orchestrator against the API.
- Not financial advice. Research tooling only.
