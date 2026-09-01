import sys
"""
BOT ENGINE v5.2 - LIVE EXECUTION + MEMORY + ALERTS (hardened, shared strategy)

This version now imports its parameters from config.py and its signal logic
from strategy.py, instead of duplicating both inline. That means:
  - backtest.py tests the EXACT same entry/exit logic this file trades with
  - changing a risk parameter means editing config.py once, not hunting
    through multiple files
  - the higher-timeframe filter (htf.py) now actually uses a higher
    timeframe (4h) instead of re-checking the same 1h candles

See CHANGELOG.md for the full history of bug fixes across versions.
"""
import os
import csv
import time
import json
import math
import logging
import traceback
from logging.handlers import RotatingFileHandler
from datetime import datetime, date

import pandas as pd
import numpy as np
from binance.client import Client
from binance.exceptions import BinanceAPIException

import config  # config.py loads .env itself - see the fix at its top
from binance_data import get_klines_df
from htf import htf_uptrend
import strategy_trend as strategy

client = Client(config.BINANCE_API_KEY, config.BINANCE_API_SECRET, testnet=True)

# ---------- LOGGING ----------
logger = logging.getLogger("bot_engine")
logger.setLevel(logging.INFO)
_console = logging.StreamHandler()
_console.setFormatter(logging.Formatter("%(asctime)s | %(message)s", "%Y-%m-%d %H:%M:%S"))
_file = RotatingFileHandler("engine.log", maxBytes=2_000_000, backupCount=5)
_file.setFormatter(logging.Formatter("%(asctime)s | %(levelname)s | %(message)s", "%Y-%m-%d %H:%M:%S"))
logger.addHandler(_console)
logger.addHandler(_file)


def with_retries(fn, *args, **kwargs):
    """Call fn(*args, **kwargs), retrying on transient Binance/network errors."""
    last_err = None
    for attempt in range(1, config.API_RETRIES + 1):
        try:
            return fn(*args, **kwargs)
        except BinanceAPIException as e:
            last_err = e
            if e.code in (-2010, -1121):  # insufficient balance, invalid symbol - won't succeed on retry
                raise
            logger.warning(f"API error attempt {attempt}/{config.API_RETRIES}: {e}")
        except Exception as e:
            last_err = e
            logger.warning(f"error attempt {attempt}/{config.API_RETRIES}: {e}")
        time.sleep(config.API_RETRY_DELAY)
    raise last_err


# ---------- STATE PERSISTENCE ----------
def load_state():
    if os.path.exists(config.STATE_FILE):
        with open(config.STATE_FILE) as f:
            state = json.load(f)
    else:
        state = {}
    state.setdefault("positions", {})
    state.setdefault("cooldowns", {})
    state.setdefault("last_prices", {})
    state.setdefault("daily_start_equity", None)
    state.setdefault("daily_date", None)
    return state


def save_state():
    with open(config.STATE_FILE, "w") as f:
        json.dump(STATE, f, indent=2)


STATE = load_state()
SYMBOL_FILTERS = {}  # exchange LOT_SIZE / MIN_NOTIONAL per symbol, loaded at startup


def load_symbol_filters():
    for symbol in config.LIVE_PAIRS:
        try:
            info = with_retries(client.get_symbol_info, symbol)
            step_size = min_qty = min_notional = None
            for f in info["filters"]:
                if f["filterType"] == "LOT_SIZE":
                    step_size = float(f["stepSize"])
                    min_qty = float(f["minQty"])
                elif f["filterType"] in ("MIN_NOTIONAL", "NOTIONAL"):
                    min_notional = float(f.get("minNotional", f.get("notional", 10)))
            SYMBOL_FILTERS[symbol] = {
                "step_size": step_size or 0.00001,
                "min_qty": min_qty or 0.0,
                "min_notional": min_notional or config.MIN_TRADE_USDT,
            }
        except Exception as e:
            logger.warning(f"couldn't load filters for {symbol}, using defaults: {e}")
            SYMBOL_FILTERS[symbol] = {
                "step_size": 0.00001,
                "min_qty": 0.0,
                "min_notional": config.MIN_TRADE_USDT,
            }


def round_step(qty, step):
    if step <= 0:
        return qty, 8
    precision = int(round(-math.log10(step)))
    return math.floor(qty / step) * step, precision


def format_qty(symbol, qty):
    f = SYMBOL_FILTERS.get(symbol, {"step_size": 0.00001, "min_qty": 0.0})
    stepped, precision = round_step(qty, f["step_size"])
    if stepped < f["min_qty"]:
        return 0.0
    return round(stepped, max(precision, 0))


# ---------- ALERTS ----------
def notify(msg):
    logger.info(f"ALERT: {msg}")
    if config.DISCORD_WEBHOOK:
        try:
            import requests
            requests.post(config.DISCORD_WEBHOOK, json={"content": "\U0001F916 " + msg}, timeout=15)
        except Exception as e:
            logger.warning(f"discord failed: {e}")
    if config.TELEGRAM_TOKEN and config.TELEGRAM_CHAT_ID:
        try:
            import requests
            url = f"https://api.telegram.org/bot{config.TELEGRAM_TOKEN}/sendMessage"
            requests.post(url, data={"chat_id": config.TELEGRAM_CHAT_ID, "text": msg}, timeout=15)
        except Exception as e:
            logger.warning(f"telegram failed: {e}")


def log(symbol, msg):
    """Per-symbol structured CSV trade log, readable by journal.py."""
    ts = f"{datetime.now():%Y-%m-%d %H:%M:%S}"
    logger.info(f"[{symbol}] {msg}")
    fname = f"trades_log_{symbol.replace('USDT', '')}.csv"
    new_file = not os.path.exists(fname)
    with open(fname, "a", newline="") as f:
        writer = csv.writer(f)
        if new_file:
            writer.writerow(["time", "message"])
        writer.writerow([ts, msg])


def get_position(symbol):
    bal = with_retries(client.get_asset_balance, asset=symbol.replace("USDT", ""))
    return float(bal['free'])


def get_usdt_balance():
    bal = with_retries(client.get_asset_balance, asset="USDT")
    return float(bal['free'])


# ---------- DAILY LOSS CIRCUIT BREAKER ----------
def check_daily_reset(current_equity):
    today = date.today().isoformat()
    if STATE.get("daily_date") != today:
        STATE["daily_date"] = today
        STATE["daily_start_equity"] = current_equity
        save_state()


def daily_loss_exceeded(current_equity):
    start = STATE.get("daily_start_equity")
    if not start or start <= 0:
        return False
    drawdown_pct = (start - current_equity) / start * 100
    return drawdown_pct >= config.MAX_DAILY_LOSS_PCT


# ---------- EXITS ----------
def check_exit(symbol, price, row):
    """
    Trend-following exits: NO fixed take-profit by design (see strategy_trend.py's
    docstring for why) - only a wide ATR-based trailing stop, a catastrophic
    hard stop, or the trend itself breaking (close back below SMA50).
    """
    p = STATE["positions"].get(symbol)
    if not p:
        return

    should_exit, exit_reason, peak = strategy.check_exit(price, p, symbol=symbol)
    p['peak'] = peak
    if not should_exit and price < row['SMA50']:
        should_exit, exit_reason = True, "TREND-BROKEN"

    save_state()  # persist the updated peak even if we're not exiting yet

    if not should_exit:
        return

    pnl = (price - p['entry']) / p['entry'] * 100
    qty = p['qty']
    real_qty = get_position(symbol)
    sell_qty = format_qty(symbol, min(qty, real_qty))
    if sell_qty <= 0:
        log(symbol, f"EXIT SKIPPED ({exit_reason}) - qty too small or balance mismatch (tracked={qty}, wallet={real_qty})")
    else:
        try:
            with_retries(client.order_market_sell, symbol=symbol, quantity=sell_qty)
            log(symbol, f"SELL(exit) {exit_reason} {pnl:.2f}% @ {price} (peak {peak:.2f})")
            emoji = "\U0001F6D1" if exit_reason in ("HARD-STOP", "TRAIL-STOP") and pnl < 0 else "\u2705"
            notify(f"{emoji} {symbol} {exit_reason} {pnl:+.2f}% @ ${price:,.2f}")
        except Exception as e:
            log(symbol, f"EXIT FAILED ({exit_reason}): {e}")
            return  # don't clear the position if the sell actually failed

    STATE["positions"][symbol] = None
    STATE["cooldowns"][symbol] = time.time() + config.COOLDOWN_MIN * 60
    save_state()


# ---------- ENTRIES ----------
def check_entry(symbol, df, daily_locked):
    if daily_locked:
        return
    if STATE["positions"].get(symbol):
        return
    if time.time() < STATE["cooldowns"].get(symbol, 0):
        return
    if len([v for v in STATE["positions"].values() if v]) >= config.MAX_OPEN_POSITIONS:
        return

    row, prev = df.iloc[-1], df.iloc[-2]
    price = float(row['Close'])

    ok, reason = strategy.entry_allowed(row, prev, symbol=symbol)
    if not ok:
        return

    usdt = get_usdt_balance()
    if usdt <= 10:
        return
    if not htf_uptrend(client, symbol):
        return

    atr_pct = row['ATR'] / price * 100 if not np.isnan(row['ATR']) else 1.0
    qty, mult, stop_pct = strategy.position_size(usdt, price, atr_pct, symbol=symbol)
    qty = format_qty(symbol, qty)

    min_notional = SYMBOL_FILTERS.get(symbol, {}).get("min_notional", config.MIN_TRADE_USDT)
    if qty <= 0 or qty * price < min_notional:
        return

    try:
        with_retries(client.order_market_buy, symbol=symbol, quantity=qty)
        atr = row['ATR']
        STATE["positions"][symbol] = {
            'entry': price,
            'qty': qty,
            'time': datetime.now().isoformat(),
            'peak': price,
            'atr_entry': atr,  # used by strategy_trend.check_exit for trail/hard-stop distances
        }
        save_state()
        log(symbol, f"BUY {qty} @ {price} | {reason} | vol_mult={mult} | ATR={atr:.2f}")
        notify(f"\U0001F7E2 BUY {symbol}: {qty} @ ${price:,.2f} | {reason}")
    except Exception as e:
        log(symbol, f"BUY FAILED: {e}")


# ---------- EQUITY TRACKING ----------
def compute_equity():
    usdt = get_usdt_balance()
    crypto_val = 0.0
    for s in config.LIVE_PAIRS:
        pos = STATE["positions"].get(s)
        if pos and s in STATE["last_prices"]:
            crypto_val += pos['qty'] * STATE["last_prices"][s]
    return usdt, crypto_val, usdt + crypto_val


def log_equity():
    usdt, crypto_val, total = compute_equity()
    new_file = not os.path.exists(config.EQUITY_FILE)
    with open(config.EQUITY_FILE, "a") as f:
        if new_file:
            f.write("time,usdt,crypto_value,total\n")
        f.write(f"{datetime.now():%Y-%m-%d %H:%M},{usdt:.2f},{crypto_val:.2f},{total:.2f}\n")
    return total


# ---------- MAIN LOOP ----------
def run_cycle():
    _, _, total_equity = compute_equity()
    check_daily_reset(total_equity)
    locked = daily_loss_exceeded(total_equity)
    if locked:
        logger.warning(f"circuit breaker: daily loss limit hit - no new entries this cycle (equity ${total_equity:,.2f})")

    for symbol in config.LIVE_PAIRS:
        try:
            df = get_klines_df(config.BINANCE_API_KEY, config.BINANCE_API_SECRET, symbol, config.INTERVAL, 200)
            d = strategy.analyze(df)
            row = d.iloc[-1]
            price = float(row['Close'])
            STATE["last_prices"][symbol] = price
            pos = STATE["positions"].get(symbol)
            if pos:
                pnl = (price - pos['entry']) / pos['entry'] * 100
                status = f"IN pos {pos['qty']} from {pos['entry']:.2f} (pnl {pnl:+.2f}%)"
            else:
                cd_left = int((STATE["cooldowns"].get(symbol, 0) - time.time()) / 60)
                status = "flat" + (f" | cooldown {cd_left}m left" if cd_left > 0 else "")
            logger.info(f"{symbol}: ${price:,.2f} | {status}")
            check_exit(symbol, price, row)
            check_entry(symbol, d, locked)
        except Exception as e:
            logger.error(f"{symbol}: ERROR {e}")
            logger.error(traceback.format_exc())

    save_state()
    total = log_equity()
    logger.info(f"TOTAL EQUITY: ${total:,.2f} (logged to {config.EQUITY_FILE})")


def main():
    logger.info("=" * 60)
    logger.info("  BOT ENGINE v5.2 - LIVE TESTNET + MEMORY + ALERTS (hardened)")
    logger.info(f"  State file: {config.STATE_FILE} | Ctrl+C to stop")
    logger.info("=" * 60)

    load_symbol_filters()
    one_shot = "--once" in sys.argv

    try:
        while True:
            logger.info(f"--- Cycle {datetime.now():%H:%M:%S} ---")
            run_cycle()
            if one_shot:
                break
            time.sleep(config.CYCLE_SLEEP_SECONDS)
    except KeyboardInterrupt:
        save_state()
        logger.info("Engine stopped SAFELY - state saved, positions remembered for next run.")


if __name__ == "__main__":
    main()
