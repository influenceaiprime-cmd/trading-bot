"""
config.py - single source of truth for engine/strategy parameters.

Previously these constants were duplicated/hardcoded directly inside
bot_engine_v5.py, which meant the backtester (backtest.py) could never
guarantee it was testing the exact same parameters the live bot uses.
Both now import from here.
"""
import os
from dotenv import load_dotenv

# Loaded here, not just in bot_engine_v5.py: config.py reads os.getenv()
# below at IMPORT time. If .env is loaded later (e.g. bot_engine_v5.py used
# to call load_dotenv() after `import config`), these secrets get baked in
# as None permanently, even though the .env file is fine and dotenv can see
# it fine when tested directly. Loading it here means it works regardless
# of what order other files import config or call load_dotenv() themselves.
load_dotenv()

# ---------- MARKET ----------
PAIRS = ["BTCUSDT", "ETHUSDT", "SOLUSDT"]
INTERVAL = "1h"          # trading/entry timeframe
HTF_INTERVAL = "4h"      # higher-timeframe confirmation - MUST be a longer interval than INTERVAL
HTF_LOOKBACK = 20        # candles of HTF_INTERVAL used for the trend SMA

# ---------- RISK ----------
SL_PCT = 2.0                 # fixed fallback stop-loss %
TP_PCT = 4.0                 # fixed fallback take-profit % (ATR-based TP usually triggers first)
RISK_PCT = 2.0                # % of account risked per trade
MAX_OPEN_POSITIONS = 3
MAX_DAILY_LOSS_PCT = 5.0      # circuit breaker: stop opening trades after this much daily drawdown
COOLDOWN_MIN = 180            # minutes to wait after closing a position before re-entering same symbol

# ---------- EXITS ----------
TRAIL_ARM = 4.0    # arm trailing stop once price is this far in profit (%)
TRAIL_GAP = 1.5    # trailing stop distance below peak once armed (%)
ATR_SL_MULT = 1.5  # stop-loss distance = ATR * this multiplier (used if wider than SL_PCT)
ATR_TP_MULT = 3.0  # take-profit distance = ATR * this multiplier

# ---------- TREND-FOLLOWING VARIANT (strategy_trend.py) ----------
# Deliberately different exit philosophy from the mean-reversion strategy above:
# no fixed take-profit at all - winners are only cut when the trend itself breaks.
TREND_DONCHIAN_LOOKBACK = 20   # breakout = new N-bar high
TREND_BREAKOUT_MARGIN_PCT = 0.3  # require close to clear the prior high by this much, not just barely poke above it
TREND_TRAIL_ATR_MULT = 3.0     # trailing stop distance = ATR * this, trails the peak
TREND_HARD_STOP_ATR_MULT = 2.5  # catastrophic stop if price falls this many ATRs from entry

# ---------- EXECUTION ----------
MIN_TRADE_USDT = 10.0
API_RETRIES = 3
API_RETRY_DELAY = 5  # seconds
CYCLE_SLEEP_SECONDS = 300

# ---------- BACKTEST-ONLY ASSUMPTIONS ----------
# These don't affect live trading (real fills/fees come from the exchange) but
# make the backtest realistic instead of assuming free, instant, perfect fills.
TAKER_FEE_PCT = 0.1     # Binance default spot taker fee (adjust if you have a BNB discount / VIP tier)
SLIPPAGE_PCT = 0.05     # assumed slippage per market order, each side
BACKTEST_START_CAPITAL = 1000.0

# ---------- FILES ----------
STATE_FILE = "bot_state.json"
EQUITY_FILE = "equity_log.csv"

# ---------- LIVE TRADING PAIR SELECTION ----------
# PAIRS above is the full backtest universe. LIVE_PAIRS is what actually
# trades with real orders. ETH is excluded here by default: across every
# backtest window tested (2023-24 bull run, last 365 days, 2021-22 crash),
# ETH consistently generated more false breakouts and slightly worse average
# losses than BTC/SOL under both the mean-reversion and trend-following
# strategies. See TREND_SYMBOL_OVERRIDES below for the alternative approach
# (give ETH wider parameters instead of excluding it) - test that further
# before trusting it live.
LIVE_PAIRS = ["BTCUSDT", "SOLUSDT"]

# ---------- PER-SYMBOL STRATEGY OVERRIDES (trend-following) ----------
# ETH showed a meaningfully higher false-breakout rate than BTC in every
# backtest window (e.g. 43 ETH trades vs 30 BTC trades over the same 2021-22
# crash window, with a worse average hard-stop loss: -3.8% vs -3.0%). This
# lets a symbol use wider/stricter parameters instead of the global
# TREND_* defaults, so it can still be tested rather than just dropped.
# Empty dict for a symbol = it uses the global defaults.
TREND_SYMBOL_OVERRIDES = {
    "ETHUSDT": {
        "TREND_BREAKOUT_MARGIN_PCT": 0.6,   # require a clearer breakout (global default: 0.3)
        "TREND_HARD_STOP_ATR_MULT": 3.2,    # more room before the hard stop (global default: 2.5)
        "TREND_TRAIL_ATR_MULT": 3.5,        # wider trailing stop too (global default: 3.0)
    },
}

# ---------- SECRETS (loaded from .env, never hardcode these) ----------
BINANCE_API_KEY = os.getenv("BINANCE_API_KEY")
BINANCE_API_SECRET = os.getenv("BINANCE_API_SECRET")
TELEGRAM_TOKEN = os.getenv("TELEGRAM_TOKEN")
TELEGRAM_CHAT_ID = os.getenv("TELEGRAM_CHAT_ID")
DISCORD_WEBHOOK = os.getenv("DISCORD_WEBHOOK")
