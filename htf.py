def htf_uptrend(client, symbol):
    try:
        k = client.get_klines(symbol=symbol, interval="1h", limit=20)
        closes = [float(r[4]) for r in k]
        sma = sum(closes) / len(closes)
        ok = closes[-1] > sma
        print("  [HTF] %s %s (1h close %.2f vs SMA20 %.2f)" % (symbol, "OK" if ok else "VETO", closes[-1], sma))
        return ok
    except Exception:
        return True
