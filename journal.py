import csv
import glob
import os
import re
from collections import defaultdict

trades = []  # (symbol, action, qty, price, pnl%, reason, time)

# --- Old format: clean CSV (time,action,qty,price,pnl_%,reason) ---
for f in glob.glob("trades_log.csv"):
    sym = "OLD-MIXED"
    for row in csv.DictReader(open(f)):
        try:
            trades.append([sym, row["action"], float(row["qty"]),
                float(row["price"]),
                float(row["pnl_%"]) if row["pnl_%"] else None,
                row.get("reason", ""), row["time"]])
        except (ValueError, KeyError):
            pass

# --- v5 format: per-symbol CSV logs with columns (time, message) ---
# Example messages written by bot_engine_v5.py's log():
#   "BUY 0.01 @ 50000 | score=2 RSI=25.3 MACD=UP | vol_mult=1.0 | ATR_SL=49000.00 ATR_TP=52000.00"
#   "SELL(exit) STOP-LOSS -1.98% @ 49000 (peak 50200.00)"
#   "SELL(exit) TRAILED-PROFIT +4.32% @ 52100 (peak 52900.00)"
PNL_RE = re.compile(r'([+-]?\d+(?:\.\d+)?)%')

for f in glob.glob("trades_log_*.csv"):
    sym = os.path.basename(f).replace("trades_log_", "").replace(".csv", "")
    with open(f, newline="") as fh:
        reader = csv.DictReader(fh)
        # Guard against files written by very old engine versions with a
        # different header, or files with no header at all.
        if reader.fieldnames != ["time", "message"]:
            continue
        for row in reader:
            t = row.get("time", "")
            msg = row.get("message", "")
            if not msg:
                continue
            upper = msg.upper()
            if " BUY" not in upper and " SELL" not in upper:
                continue
            pnl = None
            if "SELL" in upper:
                m = PNL_RE.search(msg)
                if m:
                    pnl = float(m.group(1))
            trades.append([sym, "EVENT", 0, 0, pnl, msg, t])

if not trades:
    print("No trades found in any log. The bot is still waiting patiently. \U0001F9D8")
    raise SystemExit

# --- Stats from rows that have pnl data (i.e. closed trades) ---
pnls = [(t[0], t[5], t[4]) for t in trades if t[4] is not None]
if pnls:
    wins = [p for _, _, p in pnls if p > 0]
    losses = [p for _, _, p in pnls if p <= 0]
    print("=" * 50)
    print("\U0001F4CB TRADE JOURNAL")
    print("=" * 50)
    print(f"Closed trades:  {len(pnls)}")
    print(f"Winners:        {len(wins)} ({100 * len(wins) / len(pnls):.1f}%)")
    print(f"Losers:         {len(losses)}")
    if wins:
        print(f"Avg win:        +{sum(wins) / len(wins):.2f}%")
    if losses:
        print(f"Avg loss:       {sum(losses) / len(losses):.2f}%")
    print(f"Net P&L:        {sum(p for _, _, p in pnls):+.2f}%")
    by_sym = defaultdict(list)
    for s, _, p in pnls:
        by_sym[s].append(p)
    print("-" * 50)
    for s, ps in by_sym.items():
        print(f"{s}: {len(ps)} trades | win rate "
              f"{100 * sum(1 for p in ps if p > 0) / len(ps):.0f}% | "
              f"net {sum(ps):+.2f}%")
else:
    print("=" * 50)
    print(f"\U0001F4CB {len(trades)} trade events found, none closed yet "
          "(no SELL = no P&L data).")
print("=" * 50)
