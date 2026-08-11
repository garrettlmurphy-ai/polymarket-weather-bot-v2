# From Paper to Live — Code Review & Migration Plan

This document is the honest assessment of what the bot is today, what it would
take to trade real money, and — more importantly — **whether it should**.

---

## 1. What exists today

| Component | Role | Verdict |
|-----------|------|---------|
| `bot.py` | Scans markets, builds a 3-source weather ensemble, prices directional & bracket markets | Works, but the edge is model-error-prone (see §4) |
| `tail_end_scanner.py` | "Known outcome" arbitrage — temp already observed past threshold | The only structurally sound edge |
| `paper_trader.py` | Simulates trades, tracks P&L | Now models realistic taker costs; **never places real orders** |
| `research_engine.py` | Daily markdown brief | Fixed (was silently dropping tail-end data) |
| `daily_summary.py` | Telegram summary | Secrets moved to env |
| `calibrate.py` (new) | Reliability curve, Brier score, sigma refit | The tool that decides if we go live |

**There is no live execution anywhere.** No Polymarket CLOB client, no wallet, no
order signing, no allowances. "Running for months" = months of *simulated* fills.

---

## 2. Do NOT go live until these gates pass

Going live on an unvalidated strategy is the fastest way to lose the bankroll.
Gate the switch on data, not optimism:

1. **Net-of-cost ROI is positive** over ≥200 closed paper trades *with the new
   realistic fill model* (spread + slippage + fee + liquidity cap). Run
   `python calibrate.py`. Old paper results were priced at the mid — ignore them.
2. **Brier score < 0.20** and the reliability curve is roughly diagonal (a
   predicted-70% bucket should win ~70%). If the model is overconfident, the
   "edges" are illusions.
3. **Sigma multiplier ≈ 1.0** from `calibrate.py`. If the best-fit multiplier is
   1.3+, the forecast table understates uncertainty and every edge is inflated —
   fix the table and re-validate first.
4. **The known-outcome strategy is profitable on its own.** It's the one with a
   real mechanism; prove it in isolation before risking the directional book.

If those don't hold, the answer to "how do we make it profitable" is **the
strategy isn't there yet** — no execution layer fixes a negative edge.

---

## 3. What the live execution layer needs (once gates pass)

Polymarket order placement is via the **CLOB API** using the official
`py-clob-client`. The paper trader's `place_paper_trade` is the seam to replace.

Concrete pieces to build:

- **Wallet & funding.** A funded Polygon wallet (USDC.e), private key held in
  `.env` (never committed — the new `.gitignore` covers this).
- **Client + auth.** `py-clob-client` with API creds derived from the key;
  one-time USDC allowance approval to the exchange contract.
- **Real prices, not mids.** Replace the Gamma `outcomePrices` read with the
  live order book (`/book`) — quote the actual **best ask** and available size.
  The `realistic_fill_price()` helper is a stand-in for exactly this.
- **Order type.** Prefer **marketable limit orders** with a max price = your
  edge threshold, so you never pay worse than your model allows. Never market-buy
  a thin book.
- **Fills & reconciliation.** Poll order status; record actual fill price/size
  (partial fills are normal). Update state from *confirmed* fills only.
- **Idempotency & restart safety.** On restart, reconcile open orders/positions
  from the exchange, not just local JSON, or you'll double-trade.

---

## 4. Why the directional strategy is structurally weak

The model computes `P(temp > threshold)` from a normal CDF and a **guessed**
`sigma`, then bets when that differs ≥10% from the market price. But:

- Open-Meteo and NWS are **free and public**. The market price already reflects
  them. Professional makers use the same or better data.
- Your "edge" is therefore dominated by **your sigma-calibration error**, not
  genuine mispricing. Wrong sigma manufactures fake edges at the tails.
- After spread + slippage + fees, a 10% nominal edge is often gone (the paper
  trader now subtracts these and re-checks — expect far fewer signals).

This is why §2 leads with calibration. The path to profit is **narrowing to the
known-outcome arb and proving edge net of costs**, not scanning more markets.

---

## 5. Risk controls to add before any real capital

- Global kill-switch (env flag) and a daily max-loss that halts trading.
- Hard per-market and per-day exposure caps (the Kelly cap alone isn't enough).
- Alert on any resolution that contradicts a "known outcome" call — that means
  the safety margin or data source is wrong, and it should pause the strategy.
- Stale-data guard: refuse to trade on a forecast/observation older than N minutes.

---

## 6. Fixed in this pass

- Secrets removed from source → env vars (`.env`, git-ignored; `.env.example`
  provided). **Rotate the old keys — they're in git history.**
- Realistic cost model in the paper trader (ask price, fee, slippage, liquidity
  cap) + post-cost edge re-check.
- `calibrate.py` for reliability / Brier / sigma refit.
- Research brief no longer silently drops tail-end trades (key mismatches fixed).
- `SIGMA_BRACKET` is now actually used for bracket pricing (was dead code).
- Guarded Tomorrow.io calls when the key is absent; fixed potential `NameError`
  on empty forecast arrays. Portable file paths via `DATA_DIR`.
- Enriched trade records (`forecast_mus`, `threshold_f`, …) so sigma can be
  recalibrated from real outcomes.

---

*Bottom line: the engineering is now in a fit state to answer the profitability
question. Run the paper trader with the realistic model, let `calibrate.py`
accumulate ~200 trades, and let the gates in §2 make the go-live decision.*
