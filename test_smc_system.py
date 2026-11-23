"""
Script de validación de la Estrategia Toni Maura (SMC + FVG + Displacement + Ruptura con Cuerpo)
y del blindaje de gestión de riesgo.
"""

import pandas as pd
import numpy as np
from datetime import datetime, timedelta

from src.strategies.smc_strategy import (
    detect_macro_trend_4h,
    calculate_liquidity_levels,
    detect_liquidity_sweeps,
    detect_body_structure_break,
    detect_fvg_with_displacement,
    evaluate_smc_setup,
)


def _make_df(rows: list, freq: str = "5min") -> pd.DataFrame:
    dates = pd.date_range(end=datetime.now(), periods=len(rows), freq=freq)
    df = pd.DataFrame(rows, columns=["open", "high", "low", "close", "volume"])
    df["timestamp"] = dates
    return df[["timestamp", "open", "high", "low", "close", "volume"]]


def test_toni_maura_bull_setup():
    print("--- 1. Probando Estrategia Toni Maura (BUY: Sweep + Body Break + Displacement + FVG) ---")

    # 1. Macro 4H alcista (60 velas subiendo)
    prices_4h = np.linspace(50000, 60000, 60)
    df_4h = _make_df(
        [[p - 100, p + 200, p - 200, p, 1000.0] for p in prices_4h],
        freq="4h",
    )
    assert detect_macro_trend_4h(df_4h) == "BULL"

    # 2. Velas 15m: rango lateral con mínimo previo en 58800 y barrida reciente a 58770 cerrando en 58950
    rows_15m = [[59000.0, 59050.0, 58950.0, 59000.0, 500.0] for _ in range(40)]
    rows_15m[-12] = [58920.0, 58960.0, 58800.0, 58900.0, 600.0]  # Swing Low en 58800
    rows_15m[-3] = [58900.0, 58920.0, 58770.0, 58950.0, 900.0]   # Sweep Low (mecha barre 58800 y cierra arriba)
    rows_15m[-2] = [58950.0, 59120.0, 58940.0, 59110.0, 1100.0]  # Ruptura con cuerpo > 59050
    df_15m = _make_df(rows_15m, freq="15min")

    levels = calculate_liquidity_levels(df_15m, lookback=20)
    sweeps = detect_liquidity_sweeps(df_15m, levels, tolerance_pct=0.08)
    assert sweeps["sweep_low"] is True, f"Esperado sweep_low=True, obtenido {sweeps}"

    brk_15 = detect_body_structure_break(df_15m, lookback=6, recent_window=6)
    assert brk_15["bull_break"] is True, f"Esperado bull_break=True, obtenido {brk_15}"

    # 3. Velas 5m: consolidación normal con vaivenes, luego Displacement alcista dejando FVG limpio, retroceso y gatillo
    rows_5m = [
        [58950.0 + (15 if idx % 2 == 0 else -15), 58985.0, 58915.0, 58940.0 + (15 if idx % 2 == 0 else -15), 200.0]
        for idx in range(25)
    ]
    # Vela i-2 (índice -5): high = 58980
    rows_5m[-5] = [58955.0, 58980.0, 58940.0, 58975.0, 250.0]
    # Vela i-1 (índice -4): Displacement fuerte alcista (rango = 220 >> ATR, cuerpo sólido)
    rows_5m[-4] = [58975.0, 59195.0, 58970.0, 59180.0, 800.0]
    # Vela i (índice -3): low = 59060 > 58980 -> FVG = [58980, 59060]
    rows_5m[-3] = [59180.0, 59210.0, 59060.0, 59170.0, 400.0]
    # Vela -2: retroceso parcial sin invalidar 58980
    rows_5m[-2] = [59170.0, 59175.0, 59050.0, 59070.0, 220.0]
    # Vela -1 (actual): toca el FVG (low=59020 <= 59060) y rechaza al alza (close > open)
    rows_5m[-1] = [59070.0, 59150.0, 59020.0, 59140.0, 350.0]
    df_5m = _make_df(rows_5m, freq="5min")

    fvg = detect_fvg_with_displacement(df_5m, direction="BULL")
    assert fvg is not None, "Debería detectar FVG alcista con displacement"
    assert fvg["low"] == 58980.0 and fvg["high"] == 59060.0

    setup = evaluate_smc_setup(df_4h, df_15m, df_5m, risk_ratio=2.0)
    assert setup is not None, "El setup Toni Maura BUY debería ser válido"
    assert setup["signal"] == "BUY"
    assert setup["sl_price"] < setup["entry_price"] < setup["tp_price"]
    print(f"OK Setup BUY validado: {setup['pattern']} | SL={setup['sl_price']} | TP={setup['tp_price']}")


def test_wick_only_break_rejected():
    print("--- 2. Verificando rechazo de ruptura solo con mecha (Regla Toni Maura) ---")
    rows_5m = [[100.0, 102.0, 99.0, 100.5, 100.0] for _ in range(25)]
    # Última vela rompe 102 con mecha (high=105) pero cierra dentro (close=101.0)
    rows_5m[-1] = [100.5, 105.0, 100.0, 101.0, 300.0]
    df = _make_df(rows_5m, freq="5min")
    brk = detect_body_structure_break(df, lookback=8, recent_window=3)
    assert brk["bull_break"] is False, "Una mecha sin cierre de cuerpo NO debe validar ruptura"
    print("OK Ruptura solo con mecha rechazada correctamente.")


def test_macro_filter_blocks_countertrend():
    print("--- 3. Verificando filtro estricto de tendencia Macro 4H ---")
    # 4H bajista
    prices_4h = np.linspace(60000, 50000, 60)
    df_4h_bear = _make_df([[p + 100, p + 200, p - 200, p, 1000.0] for p in prices_4h], freq="4h")
    assert detect_macro_trend_4h(df_4h_bear) == "BEAR"

    # Reusar velas 15m/5m alcistas -> debe retornar None porque 4H es BEAR
    rows_15m = [[59000.0, 59050.0, 58950.0, 59000.0, 500.0] for _ in range(40)]
    rows_15m[-12] = [58920.0, 58960.0, 58800.0, 58900.0, 600.0]
    rows_15m[-3] = [58900.0, 58920.0, 58770.0, 58950.0, 900.0]
    rows_15m[-2] = [58950.0, 59120.0, 58940.0, 59110.0, 1100.0]
    df_15m = _make_df(rows_15m, freq="15min")

    rows_5m = [[58950.0, 58970.0, 58930.0, 58955.0, 200.0] for _ in range(25)]
    rows_5m[-5] = [58955.0, 58980.0, 58940.0, 58975.0, 250.0]
    rows_5m[-4] = [58975.0, 59195.0, 58970.0, 59180.0, 800.0]
    rows_5m[-3] = [59180.0, 59210.0, 59060.0, 59170.0, 400.0]
    rows_5m[-2] = [59170.0, 59175.0, 59050.0, 59070.0, 220.0]
    rows_5m[-1] = [59070.0, 59150.0, 59020.0, 59140.0, 350.0]
    df_5m = _make_df(rows_5m, freq="5min")

    setup = evaluate_smc_setup(df_4h_bear, df_15m, df_5m)
    assert setup is None, "No debe permitir BUY cuando 4H es BEAR"
    print("OK Operación contratendencia bloqueada correctamente.")


if __name__ == "__main__":
    test_toni_maura_bull_setup()
    test_wick_only_break_rejected()
    test_macro_filter_blocks_countertrend()
    print("\nTODAS LAS PRUEBAS DE ESTRATEGIA TONI MAURA PASARON EXITOSAMENTE.")
