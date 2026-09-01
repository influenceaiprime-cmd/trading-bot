"""
buyhold_baseline.py - the single most important sanity check before tuning
anything else: what would have happened if you'd just bought and held each
symbol for the same period, instead of running the active strategy?

If buy-and-hold beat the strategy, that's a strong signal the strategy's
entries/exits are actively hurting you relative to just being in the
market - which points at the scoring/exit logic, not at needing "more
indicators" or a different stop-loss percentage.

USAGE:
    python buyhold_baseline.py --days 365
"""
import argparse
import pandas as pd
from binance.client import Client

import config
from backtest import fetch_history


def buy_hold_return(client, symbol, interval, capital_per_symbol, days=None, start=None, end=None):
    df = fetch_history(client, symbol, interval, days=days, start=start, end=end)
    if df.empty:
        return None
    start_price = float(df['Close'].iloc[0])
    end_price = float(df['Close'].iloc[-1])
    qty = capital_per_symbol / start_price
    # apply the same fee/slippage assumptions as backtest.py for a fair comparison
    buy_fill = start_price * (1 + config.SLIPPAGE_PCT / 100)
    sell_fill = end_price * (1 - config.SLIPPAGE_PCT / 100)
    buy_fee = buy_fill * qty * (config.TAKER_FEE_PCT / 100)
    sell_fee = sell_fill * qty * (config.TAKER_FEE_PCT / 100)
    cost = buy_fill * qty + buy_fee
    proceeds = sell_fill * qty - sell_fee
    return {
        'symbol': symbol,
        'start_price': start_price,
        'end_price': end_price,
        'return_pct': (proceeds - cost) / cost * 100,
        'start_time': df.index[0],
        'end_time': df.index[-1],
    }


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--days", type=int, default=None, help="last N days (default 365 if --start not given)")
    parser.add_argument("--start", type=str, default=None, help="absolute start date, e.g. 2023-01-01")
    parser.add_argument("--end", type=str, default=None, help="absolute end date, e.g. 2024-03-01")
    parser.add_argument("--capital", type=float, default=config.BACKTEST_START_CAPITAL)
    args = parser.parse_args()

    if not args.start and not args.days:
        args.days = 365

    client = Client()
    capital_per_symbol = args.capital / len(config.PAIRS)
    label = f"{args.start} to {args.end or 'now'}" if args.start else f"last {args.days} days"

    print("=" * 60)
    print(f"BUY & HOLD BASELINE ({label}, ${args.capital:,.0f} split evenly)")
    print("=" * 60)

    results = []
    for sym in config.PAIRS:
        r = buy_hold_return(client, sym, config.INTERVAL, capital_per_symbol, days=args.days, start=args.start, end=args.end)
        if r:
            results.append(r)
            print(f"{r['symbol']}: {r['start_price']:.2f} -> {r['end_price']:.2f} | "
                  f"buy-hold return: {r['return_pct']:+.2f}%")

    if results:
        total_return_pct = sum(r['return_pct'] for r in results) / len(results)
        print("-" * 60)
        print(f"Equal-weight buy-and-hold portfolio return: {total_return_pct:+.2f}%")
        print("=" * 60)
        print("Compare this to backtest.py's 'Total return' for the active strategy")
        print("over the SAME --days window.")
