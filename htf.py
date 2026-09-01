import config


def htf_uptrend(client, symbol, interval=None, lookback=None):
    """
    Higher-timeframe trend filter.

    BUG FIXED: this used to hardcode interval="1h", which is the SAME
    timeframe the bot trades on (bot_engine_v5.py's INTERVAL is also "1h").
    That meant this "higher timeframe" filter was re-checking the exact same
    candles the entry signal already used - it added almost no independent
    confirmation. Now it defaults to config.HTF_INTERVAL ("4h"), a genuinely
    longer timeframe, so it actually filters out entries that look good on
    the 1h chart but are against the broader trend.

    Also fails CLOSED (returns False / vetoes the trade) on any error,
    instead of the old behavior of failing open (returning True), which
    meant a broken API call would silently let trades through with zero
    trend confirmation at all.
    """
    interval = interval or config.HTF_INTERVAL
    lookback = lookback or config.HTF_LOOKBACK
    try:
        k = client.get_klines(symbol=symbol, interval=interval, limit=lookback)
        closes = [float(r[4]) for r in k]
        if len(closes) < lookback:
            print(f"  [HTF] {symbol} VETO (insufficient {interval} candle data: {len(closes)})")
            return False
        sma = sum(closes) / len(closes)
        ok = closes[-1] > sma
        print(f"  [HTF] {symbol} {'OK' if ok else 'VETO'} "
              f"({interval} close {closes[-1]:.2f} vs SMA{lookback} {sma:.2f})")
        return ok
    except Exception as e:
        print(f"  [HTF] {symbol} VETO (error fetching {interval} trend data: {e})")
        return False
