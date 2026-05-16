# India Auto-Trader

AI-powered automated trading system for NSE/BSE.  
**Claude Code is the AI brain** — no separate Anthropic API key needed.

## Quick Start

### 1. Python environment
```bash
cd india-auto-trader
python -m venv .venv
.venv\Scripts\activate          # Windows
pip install -e ".[dev]"
```

### 2. Environment variables
```bash
cp .env.example .env
# Edit .env — fill in KOTAK_* keys, TWITTER_BEARER_TOKEN, FINNHUB_API_KEY, TELEGRAM_*
```

### 3. Start MCP server (auto-started by Claude Code)
The MCP server starts automatically when Claude Code loads.  
Verify: open Claude Code → run `/risk-status` → should call `get_limits()`.

### 4. Collect initial data
```bash
python -m data_collector.collect_all --fast
```

### 5. Start FastAPI backend
```bash
uvicorn backend.main:app --host 0.0.0.0 --port 8000 --reload
```

### 6. Start frontend
```bash
cd frontend
npm install
npm run dev
# Open http://localhost:3000
```

### 7. Paper trading (optional)
```bash
python -m backtesting.paper_trader --capital 500000
```

---

## Claude Code Skills

Open Claude Code in this directory and use these slash commands:

| Skill | Description |
|-------|-------------|
| `/morning-scan` | Pre-market scan: circuit check, watchlist, PCR, FII/DII |
| `/scan-watchlist` | Analyze all Nifty 200 stocks, output top BUY/SELL |
| `/analyze-stock RELIANCE` | Deep 4-factor analysis for one stock |
| `/analyze-options NIFTY 29MAY2026` | PCR, max pain, OI strategy |
| `/scan-penny-stocks` | Filter NSE SME platform stocks |
| `/check-earnings` | Upcoming results + pre/post strategies |
| `/execute-trade RELIANCE BUY` | Execute equity trade |
| `/execute-options-trade NIFTY CE 22500 29MAY2026` | Execute F&O trade |
| `/penny-trade SYMBOL` | Execute penny trade |
| `/check-positions` | Review all open positions |
| `/risk-status` | Full risk report |

---

## Architecture

```
Claude Code (AI brain)
  ↓ reads data/ JSON files
  ↓ calls Kotak Neo via MCP tools
  
Data Collector (Python cron)
  → data/market/      OHLCV + indicators
  → data/fundamentals/ P/E, ROE, D/E
  → data/options/     Chain, OI, PCR, Max Pain
  → data/penny/       SME candidates
  → data/earnings/    Calendar + results
  → data/sentiment/   Twitter + FII/DII
  → data/news/        Finnhub articles

FastAPI Backend (Port 8000) → Next.js Frontend (Port 3000)
```

## Data Refresh Schedule (recommended)

| Data | Interval | Command |
|------|----------|---------|
| OHLCV + Indicators | 5 min | `collect_all --equity` |
| Option chains | 5 min | `collect_all --options` |
| News + Sentiment | 30 min | part of `collect_all` |
| Fundamentals | Daily | part of `collect_all` |
| Penny scanner | Daily | `collect_all --penny` |
| FII/DII | Daily (after 6 PM) | part of `collect_all` |
