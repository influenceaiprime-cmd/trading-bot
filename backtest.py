"""
backtest.py - simulate the strategy in strategy.py against historical data.

This is the missing piece that makes "sell it for recurring revenue" a
legitimate question to even ask. Run this BEFORE trusting the strategy with
anyone's money - including your own.

USAGE:
    python backtest.py                      # backtest config.PAIRS over the default lookback
    python backtest.py --days 365            # override lookback window
    python backtest.py --symbol BTCUSDT      # single symbol only

WHAT IT DOES:
    - Pulls historical klines from Binance (public endpoint, no API key needed)
    - Runs the exact entry/exit logic from strategy.py bar-by-bar, in order,
      never looking ahead
    - Simulates a shared USDT balance across all symbols, same position
      sizing, same cooldowns, same max open positions as the live engine
    - Applies a trading fee and slippage assumption on every fill (config.py)
      so the results aren't a fantasy of free, instant, perfect execution
    - Reports total return, max drawdown, win rate, Sharpe ratio, and a
      per-symbol breakdown
    - Writes backtest_equity_curve.csv and backtest_trades.csv for further
      analysis (e.g. plotting, or checking in dashboard.py)

LIMITATIONS (read this before trusting the output):
    - Higher-timeframe confirmation (htf.py) is NOT simulated here to keep
      this dependency-light; add it if you want a fully faithful backtest.
    - No partial fills, no exchange downtime, no funding costs (spot only).
    - Past performance on historical data is not a guarantee of future
      performance - regimes change. Walk-forward / out-of-sample testing
      is strongly recommended before trusting this further.
"""
import argparse
import math
from datetime import datetime

import numpy as np
import pandas as pd
from binance.client import Client

import config
import strategy


def fetch_history(client, symbol, interval, days=None, start=None, end=None):
    """
    Two ways to specify the window:
      - days: relative, e.g. days=365 means "365 days ago UTC" through now
      - start/end: absolute dates as strings, e.g. "2023-01-01", "2024-03-01",
        used to test a SPECIFIC historical period (like a past bull run)
        instead of only whatever the last N days happen to be.
    """
    if start:
        start_str = start
        end_str = end  # None means "through now"
        klines = client.get_historical_klines(symbol, interval, start_str, end_str)
    else:
        start_str = f"{days} days ago UTC"
        klines = client.get_historical_klines(symbol, interval, start_str)
    df = pd.DataFrame(klines, columns=[
        'OpenTime', 'Open', 'High', 'Low', 'Close', 'Volume',
        'CloseTime', 'QuoteAssetVolume', 'NumberOfTrades',
        'TakerBuyBaseAssetVolume', 'TakerBuyQuoteAssetVolume', 'Ignore'
    ])
    for col in ['Open', 'High', 'Low', 'Close', 'Volume']:
        df[col] = pd.to_numeric(df[col])
    df['OpenTime'] = pd.to_datetime(df['OpenTime'], unit='ms')
    df.set_index('OpenTime', inplace=True)
    return df


class Portfolio:
    """Tracks shared cash + open positions across symbols, bar by bar."""

    def __init__(self, start_capital):
        self.cash = start_capital
        self.positions = {}   # symbol -> {entry, qty, peak, atr_sl_price, atr_tp_price}
        self.cooldowns = {}   # symbol -> timestamp until which re-entry is blocked
        self.trades = []      # closed trade records
        self.equity_curve = []  # (timestamp, equity)

    def equity(self, last_prices):
        crypto_val = sum(p['qty'] * last_prices.get(sym, p['entry']) for sym, p in self.positions.items())
        return self.cash + crypto_val

    def open_position(self, symbol, ts, price, qty, atr):
        fee = price * qty * (config.TAKER_FEE_PCT / 100)
        fill_price = price * (1 + config.SLIPPAGE_PCT / 100)  # buying: slippage costs you more
        cost = fill_price * qty + fee
        if cost > self.cash:
            return False
        self.cash -= cost
        self.positions[symbol] = {
            'entry': fill_price,
            'qty': qty,
            'peak': fill_price,
            'atr_sl_price': fill_price - atr * config.ATR_SL_MULT,
            'atr_tp_price': fill_price + atr * config.ATR_TP_MULT,
            'open_time': ts,
        }
        return True

    def close_position(self, symbol, ts, price, reason):
        p = self.positions.pop(symbol)
        fill_price = price * (1 - config.SLIPPAGE_PCT / 100)  # selling: slippage costs you more
        proceeds = fill_price * p['qty']
        fee = proceeds * (config.TAKER_FEE_PCT / 100)
        proceeds -= fee
        self.cash += proceeds
        pnl_pct = (fill_price - p['entry']) / p['entry'] * 100
        self.trades.append({
            'symbol': symbol, 'entry_time': p['open_time'], 'exit_time': ts,
            'entry_price': p['entry'], 'exit_price': fill_price,
            'pnl_pct': pnl_pct, 'reason': reason,
        })
        self.cooldowns[symbol] = ts + pd.Timedelta(minutes=config.COOLDOWN_MIN)


def run_backtest(symbols, start_capital, days=None, start=None, end=None):
    client = Client()  # public endpoints only, no keys needed for historical klines
    data = {}
    for sym in symbols:
        label = f"{start} to {end or 'now'}" if start else f"last {days}d"
        print(f"Fetching {label} of {config.INTERVAL} history for {sym}...")
        raw = fetch_history(client, sym, config.INTERVAL, days=days, start=start, end=end)
        data[sym] = strategy.analyze(raw)

    # Align all symbols on a shared timeline so the portfolio-level position
    # cap and shared cash balance behave like the live engine.
    common_index = None
    for df in data.values():
        common_index = df.index if common_index is None else common_index.union(df.index)
    common_index = common_index.sort_values()

    pf = Portfolio(start_capital)
    last_prices = {}

    for ts in common_index:
        for sym in symbols:
            df = data[sym]
            if ts not in df.index:
                continue
            loc = df.index.get_loc(ts)
            if loc < 1:
                continue
            row, prev = df.iloc[loc], df.iloc[loc - 1]
            price = float(row['Close'])
            last_prices[sym] = price

            # ---- exits ----
            pos = pf.positions.get(sym)
            if pos:
                pnl = (price - pos['entry']) / pos['entry'] * 100
                peak = max(pos['peak'], price)
                pos['peak'] = peak
                trail_price = peak * (1 - config.TRAIL_GAP / 100)
                armed = (peak / pos['entry'] - 1) * 100 >= config.TRAIL_ARM

                reason = None
                if pnl <= -config.SL_PCT or price <= pos['atr_sl_price']:
                    reason = "STOP-LOSS"
                elif armed and price <= trail_price:
                    reason = "TRAILED-PROFIT"
                elif price >= pos['atr_tp_price']:
                    reason = "TAKE-PROFIT"
                if reason:
                    pf.close_position(sym, ts, price, reason)
                continue  # don't also try to enter the same bar we just exited

            # ---- entries ----
            if sym in pf.cooldowns and ts < pf.cooldowns[sym]:
                continue
            if len(pf.positions) >= config.MAX_OPEN_POSITIONS:
                continue
            ok, reason = strategy.entry_allowed(row, prev)
            if not ok:
                continue
            atr_pct = row['ATR'] / price * 100 if not np.isnan(row['ATR']) else 1.0
            qty, mult, stop_pct = strategy.position_size(pf.cash, price, atr_pct)
            min_notional = config.MIN_TRADE_USDT
            if qty <= 0 or qty * price < min_notional:
                continue
            pf.open_position(sym, ts, price, qty, row['ATR'])

        pf.equity_curve.append((ts, pf.equity(last_prices)))

    return pf


def summarize(pf, start_capital):
    equity_df = pd.DataFrame(pf.equity_curve, columns=['time', 'equity']).drop_duplicates('time')
    equity_df.to_csv("backtest_equity_curve.csv", index=False)

    trades_df = pd.DataFrame(pf.trades)
    trades_df.to_csv("backtest_trades.csv", index=False)

    final_equity = equity_df['equity'].iloc[-1] if len(equity_df) else start_capital
    total_return_pct = (final_equity - start_capital) / start_capital * 100

    running_max = equity_df['equity'].cummax()
    drawdown = (equity_df['equity'] - running_max) / running_max * 100
    max_drawdown_pct = drawdown.min() if len(drawdown) else 0.0

    # Approximate annualized Sharpe from per-bar equity returns (assumes 1h bars, ~8760/yr)
    returns = equity_df['equity'].pct_change().dropna()
    if len(returns) > 1 and returns.std() > 0:
        bars_per_year = 8760 if config.INTERVAL == "1h" else 365
        sharpe = (returns.mean() / returns.std()) * math.sqrt(bars_per_year)
    else:
        sharpe = float('nan')

    print("\n" + "=" * 60)
    print("BACKTEST RESULTS")
    print("=" * 60)
    print(f"Start capital:     ${start_capital:,.2f}")
    print(f"Final equity:      ${final_equity:,.2f}")
    print(f"Total return:      {total_return_pct:+.2f}%")
    print(f"Max drawdown:      {max_drawdown_pct:.2f}%")
    print(f"Sharpe (approx):   {sharpe:.2f}")
    print(f"Total trades:      {len(trades_df)}")

    if len(trades_df):
        wins = trades_df[trades_df['pnl_pct'] > 0]
        losses = trades_df[trades_df['pnl_pct'] <= 0]
        win_rate = len(wins) / len(trades_df) * 100
        print(f"Win rate:          {win_rate:.1f}% ({len(wins)}/{len(trades_df)})")
        if len(wins):
            print(f"Avg win:           +{wins['pnl_pct'].mean():.2f}%")
        if len(losses):
            print(f"Avg loss:          {losses['pnl_pct'].mean():.2f}%")
        print("-" * 60)
        for sym, grp in trades_df.groupby('symbol'):
            sym_win_rate = (grp['pnl_pct'] > 0).mean() * 100
            print(f"{sym}: {len(grp)} trades | win rate {sym_win_rate:.0f}% | "
                  f"net {grp['pnl_pct'].sum():+.2f}% (sum of per-trade %, not compounded)")
    print("=" * 60)
    print("Full equity curve -> backtest_equity_curve.csv")
    print("Full trade list    -> backtest_trades.csv")


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--days", type=int, default=None,
                         help="test the last N days (default 365 if --start not given)")
    parser.add_argument("--start", type=str, default=None,
                         help="absolute start date, e.g. 2023-01-01 - tests a SPECIFIC historical window instead of 'last N days'")
    parser.add_argument("--end", type=str, default=None,
                         help="absolute end date, e.g. 2024-03-01 (omit to run through now)")
    parser.add_argument("--symbol", type=str, default=None, help="test a single symbol instead of config.PAIRS")
    parser.add_argument("--capital", type=float, default=config.BACKTEST_START_CAPITAL)
    args = parser.parse_args()

    if not args.start and not args.days:
        args.days = 365  # default window when nothing specified

    symbols = [args.symbol] if args.symbol else config.PAIRS
    pf = run_backtest(symbols, args.capital, days=args.days, start=args.start, end=args.end)
    summarize(pf, args.capital)
