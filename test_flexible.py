import sys
sys.stdout.reconfigure(encoding='utf-8')
import asyncio
import pandas as pd
from bingx_client import BingXFuturesClient

async def test_flexible_mtf():
    client = BingXFuturesClient()
    top_pairs = client.get_market_symbols_by_volume(limit=30)
    print(f"Probando {len(top_pairs)} pares top con lógica MTF optimizada...")
    
    signals = []
    for item in top_pairs:
        ex_sym = item["symbol"]
        ticker = item["ticker"]
        
        try:
            ohlcv_15m = client.exchange.fetch_ohlcv(ex_sym, timeframe="15m", limit=50)
            if not ohlcv_15m or len(ohlcv_15m) < 30:
                continue
            df_15 = pd.DataFrame(ohlcv_15m, columns=["timestamp", "open", "high", "low", "close", "volume"])
            df_15["ema_21"] = df_15["close"].ewm(span=21, adjust=False).mean()
            df_15["ema_50"] = df_15["close"].ewm(span=50, adjust=False).mean()
            delta_15 = df_15["close"].diff()
            gain_15 = (delta_15.where(delta_15 > 0, 0)).rolling(window=14).mean()
            loss_15 = (-delta_15.where(delta_15 < 0, 0)).rolling(window=14).mean()
            rs_15 = gain_15 / (loss_15 + 1e-9)
            df_15["rsi"] = 100 - (100 / (1 + rs_15))
            c15 = df_15.iloc[-1]
            
            # Tendencia macro 15m (Estructura clara de medias y RSI)
            trend_bull_15m = (c15["ema_21"] > c15["ema_50"] or c15["close"] > c15["ema_21"]) and c15["rsi"] >= 48
            trend_bear_15m = (c15["ema_21"] < c15["ema_50"] or c15["close"] < c15["ema_21"]) and c15["rsi"] <= 52
            
            # Marco 5m: Gatillo de Cruce reciente (últimas 3 velas) o Momentum limpio
            ohlcv_5m = client.exchange.fetch_ohlcv(ex_sym, timeframe="5m", limit=30)
            if not ohlcv_5m or len(ohlcv_5m) < 20:
                continue
            df_5 = pd.DataFrame(ohlcv_5m, columns=["timestamp", "open", "high", "low", "close", "volume"])
            df_5["ema_9"] = df_5["close"].ewm(span=9, adjust=False).mean()
            df_5["ema_21"] = df_5["close"].ewm(span=21, adjust=False).mean()
            
            # Cruce en las últimas 3 velas de 5m
            recent_cross_buy = any(
                df_5.iloc[i-1]["ema_9"] <= df_5.iloc[i-1]["ema_21"] and df_5.iloc[i]["ema_9"] > df_5.iloc[i]["ema_21"]
                for i in range(-3, 0)
            )
            recent_cross_sell = any(
                df_5.iloc[i-1]["ema_9"] >= df_5.iloc[i-1]["ema_21"] and df_5.iloc[i]["ema_9"] < df_5.iloc[i]["ema_21"]
                for i in range(-3, 0)
            )
            
            # Momentum activo en 5m
            curr_5 = df_5.iloc[-1]
            active_bull_5m = curr_5["ema_9"] > curr_5["ema_21"] and curr_5["close"] > curr_5["ema_9"]
            active_bear_5m = curr_5["ema_9"] < curr_5["ema_21"] and curr_5["close"] < curr_5["ema_9"]
            
            signal = None
            if trend_bull_15m and (recent_cross_buy or active_bull_5m):
                signal = "BUY"
            elif trend_bear_15m and (recent_cross_sell or active_bear_5m):
                signal = "SELL"
                
            if signal:
                signals.append((ticker, signal, round(c15["close"], 4)))
                print(f"🔥 SEÑAL DETECTADA: {signal} en {ticker} a ${c15['close']}")
        except Exception as e:
            pass
            
    print(f"\nTotal de señales encontradas: {len(signals)} de {len(top_pairs)} pares analizados.")

asyncio.run(test_flexible_mtf())
