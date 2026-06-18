import matplotlib.pyplot as plt
import mplfinance as mpf
import pandas as pd
import tempfile
import os

def generate_chart(symbol, raw_data, basic):
    """
    Генерирует свечной график для использования в качестве обложки статьи.
    """
    if not raw_data or len(raw_data) < 50:
        return None

    try:
        df = pd.DataFrame(raw_data)
        df.columns = ['timestamp', 'open', 'high', 'low', 'close', 'volume',
                      'close_time', 'quote_asset_volume', 'number_of_trades',
                      'taker_buy_base_asset_volume', 'taker_buy_quote_asset_volume', 'ignore']
        
        for col in ['open', 'high', 'low', 'close', 'volume']:
            df[col] = df[col].astype(float)
        
        df['timestamp'] = pd.to_datetime(df['timestamp'], unit='ms')
        df.set_index('timestamp', inplace=True)
        df = df.tail(100)

        df['EMA20'] = df['close'].ewm(span=20, adjust=False).mean()
        df['EMA50'] = df['close'].ewm(span=50, adjust=False).mean()

        apds = [
            mpf.make_addplot(df['EMA20'], color='orange', width=0.7),
            mpf.make_addplot(df['EMA50'], color='blue', width=0.7),
        ]
        
        temp_file = tempfile.NamedTemporaryFile(suffix='.png', delete=False)
        temp_path = temp_file.name
        temp_file.close()

        # Стиль для обложки — более яркий и чистый
        mpf.plot(df, type='candle', style='binance',
                 addplot=apds, volume=True,
                 title=f'{basic}/USDT • 15m',
                 ylabel='Price (USDT)',
                 savefig=temp_path,
                 figsize=(12, 7),
                 tight_layout=True)

        return temp_path

    except Exception as e:
        print(f"[CHART] Error generating chart: {e}")
        return None
