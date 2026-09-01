# Trading Bot

An automated crypto trading engine for Binance (spot), with a rules-based
strategy (RSI/MACD/Bollinger/ATR scoring + multi-timeframe trend filter),
risk management (ATR-based stops, trailing stop, daily loss circuit
breaker), a backtesting framework, and a results dashboard.

## ⚠️ Read this before running it with real money

- **This is beta software with no long-term live track record.** It runs on
  Binance testnet by default (`testnet=True` in `bot_engine_v5.py`).
- **Nothing here is financial advice.** You are responsible for any trades
  it places and any losses it produces. Past backtest performance does not
  guarantee future results — markets change regimes.
- **Run `backtest.py` and testnet for an extended period before considering
  real funds.** Read `backtest.py`'s docstring for its known limitations.
- If you plan to offer this to other people in any form (sold software,
  hosted service, signals), get advice from a lawyer familiar with your
  jurisdiction's securities/investment-adviser rules before taking anyone's
  money. Requirements differ a lot depending on whether you're selling
  software people run themselves vs. running trades on their behalf.

## Setup

```bash
git clone <your-repo-url>
cd trading-bot
pip install -r requirements.txt
cp .env.example .env   # then fill in your API keys
```

Get free testnet API keys at https://testnet.binance.vision before touching
anything real.

## Usage

**Run the live engine (testnet by default):**
```bash
python bot_engine_v5.py            # runs continuously
python bot_engine_v5.py --once     # runs a single cycle then exits (used by the GitHub Actions workflow)
```

**Backtest the strategy against historical data:**
```bash
python backtest.py --days 365
python backtest.py --symbol BTCUSDT --days 180
```

**Generate a results dashboard:**
```bash
python dashboard.py              # from live trading logs
python dashboard.py --backtest   # from the most recent backtest run
```
Open `dashboard.html` in a browser.

**Review closed-trade stats from the command line:**
```bash
python journal.py
```

## Project layout

| File | Purpose |
|---|---|
| `config.py` | All tunable parameters (risk %, pairs, timeframes, etc.) in one place |
| `strategy.py` | The actual signal logic — shared by the live engine and the backtester so they never drift apart |
| `bot_engine_v5.py` | Live execution loop: state persistence, order placement, alerts, logging |
| `binance_data.py` | Historical kline fetching for the live engine |
| `htf.py` | Higher-timeframe trend confirmation filter |
| `backtest.py` | Historical simulation with fees/slippage, equity curve, Sharpe/drawdown |
| `dashboard.py` | Static HTML report generator (live or backtest) |
| `journal.py` | Command-line trade stats summary |

## Configuration

Edit `config.py` to change: which pairs trade, risk-per-trade %, max open
positions, stop-loss/take-profit logic, daily loss circuit breaker, and
backtest fee/slippage assumptions.

## License

See `LICENSE`. In short: provided as-is, no warranty, you assume all risk
of using it — see the license file for the full terms.
