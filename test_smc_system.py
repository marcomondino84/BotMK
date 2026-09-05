"""
Script de validación del sistema SMC / ICT, Screener de Volatilidad y Gestión de Estancamiento.
"""

import sys
import pandas as pd
import numpy as np
from datetime import datetime, timedelta

from config import config
from src.strategies.smc_strategy import (
    detect_macro_trend_4h,
    calculate_liquidity_levels,
    detect_ichoch,
    detect_liquidity_sweeps,
    evaluate_smc_setup
)
from bingx_client import BingXFuturesClient
from auto_scanner import AutoScanner


def test_smc_strategy_math():
    print("--- 1. Probando Estrategia SMC / ICT ---")

    # Crear velas 4H sintéticas alcistas (close > ema 50)
    dates_4h = pd.date_range(end=datetime.now(), periods=60, freq="4h")
    prices_4h = np.linspace(50000, 60000, 60)
    df_4h = pd.DataFrame({
        "timestamp": dates_4h,
        "open": prices_4h - 100,
        "high": prices_4h + 200,
        "low": prices_4h - 200,
        "close": prices_4h,
        "volume": [1000] * 60
    })

    macro_trend = detect_macro_trend_4h(df_4h, ema_period=50)
    print(f"Tendencia 4H detectada: {macro_trend}")
    assert macro_trend == "BULL", f"Esperado BULL, obtenido {macro_trend}"

    # Crear velas 15m con barrida de mínimo (Sweep Low)
    dates_15m = pd.date_range(end=datetime.now(), periods=60, freq="15min")
    prices_15m = np.full(60, 59000.0)
    df_15m = pd.DataFrame({
        "timestamp": dates_15m,
        "open": prices_15m,
        "high": prices_15m + 50,
        "low": prices_15m - 50,
        "close": prices_15m,
        "volume": [500] * 60
    })
    # Definir un mínimo fractal hace 10 velas en 58800
    df_15m.loc[df_15m.index[-10], "low"] = 58800.0
    # En la última vela, mecha barre a 58780 pero cierra en 58950 (Sweep Low!)
    df_15m.loc[df_15m.index[-1], "low"] = 58780.0
    df_15m.loc[df_15m.index[-1], "close"] = 58950.0

    levels = calculate_liquidity_levels(df_15m, lookback=15)
    print(f"Niveles calculados en 15m: {levels}")

    sweeps = detect_liquidity_sweeps(df_15m, levels, tolerance_pct=0.1)
    print(f"Barridas detectadas: {sweeps}")
    assert sweeps["sweep_low"] is True, "Debería haber detectado Sweep Low"

    # Velas 5m con vela verde de confirmación
    df_5m = pd.DataFrame({
        "timestamp": pd.date_range(end=datetime.now(), periods=20, freq="5min"),
        "open": [58900.0] * 20,
        "high": [59000.0] * 20,
        "low": [58800.0] * 20,
        "close": [58980.0] * 20,  # close > open (rejection)
        "volume": [100] * 20
    })

    setup = evaluate_smc_setup(df_4h, df_15m, df_5m)
    print(f"Setup SMC Evaluado: {setup}")
    assert setup is not None, "El setup SMC debería ser válido"
    assert setup["signal"] == "BUY", "La señal debería ser BUY"
    print("✅ Estrategia SMC validada con éxito.")


def test_screener_and_stagnant():
    print("\n--- 2. Probando Screener y Cliente BingX ---")
    client = BingXFuturesClient()

    print("Probando Screener por Volatilidad (Top 5)...")
    vol_symbols = client.get_market_symbols_by_volatility(limit=5, min_volume=1000000.0)
    print(f"Top 5 contratos más volátiles encontrados: {len(vol_symbols)}")
    for s in vol_symbols:
        print(f"  -> {s['ticker']}: Volatilidad={s.get('volatility_pct')}% | Vol24h=${s.get('volume_24h'):,.0f}")

    assert len(vol_symbols) > 0, "Debería retornar contratos volátiles"

    print("\nProbando inicialización y cambio de modo del AutoScanner...")
    scanner = AutoScanner(client)
    scanner.set_mode("top_volatility")
    assert scanner.scan_mode == "top_volatility", f"Modo esperado top_volatility, obtenido {scanner.scan_mode}"
    print(f"Modo del scanner verificado: {scanner.scan_mode}")

    print("\nProbando lógica de estancamiento temporal...")
    fake_positions = [{
        "symbol": "BTC/USDT:USDT",
        "side": "SHORT",
        "amount": 0.001,
        "entry_price": 60000.0,
        "current_price": 60050.0,  # Leve pérdida / plano
        "timestamp": int((datetime.now() - timedelta(hours=5)).timestamp() * 1000),  # Abierta hace 5 horas
        "roe_pct": -0.5
    }]
    # Esto no debe generar error
    client.check_and_apply_break_even(fake_positions)
    print("✅ Gestión de estancamiento probada sin errores.")


if __name__ == "__main__":
    try:
        test_smc_strategy_math()
        test_screener_and_stagnant()
        print("\n🎉 TODAS LAS PRUEBAS PASARON EXITOSAMENTE.")
    except Exception as e:
        print(f"\n❌ ERROR EN LAS PRUEBAS: {e}")
        import traceback
        traceback.print_exc()
        sys.exit(1)
