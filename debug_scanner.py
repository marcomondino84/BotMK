import sys
sys.stdout.reconfigure(encoding='utf-8')
import asyncio
import pandas as pd
from bingx_client import BingXFuturesClient
from auto_scanner import AutoScanner

async def test_scanner_debug():
    client = BingXFuturesClient()
    scanner = AutoScanner(client)
    
    top_pairs = client.get_market_symbols_by_volume(limit=15)
    print(f"Probando {len(top_pairs)} pares top...")
    
    for item in top_pairs:
        ex_sym = item["symbol"]
        ticker = item["ticker"]
        
        try:
            # 15m
            ohlcv_15m = client.exchange.fetch_ohlcv(ex_sym, timeframe="15m", limit=60)
            df_15 = pd.DataFrame(ohlcv_15m, columns=["timestamp", "open", "high", "low", "close", "volume"])
            df_15["ema_21"] = df_15["close"].ewm(span=21, adjust=False).mean()
            df_15["ema_50"] = df_15["close"].ewm(span=50, adjust=False).mean()
            delta_15 = df_15["close"].diff()
            gain_15 = (delta_15.where(delta_15 > 0, 0)).rolling(window=14).mean()
            loss_15 = (-delta_15.where(delta_15 < 0, 0)).rolling(window=14).mean()
            rs_15 = gain_15 / (loss_15 + 1e-9)
            df_15["rsi"] = 100 - (100 / (1 + rs_15))
            c15 = df_15.iloc[-1]
            
            bull_15 = c15["close"] > c15["ema_21"] > c15["ema_50"] and c15["rsi"] > 50
            bear_15 = c15["close"] < c15["ema_21"] < c15["ema_50"] and c15["rsi"] < 50
            
            # 5m
            ohlcv_5m = client.exchange.fetch_ohlcv(ex_sym, timeframe="5m", limit=40)
            df_5 = pd.DataFrame(ohlcv_5m, columns=["timestamp", "open", "high", "low", "close", "volume"])
            df_5["ema_9"] = df_5["close"].ewm(span=9, adjust=False).mean()
            df_5["ema_21"] = df_5["close"].ewm(span=21, adjust=False).mean()
            c5 = df_5.iloc[-1]
            p5 = df_5.iloc[-2]
            
            crossover_buy = p5["ema_9"] <= p5["ema_21"] and c5["ema_9"] > c5["ema_21"]
            crossover_sell = p5["ema_9"] >= p5["ema_21"] and c5["ema_9"] < c5["ema_21"]
            
            print(f"[{ticker}] 15m Bull:{bull_15} Bear:{bear_15} (Close:{c15['close']:.2f}, EMA21:{c15['ema_21']:.2f}, EMA50:{c15['ema_50']:.2f}, RSI:{c15['rsi']:.1f}) | 5m CrossBuy:{crossover_buy} CrossSell:{crossover_sell} (p9:{p5['ema_9']:.2f}, p21:{p5['ema_21']:.2f}, c9:{c5['ema_9']:.2f}, c21:{c5['ema_21']:.2f})")
            
        except Exception as e:
            print(f"Error en {ticker}: {e}")

asyncio.run(test_scanner_debug())
