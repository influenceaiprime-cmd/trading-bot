import csv, glob, os
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
                row.get("reason",""), row["time"]])
        except (ValueError, KeyError):
            pass

# --- New v5 format: per-symbol message logs ---
for f in glob.glob("trades_log_*.csv"):
    sym = os.path.basename(f).replace("trades_log_","").replace(".csv","")
    for line in open(f):
        # lines like: 2026-.. | BUY ... / SELL ... pnl ...
        parts = line.strip().split("|")
        if len(parts) < 2: continue
        t, msg = parts[0].strip(), "|".join(parts[1:]).strip()
        upper = msg.upper()
        if " BUY" in upper or " SELL" in upper:
            trades.append([sym, "EVENT", 0, 0, None, msg, t])

if not trades:
    print("No trades found in any log. The bot is still waiting patiently. 🧘")
    raise SystemExit

# --- Stats from rows that have pnl data ---
pnls = [(t[0], t[5], t[4]) for t in trades if t[4] is not None]
if pnls:
    wins   = [p for _,_,p in pnls if p > 0]
    losses = [p for _,_,p in pnls if p <= 0]
    print("="*50)
    print("📋 TRADE JOURNAL")
    print("="*50)
    print(f"Closed trades:  {len(pnls)}")
    print(f"Winners:        {len(wins)} ({100*len(wins)/len(pnls):.1f}%)")
    print(f"Losers:         {len(losses)}")
    if wins:   print(f"Avg win:        +{sum(wins)/len(wins):.2f}%")
    if losses: print(f"Avg loss:       {sum(losses)/len(losses):.2f}%")
    print(f"Net P&L:        {sum(p for _,_,p in pnls):+.2f}%")
    by_sym = defaultdict(list)
    for s,_,p in pnls: by_sym[s].append(p)
    print("-"*50)
    for s, ps in by_sym.items():
        print(f"{s}: {len(ps)} trades | win rate "
              f"{100*sum(1 for p in ps if p>0)/len(ps):.0f}% | "
              f"net {sum(ps):+.2f}%")
else:
    print("="*50)
    print(f"📋 {len(trades)} trade events found, none closed yet "
          "(no SELL = no P&L data).")
print("="*50)
