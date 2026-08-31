from binance.client import Client
import pandas as pd

def get_klines_df(api_key, api_secret, symbol="BTCUSDT", interval="1h", lookback=500):
    client = Client(api_key, api_secret, testnet=True)
    klines = client.get_historical_klines(symbol, interval, limit=lookback)
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
