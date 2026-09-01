"""
dashboard.py - generates a single static HTML report from whatever data
exists in the current directory: live equity_log.csv / trades_log_*.csv,
and/or backtest_equity_curve.csv / backtest_trades.csv from backtest.py.

USAGE:
    python dashboard.py                # reads live logs, writes dashboard.html
    python dashboard.py --backtest      # reads backtest_* files instead

No web server, no dependencies beyond matplotlib/pandas - open dashboard.html
in any browser.
"""
import argparse
import glob
import os
import re
from collections import defaultdict

import pandas as pd
import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt

PNL_RE = re.compile(r'([+-]?\d+(?:\.\d+)?)%')


def load_live_trades():
    trades = []
    for f in glob.glob("trades_log_*.csv"):
        sym = os.path.basename(f).replace("trades_log_", "").replace(".csv", "")
        try:
            df = pd.read_csv(f)
        except Exception:
            continue
        if list(df.columns) != ["time", "message"]:
            continue
        for _, row in df.iterrows():
            msg = str(row["message"])
            if "SELL" not in msg.upper():
                continue
            m = PNL_RE.search(msg)
            if m:
                trades.append({"symbol": sym, "pnl_pct": float(m.group(1)), "time": row["time"], "detail": msg})
    return pd.DataFrame(trades)


def load_live_equity():
    if not os.path.exists("equity_log.csv"):
        return pd.DataFrame()
    return pd.read_csv("equity_log.csv")


def load_backtest_trades():
    if not os.path.exists("backtest_trades.csv"):
        return pd.DataFrame()
    return pd.read_csv("backtest_trades.csv")


def load_backtest_equity():
    if not os.path.exists("backtest_equity_curve.csv"):
        return pd.DataFrame()
    return pd.read_csv("backtest_equity_curve.csv")


def make_equity_chart(equity_df, time_col, value_col, out_path):
    if equity_df.empty:
        return False
    plt.figure(figsize=(9, 4))
    plt.plot(pd.to_datetime(equity_df[time_col]), equity_df[value_col], linewidth=1.5)
    plt.title("Equity Curve")
    plt.xlabel("Time")
    plt.ylabel("Equity ($)")
    plt.grid(alpha=0.3)
    plt.tight_layout()
    plt.savefig(out_path, dpi=110)
    plt.close()
    return True


def build_stats(trades_df):
    if trades_df.empty or "pnl_pct" not in trades_df.columns:
        return None
    wins = trades_df[trades_df["pnl_pct"] > 0]
    losses = trades_df[trades_df["pnl_pct"] <= 0]
    stats = {
        "total_trades": len(trades_df),
        "win_rate": 100 * len(wins) / len(trades_df) if len(trades_df) else 0,
        "avg_win": wins["pnl_pct"].mean() if len(wins) else 0,
        "avg_loss": losses["pnl_pct"].mean() if len(losses) else 0,
        "net_pnl": trades_df["pnl_pct"].sum(),
        "by_symbol": trades_df.groupby("symbol")["pnl_pct"].agg(["count", "sum", "mean"]).to_dict("index"),
    }
    return stats


def render_html(stats, chart_exists, title):
    rows = ""
    if stats:
        for sym, s in stats["by_symbol"].items():
            rows += f"<tr><td>{sym}</td><td>{s['count']}</td><td>{s['sum']:+.2f}%</td><td>{s['mean']:+.2f}%</td></tr>"

    stats_block = ""
    if stats:
        stats_block = f"""
        <div class="cards">
          <div class="card"><div class="label">Total Trades</div><div class="value">{stats['total_trades']}</div></div>
          <div class="card"><div class="label">Win Rate</div><div class="value">{stats['win_rate']:.1f}%</div></div>
          <div class="card"><div class="label">Avg Win</div><div class="value pos">+{stats['avg_win']:.2f}%</div></div>
          <div class="card"><div class="label">Avg Loss</div><div class="value neg">{stats['avg_loss']:.2f}%</div></div>
          <div class="card"><div class="label">Net P&amp;L (sum)</div><div class="value">{stats['net_pnl']:+.2f}%</div></div>
        </div>
        <table>
          <tr><th>Symbol</th><th>Trades</th><th>Net %</th><th>Avg %</th></tr>
          {rows}
        </table>
        """
    else:
        stats_block = "<p class='muted'>No closed trades found yet.</p>"

    chart_block = "<img src='equity_chart.png' style='max-width:100%'>" if chart_exists else "<p class='muted'>No equity data found yet.</p>"

    return f"""<!DOCTYPE html>
<html><head><meta charset="utf-8"><title>{title}</title>
<style>
body {{ font-family: -apple-system, Segoe UI, Arial, sans-serif; background:#0f1115; color:#e6e6e6; margin:0; padding:32px; }}
h1 {{ font-weight:600; }}
.muted {{ color:#888; }}
.cards {{ display:flex; gap:16px; flex-wrap:wrap; margin:24px 0; }}
.card {{ background:#1a1d24; border-radius:10px; padding:16px 20px; min-width:140px; }}
.label {{ font-size:12px; color:#999; text-transform:uppercase; letter-spacing:0.5px; }}
.value {{ font-size:24px; font-weight:600; margin-top:4px; }}
.pos {{ color:#4ade80; }} .neg {{ color:#f87171; }}
table {{ border-collapse: collapse; width:100%; margin-top:16px; }}
th, td {{ text-align:left; padding:8px 12px; border-bottom:1px solid #2a2d34; }}
th {{ color:#999; font-weight:500; font-size:13px; text-transform:uppercase; }}
.disclaimer {{ margin-top:40px; font-size:12px; color:#666; border-top:1px solid #2a2d34; padding-top:16px; }}
</style></head>
<body>
<h1>{title}</h1>
{chart_block}
{stats_block}
<div class="disclaimer">
Generated by dashboard.py. Backtest results do not guarantee future performance.
Live results reflect testnet unless the engine has been switched to mainnet.
</div>
</body></html>"""


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--backtest", action="store_true", help="read backtest_* files instead of live logs")
    args = parser.parse_args()

    if args.backtest:
        trades_df = load_backtest_trades()
        equity_df = load_backtest_equity()
        chart_exists = make_equity_chart(equity_df, "time", "equity", "equity_chart.png")
        title = "Backtest Report"
    else:
        trades_df = load_live_trades()
        equity_df = load_live_equity()
        chart_exists = make_equity_chart(equity_df, "time", "total", "equity_chart.png")
        title = "Live Trading Dashboard"

    stats = build_stats(trades_df)
    html = render_html(stats, chart_exists, title)
    with open("dashboard.html", "w") as f:
        f.write(html)
    print("Wrote dashboard.html" + (" and equity_chart.png" if chart_exists else " (no equity chart - no data yet)"))
