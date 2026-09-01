# Changelog

## v5.2 — Shared strategy module, config, hardening
- Extracted all indicator/scoring logic into `strategy.py`, shared by
  `bot_engine_v5.py` and the new `backtest.py`. Previously any strategy
  logic lived only in the live engine, so a backtest was impossible to
  trust — it would have been testing different code than what trades live.
- Extracted all tunable parameters into `config.py` (was scattered as
  module-level constants duplicated across files).
- **Fixed `htf.py`**: the "higher timeframe" filter was hardcoded to `1h`,
  the same interval the bot already trades on — it added almost no real
  confirmation. Now uses `4h` by default (`config.HTF_INTERVAL`), a
  genuinely higher timeframe.
- Replaced ad hoc `print()` logging with a proper rotating file logger
  (`engine.log`, 5 x 2MB rotation) plus console output.
- Added `backtest.py`: historical simulation with trading-fee and slippage
  assumptions, shared portfolio cash across symbols, equity curve export,
  win rate / Sharpe / max drawdown reporting.
- Added `dashboard.py`: generates a static `dashboard.html` report (equity
  curve chart + trade stats) from either live logs or backtest output.
- Added `requirements.txt`, `README.md`, `.env.example`, `LICENSE` template
  for anyone packaging this as a distributable project.

## v5.1 — Bug fixes and risk hardening
- **Position sizing** previously used a hardcoded `$10,000` account size
  regardless of actual balance — now uses the real live USDT balance.
- **Exit orders** previously sold the entire wallet balance of an asset,
  not just the bot's own tracked position — now sells exactly `pos['qty']`.
- **Order quantities** are now rounded to each symbol's real exchange
  `LOT_SIZE` step (pulled from `get_symbol_info`) instead of a blind
  `round(qty, 5)` that could get silently rejected by Binance.
- **RSI** no longer produces `NaN`/`inf` when a window has zero down-moves.
- **`TELEGRAM_TOKEN`/`TELEGRAM_CHAT_ID`** were loaded but never used —
  wired up alongside the existing Discord webhook.
- **Failed sell orders** no longer clear the position from state — a failed
  exit previously still marked the bot as flat, silently losing track of
  an open position.
- **`log()` / `journal.py` mismatch**: `log()` wrote lines with `" | "`
  replaced by `,`, but `journal.py` parsed those same files by splitting on
  `|` — meaning the trade journal silently never parsed any v5-format log
  line. Both sides now use a real CSV format (`time,message` columns), and
  `journal.py` extracts actual P&L percentages from the message text.
- **`htf_uptrend`** previously failed *open* (returned `True`, "trend
  confirmed") on any API error — now fails *closed* (`False`, veto the
  trade) so a broken API call can't silently bypass trend confirmation.
- Added a daily loss circuit breaker (`MAX_DAILY_LOSS_PCT`) that stops
  opening new positions once daily drawdown crosses the configured limit.
- Added ATR-based dynamic stop-loss/take-profit distances alongside the
  existing fixed-percentage stop and trailing-stop logic.
- Added a retry wrapper around Binance API calls so a single dropped
  connection doesn't crash a whole trading cycle.
