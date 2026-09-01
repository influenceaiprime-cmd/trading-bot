"""
backtest_trend.py - backtests strategy_trend.py using the same historical
data fetching, fee/slippage assumptions, and portfolio simulation approach
as backtest.py, so the two are a fair apples-to-apples comparison.

USAGE:
    python backtest_trend.py --start 2023-01-01 --end 2024-03-01
    python backtest_trend.py --days 365
"""
import argparse
import math

import numpy as np
import pandas as pd
from binance.client import Client

import config
import strategy_trend as strategy
from backtest import fetch_history  # reuse the exact same data-fetching logic


class Portfolio:
    def __init__(self, start_capital):
        self.cash = start_capital
        self.positions = {}
        self.cooldowns = {}
        self.trades = []
        self.equity_curve = []

    def equity(self, last_prices):
        crypto_val = sum(p['qty'] * last_prices.get(sym, p['entry']) for sym, p in self.positions.items())
        return self.cash + crypto_val

    def open_position(self, symbol, ts, price, qty, atr):
        fee = price * qty * (config.TAKER_FEE_PCT / 100)
        fill_price = price * (1 + config.SLIPPAGE_PCT / 100)
        cost = fill_price * qty + fee
        if cost > self.cash:
            return False
        self.cash -= cost
        self.positions[symbol] = {
            'entry': fill_price, 'qty': qty, 'peak': fill_price,
            'atr_entry': atr, 'open_time': ts,
        }
        return True

    def close_position(self, symbol, ts, price, reason):
        p = self.positions.pop(symbol)
        fill_price = price * (1 - config.SLIPPAGE_PCT / 100)
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
    client = Client()
    data = {}
    for sym in symbols:
        label = f"{start} to {end or 'now'}" if start else f"last {days}d"
        print(f"Fetching {label} of {config.INTERVAL} history for {sym}...")
        raw = fetch_history(client, sym, config.INTERVAL, days=days, start=start, end=end)
        data[sym] = strategy.analyze(raw)

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

            pos = pf.positions.get(sym)
            if pos:
                should_exit, reason, peak = strategy.check_exit(price, pos, symbol=sym)
                pos['peak'] = peak
                # trend-broken exit needs SMA50 from the live row, checked here
                # rather than inside strategy.check_exit (which only sees price)
                if not should_exit and price < row['SMA50']:
                    should_exit, reason = True, "TREND-BROKEN"
                if should_exit:
                    pf.close_position(sym, ts, price, reason)
                continue

            if sym in pf.cooldowns and ts < pf.cooldowns[sym]:
                continue
            if len(pf.positions) >= config.MAX_OPEN_POSITIONS:
                continue
            ok, reason = strategy.entry_allowed(row, prev, symbol=sym)
            if not ok:
                continue
            atr_pct = row['ATR'] / price * 100 if not np.isnan(row['ATR']) else 1.0
            qty, mult, stop_pct = strategy.position_size(pf.cash, price, atr_pct, symbol=sym)
            if qty <= 0 or qty * price < config.MIN_TRADE_USDT:
                continue
            pf.open_position(sym, ts, price, qty, row['ATR'])

        pf.equity_curve.append((ts, pf.equity(last_prices)))

    return pf


def summarize(pf, start_capital):
    equity_df = pd.DataFrame(pf.equity_curve, columns=['time', 'equity']).drop_duplicates('time')
    equity_df.to_csv("backtest_trend_equity_curve.csv", index=False)

    trades_df = pd.DataFrame(pf.trades)
    trades_df.to_csv("backtest_trend_trades.csv", index=False)

    final_equity = equity_df['equity'].iloc[-1] if len(equity_df) else start_capital
    total_return_pct = (final_equity - start_capital) / start_capital * 100

    running_max = equity_df['equity'].cummax()
    drawdown = (equity_df['equity'] - running_max) / running_max * 100
    max_drawdown_pct = drawdown.min() if len(drawdown) else 0.0

    returns = equity_df['equity'].pct_change().dropna()
    if len(returns) > 1 and returns.std() > 0:
        bars_per_year = 8760 if config.INTERVAL == "1h" else 365
        sharpe = (returns.mean() / returns.std()) * math.sqrt(bars_per_year)
    else:
        sharpe = float('nan')

    print("\n" + "=" * 60)
    print("TREND-FOLLOWING BACKTEST RESULTS")
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
        print("-" * 60)
        print("Exit reason breakdown:")
        print(trades_df.groupby('reason')['pnl_pct'].agg(['count', 'mean', 'sum']))
    print("=" * 60)
    print("Full equity curve -> backtest_trend_equity_curve.csv")
    print("Full trade list    -> backtest_trend_trades.csv")


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--days", type=int, default=None)
    parser.add_argument("--start", type=str, default=None)
    parser.add_argument("--end", type=str, default=None)
    parser.add_argument("--symbol", type=str, default=None)
    parser.add_argument("--live-pairs-only", action="store_true",
                         help="test only config.LIVE_PAIRS (excludes ETH by default) instead of the full config.PAIRS universe")
    parser.add_argument("--capital", type=float, default=config.BACKTEST_START_CAPITAL)
    args = parser.parse_args()

    if not args.start and not args.days:
        args.days = 365

    if args.symbol:
        symbols = [args.symbol]
    elif args.live_pairs_only:
        symbols = config.LIVE_PAIRS
    else:
        symbols = config.PAIRS
    pf = run_backtest(symbols, args.capital, days=args.days, start=args.start, end=args.end)
    summarize(pf, args.capital)
