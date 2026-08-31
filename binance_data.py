"""
Fetches real klines (candles) directly from Binance - replaces Yahoo Finance
Same OHLCV format as before, so bot_engine_v3 works unchanged
"""
from binance.client import Client
import pandas as pd

def get_klines_df(api_key, api_secret, symbol="BTCUSDT", interval="1h", lookback=500):
    client = Client(api_key, api_secret, testnet=True)
    kl = client.get_klines(symbol=symbol, interval=interval, limit=lookback)
    df = pd.DataFrame([row[:11] for row in kl], columns=[
        'time', 'Open', 'High', 'Low', 'Close', 'Volume',
        'close_time', 'trades', 'bb_base', 'bb_quote', 'ignore'])
    for c in ['Open', 'High', 'Low', 'Close', 'Volume']:
        df[c] = df[c].astype(float)
    df['time'] = pd.to_datetime(df['time'], unit='ms')
    df = df.set_index('time')
    return df[['Open', 'High', 'Low', 'Close', 'Volume']]

if __name__ == "__main__":
    import os
    from dotenv import load_dotenv
    load_dotenv()
    df = get_klines_df(os.getenv("BINANCE_API_KEY"), os.getenv("BINANCE_API_SECRET"))
    print(f"Got {len(df)} REAL Binance candles for BTCUSDT (1h):")
    print(df.tail(5))
    print("\nDATA SOURCE SWITCHED - no more Yahoo delay!")
