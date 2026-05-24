"""
/scan-watchlist — runs /analyze-stock on all watchlist stocks.
Outputs top 5 BUY + top 5 SELL ranked by composite score.
Writes signals to data/signals/{SYMBOL}_signal.json AND Supabase.
"""
import json
from pathlib import Path
from local_db import upsert_signal

WATCHLIST = [
    "RELIANCE", "TCS", "HDFCBANK", "ICICIBANK", "INFY",
    "SBIN", "HINDUNILVR", "ITC", "LT", "BAJFINANCE",
]
SECTOR_MAP = {
    "RELIANCE": "Energy",       "TCS": "IT/Tech",
    "HDFCBANK": "Banking Private", "ICICIBANK": "Banking Private",
    "INFY": "IT/Tech",          "SBIN": "Banking PSU",
    "HINDUNILVR": "FMCG",       "ITC": "FMCG",
    "LT": "Capital Goods",      "BAJFINANCE": "NBFC",
}
SECTOR_PE = {
    "IT/Tech": 30, "FMCG": 47, "Banking Private": 20,
    "Banking PSU": 10, "Energy": 14, "NBFC": 28, "Capital Goods": 25,
}


def load(path: str) -> dict:
    p = Path(path)
    return json.loads(p.read_text()) if p.exists() else {}


def g(d: dict, key: str, default=None):
    v = d.get(key, default)
    return default if v is None else v


def analyze(sym: str) -> dict:
    mkt  = load(f"data/market/{sym}_ohlcv.json")
    fund = load(f"data/fundamentals/{sym}_fund.json")
    sent = load(f"data/sentiment/{sym}_sent.json")

    # ── Technical score (0–1) ──────────────────────────────────────────
    rsi           = g(mkt, "rsi", 50.0)
    macd_hist     = g(mkt, "macd_hist", 0.0)
    macd_crossover= g(mkt, "macd_crossover", False)
    above_ema200  = g(mkt, "above_ema200", False)
    vol_ratio     = g(mkt, "vol_ratio", 1.0)
    bb_pct        = g(mkt, "bb_pct", 0.5)

    t = 0.0
    # RSI contribution
    if rsi < 30:    t += 0.30   # oversold — strong buy signal
    elif rsi < 40:  t += 0.20
    elif rsi < 50:  t += 0.10
    elif rsi > 75:  t -= 0.20   # overbought — avoid
    elif rsi > 65:  t -= 0.10
    # MACD
    if macd_crossover:    t += 0.25  # fresh bullish cross
    elif macd_hist > 0:   t += 0.15  # already above signal
    elif macd_hist < 0:   t -= 0.10
    # EMA-200 trend
    if above_ema200:      t += 0.20
    else:                 t -= 0.05
    # Volume confirmation
    if vol_ratio and vol_ratio > 1.5:  t += 0.15
    elif vol_ratio and vol_ratio > 1.2: t += 0.05
    # Bollinger: near lower band = oversold, near upper = overbought
    if bb_pct < 0.2:  t += 0.10
    elif bb_pct > 0.8: t -= 0.10
    t = max(0.0, min(1.0, t + 0.15))  # baseline offset

    # ── Fundamental score (reuse pre-computed if available) ────────────
    f = g(fund, "fundamental_score", None)
    if f is None:
        pe         = g(fund, "pe_ratio", 0)
        roe        = g(fund, "roe", 0)          # decimal e.g. 0.15 = 15%
        de         = g(fund, "de_ratio")
        rev_growth = g(fund, "revenue_growth", 0) or g(fund, "earnings_growth", 0)
        bench_pe   = SECTOR_PE.get(SECTOR_MAP.get(sym, ""), 20)
        f = 0.45
        if pe and bench_pe:
            ratio = pe / bench_pe
            if ratio < 0.7:   f += 0.25
            elif ratio < 0.9: f += 0.15
            elif ratio > 1.6: f -= 0.15
        if roe:
            if roe > 0.20:    f += 0.20
            elif roe > 0.12:  f += 0.10
        if isinstance(de, float) and de < 1.0: f += 0.15
        if rev_growth and rev_growth > 0.10:   f += 0.15
        elif rev_growth and rev_growth > 0:    f += 0.05
        f = max(0.0, min(1.0, f))

    # ── Sentiment score (already 0–1, 0.5 = neutral) ─────────────────
    s = g(sent, "sentiment_score", 0.5)

    # ── Composite: Tech 35% + Fund 30% + News 20% + Sentiment 15% ────
    # Using sentiment as proxy for both news and social
    composite = round(0.35*t + 0.30*f + 0.20*s + 0.15*s, 4)

    action = "BUY" if composite >= 0.56 else ("SELL" if composite <= 0.42 else "HOLD")
    confidence = "HIGH" if composite > 0.65 else ("MEDIUM" if composite > 0.55 else "LOW")

    # Entry / SL / Target using ATR
    ltp = g(mkt, "last_close", 0.0)
    atr = g(mkt, "atr", ltp * 0.015 if ltp else 1.0)
    entry  = round(ltp, 2) if ltp else None
    sl     = round(ltp - 1.5 * atr, 2) if ltp and atr else None
    target = round(ltp + 2.5 * atr, 2) if ltp and atr else None
    rr     = round((target - ltp) / (ltp - sl), 2) if sl and target and ltp and (ltp - sl) > 0 else None

    reasoning = (
        f"RSI {rsi:.1f} ({'oversold' if rsi<40 else 'overbought' if rsi>65 else 'neutral'}), "
        f"MACD hist {macd_hist:.2f} ({'bullish cross' if macd_crossover else 'positive' if macd_hist>0 else 'negative'}), "
        f"{'ABOVE' if above_ema200 else 'BELOW'} EMA-200, "
        f"Vol ratio {vol_ratio:.1f}x, "
        f"Sent {s:.3f} ({'positive' if s>0.55 else 'negative' if s<0.45 else 'neutral'})"
    )

    signal = {
        "symbol": sym, "tier": "EQUITY", "action": action,
        "composite_score": composite,
        "technical_score": round(t, 3),
        "fundamental_score": round(f, 3),
        "sentiment_score": round(s, 3),
        "confidence": confidence,
        "entry_price": entry, "stop_loss": sl, "target": target, "risk_reward": rr,
        "rsi": round(rsi, 1), "above_ema200": above_ema200,
        "macd_crossover": macd_crossover, "vol_ratio": round(vol_ratio or 1.0, 2),
        "pe": round(g(fund, "pe_ratio", 0), 1) or None,
        "reasoning": reasoning, "executed": False,
    }

    # Write local signal file
    sig_dir = Path("data/signals")
    sig_dir.mkdir(exist_ok=True)
    (sig_dir / f"{sym}_signal.json").write_text(json.dumps(signal, indent=2))

    # Push to Supabase so dashboard updates live
    try:
        upsert_signal(signal)
    except Exception as exc:
        print(f"  [warn] Supabase write failed for {sym}: {exc}")

    return signal


# ── Run scan ──────────────────────────────────────────────────────────────────

results = [analyze(s) for s in WATCHLIST]
results.sort(key=lambda x: x["composite_score"], reverse=True)

buys  = [r for r in results if r["action"] == "BUY"]
sells = [r for r in results if r["action"] == "SELL"]
holds = [r for r in results if r["action"] == "HOLD"]

W = 72
print("=" * W)
print("  /scan-watchlist  --  India Auto-Trader  --  NSE/BSE Signal Scan")
print("=" * W)
print(f"{'SYMBOL':<13}{'ACTION':<7}{'SCORE':<8}{'TECH':<7}{'FUND':<7}{'SENT':<7}{'RSI':<7}{'EMA200':<8}{'P/E'}")
print("-" * W)
for r in results:
    ema   = "ABOVE" if r["above_ema200"] else "BELOW"
    rsi   = str(r["rsi"])  if r["rsi"]  else "--"
    pe    = str(r["pe"])   if r["pe"]   else "--"
    tag   = " <BUY" if r["action"] == "BUY" else (" <SELL" if r["action"] == "SELL" else "")
    print(f"{r['symbol']:<13}{r['action']:<7}{r['composite_score']:<8.4f}{r['technical_score']:<7.3f}{r['fundamental_score']:<7.3f}{r['sentiment_score']:<7.3f}{rsi:<7}{ema:<8}{pe}{tag}")

print()
print(f"TOP BUY  ({len(buys)})  : {', '.join(r['symbol'] for r in buys[:5])  or 'None'}")
print(f"TOP SELL ({len(sells)}) : {', '.join(r['symbol'] for r in sells[:5]) or 'None'}")
print(f"HOLD     ({len(holds)}) : {', '.join(r['symbol'] for r in holds[:5]) or 'None'}")

if buys:
    print()
    print("-- BUY SETUPS " + "-" * (W - 14))
    for r in buys[:5]:
        rr = f"  R:R {r['risk_reward']}" if r["risk_reward"] else ""
        sl = f"SL {r['stop_loss']}" if r["stop_loss"] else ""
        tg = f"Target {r['target']}" if r["target"] else ""
        print(f"  {r['symbol']:<12} Entry {r['entry_price']}  {sl}  {tg}{rr}  [{r['confidence']}]")
        print(f"             {r['reasoning']}")

print("=" * W)
print(f"Signals written to data/signals/  ({len(results)} stocks scanned)")
