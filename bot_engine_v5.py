import sys
"""
BOT ENGINE v5 - LIVE EXECUTION + MEMORY + ALERTS
v4 + persistent state (survives restarts) + equity log + dashboard + Telegram
"""
import os, time, json
from datetime import datetime
import pandas as pd
import numpy as np
from dotenv import load_dotenv
from binance.client import Client
from binance_data import get_klines_df

load_dotenv()
API_KEY = os.getenv("BINANCE_API_KEY")
API_SECRET = os.getenv("BINANCE_API_SECRET")
TG_TOKEN = os.getenv("TELEGRAM_TOKEN")       # optional
TG_CHAT = os.getenv("TELEGRAM_CHAT_ID")      # optional
client = Client(API_KEY, API_SECRET, testnet=True)

from htf import htf_uptrend
PAIRS = ["BTCUSDT", "ETHUSDT", "SOLUSDT"]
INTERVAL = "1h"
SL_PCT, TP_PCT, RISK_PCT = 2.0, 4.0, 2.0
COOLDOWN_MIN = 180
STATE_FILE = "bot_state.json"
EQUITY_FILE = "equity_log.csv"

# ---------- STATE PERSISTENCE ----------
def load_state():
    if os.path.exists(STATE_FILE):
        with open(STATE_FILE) as f:
            return json.load(f)
    return {"positions": {}, "cooldowns": {}, "last_prices": {}}

def save_state():
    with open(STATE_FILE, "w") as f:
        json.dump(STATE, f, indent=2)

STATE = load_state()

# ---------- DISCORD ALERTS ----------
def notify(msg):
    print(f"  [ALERT] {msg}")
    hook = os.getenv("DISCORD_WEBHOOK")
    if hook:
        try:
            import requests
            requests.post(hook, json={"content": "🤖 " + msg}, timeout=15)
        except Exception as e:
            print(f"  [discord failed: {e}]")

# ---------- INDICATORS ----------
def rsi(s, n=14):
    d = s.diff()
    return 100 - (100 / (1 + d.clip(lower=0).rolling(n).mean() / -d.clip(upper=0).rolling(n).mean()))

def analyze(df):
    d = df.copy()
    d['RSI'] = rsi(d['Close'])
    d['SMA20'] = d['Close'].rolling(20).mean()
    d['SMA50'] = d['Close'].rolling(50).mean()
    d['SMA100'] = d['Close'].rolling(100).mean()
    d['MACD'] = d['Close'].ewm(span=12).mean() - d['Close'].ewm(span=26).mean()
    d['MACDsig'] = d['MACD'].ewm(span=9).mean()
    std = d['Close'].rolling(20).std()
    d['BBlo'] = d['SMA20'] - 2 * std
    d['BBhi'] = d['SMA20'] + 2 * std
    hl, hc, lc = d['High']-d['Low'], (d['High']-d['Close'].shift()).abs(), (d['Low']-d['Close'].shift()).abs()
    d['ATR'] = pd.concat([hl, hc, lc], axis=1).max(axis=1).rolling(14).mean()
    return d

def log(symbol, msg):
    line = f"{datetime.now():%Y-%m-%d %H:%M:%S} | {msg}"
    print(line)
    with open(f"trades_log_{symbol.replace('USDT','')}.csv", "a") as f:
        f.write(line.replace(" | ", ",") + "\n")

def get_position(symbol):
    bal = float(client.get_asset_balance(asset=symbol.replace("USDT", ""))['free'])
    return bal

# ---------- EXITS (with trailing stop) ----------
TRAIL_ARM, TRAIL_GAP = 4.0, 1.5   # arm at +4%, exit 1.5% below peak
def check_exit(symbol, price):
    p = STATE["positions"].get(symbol)
    if not p:
        return
    entry = p['entry']
    peak = p.get('peak', entry)
    if price > peak:
        peak = price
        p['peak'] = peak
        save_state()
    pnl = (price - entry) / entry * 100
    trail_price = peak * (1 - TRAIL_GAP/100)
    armed = (peak/entry - 1) * 100 >= TRAIL_ARM
    if pnl <= -SL_PCT:
        qty = get_position(symbol)
        if qty * price > 5:
            client.order_market_sell(symbol=symbol, quantity=round(qty, 5))
            log(symbol, f"SELL(exit) STOP-LOSS {pnl:.2f}% @ {price}")
            notify(f"🛑 {symbol} STOP-LOSS {pnl:+.2f}% @ ${price:,.2f}")
        STATE["positions"][symbol] = None
        STATE["cooldowns"][symbol] = time.time() + COOLDOWN_MIN * 60
        save_state()
    elif armed and price <= trail_price:
        qty = get_position(symbol)
        if qty * price > 5:
            client.order_market_sell(symbol=symbol, quantity=round(qty, 5))
            log(symbol, f"SELL(exit) TRAILED-PROFIT {pnl:.2f}% @ {price} (peak {peak:.2f})")
            notify(f"✅ {symbol} TAKE-PROFIT {pnl:+.2f}% @ ${price:,.2f}")
        STATE["positions"][symbol] = None
        STATE["cooldowns"][symbol] = time.time() + COOLDOWN_MIN * 60
        save_state()

# ---------- ENTRIES ----------
def check_entry(symbol, df):
    if STATE["positions"].get(symbol):
        return
    if time.time() < STATE["cooldowns"].get(symbol, 0):
        return
    row, prev = df.iloc[-1], df.iloc[-2]
    price = float(row['Close'])
    usdt = float(client.get_asset_balance(asset="USDT")['free'])

    if price <= row['SMA100']:   # regime filter
        return
    atr_pct = row['ATR'] / price * 100 if not np.isnan(row['ATR']) else 1.0
    mult = 1.0 if atr_pct < 0.5 else 0.7 if atr_pct < 1.0 else 0.5 if atr_pct < 2.0 else 0.4
    score = 0
    if row['RSI'] < 30: score += 1
    elif row['RSI'] > 70: score -= 1
    score += 1 if row['SMA20'] > row['SMA50'] else -1
    if row['MACD'] > row['MACDsig'] and prev['MACD'] <= prev['MACDsig']: score += 2
    elif row['MACD'] < row['MACDsig'] and prev['MACD'] >= prev['MACDsig']: score -= 2
    if price < row['BBlo']: score += 1
    elif price > row['BBhi']: score -= 1

    reason = f"score={score} RSI={row['RSI']:.1f} MACD={'UP' if row['MACD']>row['MACDsig'] else 'DN'}"
    if score >= 2 and usdt > 10 and htf_uptrend(client, symbol) and len(STATE["positions"]) < 3:
        risk_amt = 10000.0 * (RISK_PCT / 100) * mult
        qty = risk_amt / (price * (SL_PCT / 100))
        qty = min(qty, (usdt * 0.95) / price)
        qty = float(f"{qty:.5f}")
        if qty * price < 10:
            return
        try:
            client.order_market_buy(symbol=symbol, quantity=qty)
            STATE["positions"][symbol] = {'entry': price, 'qty': qty, 'time': datetime.now().isoformat(), 'peak': price}
            save_state()
            log(symbol, f"BUY {qty} @ {price} | {reason} | vol_mult={mult}")
            notify(f"🟢 BUY {symbol}: {qty} @ ${price:,.2f} | {reason}")
        except Exception as e:
            log(symbol, f"BUY FAILED: {e}")

# ---------- EQUITY TRACKING ----------
def log_equity():
    usdt = float(client.get_asset_balance(asset="USDT")['free'])
    crypto_val = 0.0
    for s in PAIRS:
        qty = get_position(s)
        if qty > 0 and s in STATE["last_prices"]:
            crypto_val += qty * STATE["last_prices"][s]
    total = usdt + crypto_val
    new_file = not os.path.exists(EQUITY_FILE)
    with open(EQUITY_FILE, "a") as f:
        if new_file:
            f.write("time,usdt,crypto_value,total\n")
        f.write(f"{datetime.now():%Y-%m-%d %H:%M},{usdt:.2f},{crypto_val:.2f},{total:.2f}\n")
    return total

# ---------- MAIN LOOP ----------
print("="*60)
print("  BOT ENGINE v5 - LIVE TESTNET + MEMORY + ALERTS")
print("  State file:", STATE_FILE, "| Ctrl+C to stop")
print("="*60)

ONE_SHOT = "--once" in sys.argv
try:
    while not ONE_SHOT:
        print(f"\n--- Cycle {datetime.now():%H:%M:%S} ---")
        for symbol in PAIRS:
            try:
                df = get_klines_df(API_KEY, API_SECRET, symbol, INTERVAL, 200)
                d = analyze(df)
                price = float(d['Close'].iloc[-1])
                STATE["last_prices"][symbol] = price
                pos = STATE["positions"].get(symbol)
                if pos:
                    pnl = (price - pos['entry']) / pos['entry'] * 100
                    status = f"IN pos {pos['qty']} from {pos['entry']:.2f} (pnl {pnl:+.2f}%)"
                else:
                    cd_left = int((STATE["cooldowns"].get(symbol, 0) - time.time()) / 60)
                    status = f"flat" + (f" | cooldown {cd_left}m left" if cd_left > 0 else "")
                print(f"{symbol}: ${price:,.2f} | {status}")
                check_exit(symbol, price)
                check_entry(symbol, d)
            except Exception as e:
                print(f"{symbol}: ERROR {e}")
        save_state()
        total = log_equity()
        print(f"TOTAL EQUITY: ${total:,.2f} (logged to {EQUITY_FILE})")
        time.sleep(300)
        if ONE_SHOT:
            break
except KeyboardInterrupt:
    save_state()
    print("\nEngine stopped SAFELY - state saved, positions remembered for next run.")
