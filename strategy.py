"""
strategy.py - the actual trading logic (indicators + entry scoring), shared
by bot_engine_v5.py (live) and backtest.py (historical simulation).

Why this file exists: a backtest is only meaningful if it's testing the
EXACT same decision logic that trades your real money. If the live engine
and the backtester each have their own copy of the scoring logic, they will
drift apart the first time either one gets tweaked, and your backtest
results stop meaning anything. Both files import from here instead.
"""
import numpy as np
import pandas as pd

import config


def rsi(s, n=14):
    """RSI that doesn't blow up to NaN/inf when there are zero down-moves in the window."""
    d = s.diff()
    gain = d.clip(lower=0).rolling(n).mean()
    loss = -d.clip(upper=0).rolling(n).mean()
    rs = gain / loss.replace(0, np.nan)
    result = 100 - (100 / (1 + rs))
    result = result.where(loss != 0, 100.0)
    result = result.where(~((loss == 0) & (gain == 0)), 50.0)
    return result


def analyze(df):
    """Adds all indicator columns used by the strategy to a raw OHLCV dataframe."""
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
    hl = d['High'] - d['Low']
    hc = (d['High'] - d['Close'].shift()).abs()
    lc = (d['Low'] - d['Close'].shift()).abs()
    d['ATR'] = pd.concat([hl, hc, lc], axis=1).max(axis=1).rolling(14).mean()
    if 'Volume' in d.columns:
        d['VolAvg20'] = d['Volume'].rolling(20).mean()
    return d


def row_is_ready(row):
    """True once enough history has accumulated for every indicator to be valid (no NaNs)."""
    return not any(pd.isna(row[c]) for c in ('SMA100', 'ATR', 'RSI', 'MACD', 'MACDsig'))


def volatility_multiplier(atr_pct):
    """Scales down position size in high-volatility regimes."""
    if atr_pct < 0.5:
        return 1.0
    if atr_pct < 1.0:
        return 0.7
    if atr_pct < 2.0:
        return 0.5
    return 0.4


def score_entry(row, prev):
    """
    Pure scoring function - no I/O, no account/exchange state, so it can be
    unit tested and reused identically by both the live engine and the
    backtester. Returns (score, reason_string, vol_ok).
    """
    score = 0
    if row['RSI'] < 30:
        score += 1
    elif row['RSI'] > 70:
        score -= 1
    score += 1 if row['SMA20'] > row['SMA50'] else -1
    if row['MACD'] > row['MACDsig'] and prev['MACD'] <= prev['MACDsig']:
        score += 2
    elif row['MACD'] < row['MACDsig'] and prev['MACD'] >= prev['MACDsig']:
        score -= 2
    if row['Close'] < row['BBlo']:
        score += 1
    elif row['Close'] > row['BBhi']:
        score -= 1

    vol_ok = True
    if 'VolAvg20' in row.index and not pd.isna(row.get('VolAvg20', np.nan)):
        vol_ok = row['Volume'] >= row['VolAvg20'] * 0.8
        if row['Volume'] > row['VolAvg20'] * 1.3:
            score += 1

    reason = f"score={score} RSI={row['RSI']:.1f} MACD={'UP' if row['MACD'] > row['MACDsig'] else 'DN'} vol_ok={vol_ok}"
    return score, reason, vol_ok


def passes_regime_filter(row):
    """Only trade with the longer-term trend: price above its 100-period SMA."""
    return row['Close'] > row['SMA100']


def entry_allowed(row, prev):
    """
    All of the SYMBOL-LOCAL entry conditions this module can evaluate on its own
    (score, regime, volume). Does NOT include account-state checks like open
    position count, cooldowns, or balance - those depend on execution context
    and are handled by the caller (live engine or backtester).
    """
    if not row_is_ready(row):
        return False, None
    if not passes_regime_filter(row):
        return False, None
    score, reason, vol_ok = score_entry(row, prev)
    if score < 2 or not vol_ok:
        return False, reason
    return True, reason


def stop_loss_distance_pct(atr_pct):
    """Never risk less than what the ATR says the symbol actually moves."""
    return max(config.SL_PCT, atr_pct * config.ATR_SL_MULT)


def position_size(usdt_balance, price, atr_pct):
    """
    Risk-based position sizing using the REAL account balance (not a hardcoded
    figure) and a stop distance that adapts to volatility (ATR), not a single
    fixed percentage for every symbol regardless of how it actually moves.
    """
    mult = volatility_multiplier(atr_pct)
    risk_amt = usdt_balance * (config.RISK_PCT / 100) * mult
    stop_pct = stop_loss_distance_pct(atr_pct)
    qty = risk_amt / (price * (stop_pct / 100))
    qty = min(qty, (usdt_balance * 0.95) / price)  # never spend more than 95% of free balance
    return qty, mult, stop_pct
