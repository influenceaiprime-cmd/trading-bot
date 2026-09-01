"""
strategy_trend.py - a trend-following alternative to strategy.py.

WHY THIS EXISTS: backtesting strategy.py (mean-reversion: RSI/MACD-cross/
Bollinger scoring) against a real bull run (Jan 2023 - Mar 2024, BTC +271%,
SOL +1227%) showed it LOST -26.8% during that window. The trade-level
breakdown showed why: 529 trades, 61% hit a fixed stop-loss, and even the
wins averaged only +3.2% - capped far short of the moves actually available.
A strategy scored on "overbought/oversold" and "band touches" is, by
construction, betting against continuation - which is exactly what a strong
trend does over and over.

This module flips the philosophy:
  - Fewer, stricter entries: only take a position on a genuine breakout
    (new N-bar high) WITH multi-timeframe SMA alignment (20 > 50 > 100) AND
    MACD confirmation - not on every RSI wiggle.
  - No fixed take-profit at all. The only way out is a wide ATR-based
    trailing stop or the trend itself breaking (price closes back below
    SMA50). This is what "let winners run" actually means in code, instead
    of a name for a philosophy that gets undercut by a small TP in practice.
  - A hard stop still exists for catastrophic protection, but it's wider
    than the mean-reversion version's, because trend trades need more room
    to breathe through normal pullbacks.

This is a genuinely different bet: it will likely produce a LOWER win rate
than strategy.py (most breakouts fail) but should show a much higher average
win size, since it isn't self-capping. Whether that trade-off nets out
positive is exactly what backtest_trend.py exists to check - don't assume
either way, test it.
"""
import numpy as np
import pandas as pd

import config


def analyze(df):
    d = df.copy()
    d['SMA20'] = d['Close'].rolling(20).mean()
    d['SMA50'] = d['Close'].rolling(50).mean()
    d['SMA100'] = d['Close'].rolling(100).mean()
    d['MACD'] = d['Close'].ewm(span=12).mean() - d['Close'].ewm(span=26).mean()
    d['MACDsig'] = d['MACD'].ewm(span=9).mean()
    d['DonchianHigh'] = d['High'].rolling(config.TREND_DONCHIAN_LOOKBACK).max()
    hl = d['High'] - d['Low']
    hc = (d['High'] - d['Close'].shift()).abs()
    lc = (d['Low'] - d['Close'].shift()).abs()
    d['ATR'] = pd.concat([hl, hc, lc], axis=1).max(axis=1).rolling(14).mean()
    if 'Volume' in d.columns:
        d['VolAvg20'] = d['Volume'].rolling(20).mean()
    return d


def row_is_ready(row):
    return not any(pd.isna(row[c]) for c in ('SMA100', 'ATR', 'MACD', 'MACDsig', 'DonchianHigh'))


def get_param(symbol, name):
    """Look up a symbol-specific override, falling back to the global default in config.py."""
    if symbol:
        overrides = config.TREND_SYMBOL_OVERRIDES.get(symbol, {})
        if name in overrides:
            return overrides[name]
    return getattr(config, name)


def entry_allowed(row, prev, symbol=None):
    """
    All four must be true:
      1. Trend alignment: SMA20 > SMA50 > SMA100 (genuinely trending, not chop)
      2. Breakout: current close clears the prior N-bar high by a real margin
         (config.TREND_BREAKOUT_MARGIN_PCT, or a per-symbol override)
      3. MACD confirms upward momentum
      4. Volume supports the move (if volume data is available)
    """
    if not row_is_ready(row) or not row_is_ready(prev):
        return False, None

    trend_aligned = row['SMA20'] > row['SMA50'] > row['SMA100']
    if not trend_aligned:
        return False, None

    # prior high excludes the current bar so this is a real breakout, not tautological.
    # Requires clearing it by a margin, not just barely poking above it - a big share
    # of hard-stops were weak breakouts that cleared the high by a hair and failed.
    margin_pct = get_param(symbol, "TREND_BREAKOUT_MARGIN_PCT")
    breakout_threshold = prev['DonchianHigh'] * (1 + margin_pct / 100)
    breakout = row['Close'] >= breakout_threshold
    if not breakout:
        return False, None

    macd_bullish = row['MACD'] > row['MACDsig']
    if not macd_bullish:
        return False, None

    vol_ok = True
    if 'VolAvg20' in row.index and not pd.isna(row.get('VolAvg20', np.nan)):
        vol_ok = row['Volume'] >= row['VolAvg20'] * 0.9
    if not vol_ok:
        return False, None

    reason = f"breakout @ {row['Close']:.2f} | SMA20>SMA50>SMA100 | MACD_bullish"
    return True, reason


def check_exit(price, position, symbol=None):
    """
    position must contain: entry, atr_entry, peak
    Returns (should_exit: bool, reason: str or None, updated_peak: float)

    No fixed take-profit by design - this is the whole point of the variant.
    trail/hard-stop multipliers respect per-symbol overrides (see
    config.TREND_SYMBOL_OVERRIDES).
    """
    peak = max(position['peak'], price)

    trail_mult = get_param(symbol, "TREND_TRAIL_ATR_MULT")
    hard_mult = get_param(symbol, "TREND_HARD_STOP_ATR_MULT")

    trail_stop = peak - position['atr_entry'] * trail_mult
    hard_stop = position['entry'] - position['atr_entry'] * hard_mult

    if price <= hard_stop:
        return True, "HARD-STOP", peak
    if price <= trail_stop:
        return True, "TRAIL-STOP", peak
    # trend_broken is checked by the caller (needs SMA50 from the current row,
    # which this function doesn't have access to) - see backtest_trend.py
    return False, None, peak


def position_size(usdt_balance, price, atr_pct, symbol=None):
    mult = 1.0 if atr_pct < 1.0 else 0.6 if atr_pct < 2.5 else 0.4
    risk_amt = usdt_balance * (config.RISK_PCT / 100) * mult
    hard_mult = get_param(symbol, "TREND_HARD_STOP_ATR_MULT")
    stop_pct = max(config.SL_PCT, atr_pct * hard_mult)
    qty = risk_amt / (price * (stop_pct / 100))
    qty = min(qty, (usdt_balance * 0.95) / price)
    return qty, mult, stop_pct
