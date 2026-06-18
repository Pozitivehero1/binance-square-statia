import matplotlib.pyplot as plt
import mplfinance as mpf
import pandas as pd
import tempfile
import os

def generate_chart(symbol, raw_data, basic):
    """
    Генерирует свечной график с EMA20/EMA50 и сохраняет во временный PNG-файл.
    Возвращает путь к файлу.
    """
    if not raw_data or len(raw_data) < 50:
        return None

    try:
        # Преобразуем данные в DataFrame
        df = pd.DataFrame(raw_data)
        df.columns = ['timestamp', 'open', 'high', 'low', 'close', 'volume',
                      'close_time', 'quote_asset_volume', 'number_of_trades',
                      'taker_buy_base_asset_volume', 'taker_buy_quote_asset_volume', 'ignore']
        # Конвертируем типы
        for col in ['open', 'high', 'low', 'close', 'volume']:
            df[col] = df[col].astype(float)
        df['timestamp'] = pd.to_datetime(df['timestamp'], unit='ms')
        df.set_index('timestamp', inplace=True)
        # Берём последние 100 свечей для наглядности
        df = df.tail(100)

        # Добавляем EMA
        df['EMA20'] = df['close'].ewm(span=20, adjust=False).mean()
        df['EMA50'] = df['close'].ewm(span=50, adjust=False).mean()

        # Строим график
        apds = [
            mpf.make_addplot(df['EMA20'], color='orange', width=0.7),
            mpf.make_addplot(df['EMA50'], color='blue', width=0.7),
        ]
        # Сохраняем во временный файл
        temp_file = tempfile.NamedTemporaryFile(suffix='.png', delete=False)
        temp_path = temp_file.name
        temp_file.close()

        mpf.plot(df, type='candle', style='charles',
                 addplot=apds, volume=True,
                 title=f'{basic} (USDT) 15m Chart',
                 ylabel='Price (USDT)',
                 savefig=temp_path,
                 figsize=(10, 6))

        return temp_path

    except Exception as e:
        print(f"[CHART] Error generating chart: {e}")
        return None
