"""
Estrategia Cuantitativa SMC / ICT Multi-Temporalidad:
1. Macro 4H: Filtro directriz institucional con EMA 50 (Solo Longs si Close > EMA50, Solo Shorts si Close < EMA50).
2. Marco 15m: Detección de Barridas de Liquidez (Sweeps de PDH/PDL o Swings fractales) + Cambio Estructural (iCHoCH).
3. Marco 5m: Gatillo de Confirmación por Vela de Rechazo y cálculo de SL técnico ceñido a la mecha.
"""

from typing import Optional, Dict, Any, Tuple
import pandas as pd
import numpy as np


def detect_macro_trend_4h(df_4h: pd.DataFrame, ema_period: int = 50) -> Optional[str]:
    """
    Evalúa la tendencia dominante en 4H usando EMA 50.
    Retorna: 'BULL', 'BEAR' o None.
    """
    if df_4h is None or len(df_4h) < ema_period:
        return None

    df = df_4h.copy()
    df["ema_macro"] = df["close"].ewm(span=ema_period, adjust=False).mean()

    curr = df.iloc[-1]
    close_price = curr["close"]
    ema_val = curr["ema_macro"]

    if close_price > ema_val:
        return "BULL"
    elif close_price < ema_val:
        return "BEAR"

    return None


def calculate_liquidity_levels(df_15m: pd.DataFrame, lookback: int = 15) -> Dict[str, float]:
    """
    Calcula los niveles clave de liquidez en 15m:
    - PDH (Previous Day High) aproximado con las últimas 96 velas (24 horas).
    - PDL (Previous Day Low) aproximado con las últimas 96 velas.
    - Swing Highs / Swing Lows fractales de los últimos `lookback` periodos.
    """
    levels = {}
    if df_15m is None or len(df_15m) < lookback + 2:
        return levels

    # 1. Rango del día anterior (aprox. 96 velas de 15m = 24 horas)
    bars_24h = min(96, len(df_15m) - 1)
    df_prev = df_15m.iloc[-bars_24h:-1]  # Excluir la vela actual
    levels["pdh"] = float(df_prev["high"].max())
    levels["pdl"] = float(df_prev["low"].min())

    # 2. Swing High y Swing Low recientes (fractales)
    df_recent = df_15m.iloc[-(lookback + 1):-1]
    levels["swing_high"] = float(df_recent["high"].max())
    levels["swing_low"] = float(df_recent["low"].min())

    return levels


def detect_ichoch(df_15m: pd.DataFrame, lookback: int = 5) -> Dict[str, Any]:
    """
    Módulo iCHoCH (Internal Change of Character) idéntico a TradingView:
    - Identifica extremos fractales: highest(high, lookback)[1] y lowest(low, lookback)[1].
    - Quiebre en tiempo real por cuerpo de vela (close > highest_val o close < lowest_val).
    - Verifica si ocurrió un quiebre en las últimas 4 velas.
    """
    res = {
        "recent_ich_bull": False,
        "recent_ich_bear": False,
        "highest_val": 0.0,
        "lowest_val": 0.0,
    }

    if df_15m is None or len(df_15m) < lookback + 5:
        return res

    df = df_15m.copy()
    # ta.highest(high, lookback)[1]
    df["highest_val"] = df["high"].shift(1).rolling(window=lookback).max()
    df["lowest_val"] = df["low"].shift(1).rolling(window=lookback).min()

    df["break_up"] = df["close"] > df["highest_val"]
    df["break_dn"] = df["close"] < df["lowest_val"]

    # Verificar si hubo quiebre en las últimas 4 velas
    recent_break_up = df["break_up"].iloc[-4:].any()
    recent_break_dn = df["break_dn"].iloc[-4:].any()

    curr = df.iloc[-1]
    res["recent_ich_bull"] = bool(recent_break_up)
    res["recent_ich_bear"] = bool(recent_break_dn)
    res["highest_val"] = float(curr["highest_val"]) if pd.notna(curr["highest_val"]) else 0.0
    res["lowest_val"] = float(curr["lowest_val"]) if pd.notna(curr["lowest_val"]) else 0.0

    return res


def detect_liquidity_sweeps(
    df_15m: pd.DataFrame,
    levels: Dict[str, float],
    tolerance_pct: float = 0.08
) -> Dict[str, Any]:
    """
    Detecta si la vela actual o anterior de 15m barrió la liquidez con la mecha pero cerró dentro.
    - Sweep High (para SHORT): High >= Level - Tol Y Close < Level.
    - Sweep Low (para LONG): Low <= Level + Tol Y Close > Level.
    """
    result = {
        "sweep_high": False,
        "sweep_low": False,
        "level_type": None,
        "level_price": 0.0,
        "sweep_wick_price": 0.0
    }

    if df_15m is None or len(df_15m) < 2 or not levels:
        return result

    # Analizar las 2 velas más recientes de 15m (actual y previa)
    candidates = [df_15m.iloc[-1], df_15m.iloc[-2]]

    # Evaluar primero PDH / PDL, luego Swings
    high_levels = [("PDH", levels.get("pdh", 0.0)), ("Swing_High", levels.get("swing_high", 0.0))]
    low_levels = [("PDL", levels.get("pdl", 0.0)), ("Swing_Low", levels.get("swing_low", 0.0))]

    # 1. Chequear barrida de máximos (Sweep High)
    for name, lvl in high_levels:
        if lvl <= 0:
            continue
        tol_val = lvl * (tolerance_pct / 100.0)
        for c in candidates:
            if c["high"] >= (lvl - tol_val) and c["close"] < lvl:
                result["sweep_high"] = True
                result["level_type"] = name
                result["level_price"] = lvl
                result["sweep_wick_price"] = float(c["high"])
                break
        if result["sweep_high"]:
            break

    # 2. Chequear barrida de mínimos (Sweep Low)
    for name, lvl in low_levels:
        if lvl <= 0:
            continue
        tol_val = lvl * (tolerance_pct / 100.0)
        for c in candidates:
            if c["low"] <= (lvl + tol_val) and c["close"] > lvl:
                result["sweep_low"] = True
                result["level_type"] = name
                result["level_price"] = lvl
                result["sweep_wick_price"] = float(c["low"])
                break
        if result["sweep_low"]:
            break

    return result


def evaluate_smc_setup(
    df_4h: pd.DataFrame,
    df_15m: pd.DataFrame,
    df_5m: pd.DataFrame,
    tolerance_pct: float = 0.08,
    risk_ratio: float = 1.5,
    min_sl_pct: float = 0.5,
    max_sl_pct: float = 3.5
) -> Optional[Dict[str, Any]]:
    """
    Evaluación integral cuantitativa institucional SMC:
    Paso 1: Tendencia 4H con EMA 50.
    Paso 2: Barrida de liquidez en 15m (PDH/PDL o Swings) + confirmación de estructura iCHoCH.
    Paso 3: Gatillo de rechazo en 5m y cálculo de SL técnico ceñido.
    """
    # 1. Filtro Macro 4H
    macro_trend = detect_macro_trend_4h(df_4h, ema_period=50)
    if not macro_trend:
        return None

    # 2. Estructura y Barrida en 15m
    levels = calculate_liquidity_levels(df_15m, lookback=15)
    sweeps = detect_liquidity_sweeps(df_15m, levels, tolerance_pct=tolerance_pct)
    ichoch = detect_ichoch(df_15m, lookback=5)

    curr_5 = df_5m.iloc[-1]
    curr_price = float(curr_5["close"])

    # EVALUAR CASO LONG:
    # Macro DEBE ser BULL + Barrida de mínimos (Sweep Low) + vela de rechazo alcista en 5m
    if macro_trend == "BULL" and sweeps["sweep_low"]:
        # Vela de confirmación en 5m (vela verde de rechazo o mecha inferior)
        is_bull_trigger_5m = curr_5["close"] >= curr_5["open"]
        # iCHoCH debe ser bull o estructura alcista activa
        is_ich_valid = ichoch["recent_ich_bull"] or (curr_price > ichoch["lowest_val"])

        if is_bull_trigger_5m and is_ich_valid:
            # SL Técnico por debajo de la mecha de barrida
            wick_low = min(sweeps["sweep_wick_price"], float(curr_5["low"]))
            sl_price = round(wick_low * 0.9985, 4)  # 0.15% por debajo de la mecha
            sl_dist_pct = ((curr_price - sl_price) / curr_price) * 100.0

            # Normalizar límites de SL para seguridad
            sl_dist_pct = max(min_sl_pct, min(max_sl_pct, sl_dist_pct))
            sl_price = round(curr_price * (1.0 - (sl_dist_pct / 100.0)), 4)
            tp_price = round(curr_price * (1.0 + ((sl_dist_pct * risk_ratio) / 100.0)), 4)

            return {
                "signal": "BUY",
                "pattern": f"SMC Sweep Low ({sweeps['level_type']}) + iCHoCH Bull",
                "level_type": sweeps["level_type"],
                "level_price": sweeps["level_price"],
                "macro_trend": "4H BULL (EMA 50)",
                "entry_price": curr_price,
                "sl_price": sl_price,
                "tp_price": tp_price,
                "sl_pct": round(sl_dist_pct, 2)
            }

    # EVALUAR CASO SHORT:
    # Macro DEBE ser BEAR + Barrida de máximos (Sweep High) + vela de rechazo bajista en 5m
    if macro_trend == "BEAR" and sweeps["sweep_high"]:
        # Vela de confirmación en 5m (vela roja de rechazo o mecha superior)
        is_bear_trigger_5m = curr_5["close"] <= curr_5["open"]
        # iCHoCH debe ser bear o estructura bajista activa
        is_ich_valid = ichoch["recent_ich_bear"] or (curr_price < ichoch["highest_val"])

        if is_bear_trigger_5m and is_ich_valid:
            # SL Técnico por encima de la mecha de barrida
            wick_high = max(sweeps["sweep_wick_price"], float(curr_5["high"]))
            sl_price = round(wick_high * 1.0015, 4)  # 0.15% por encima de la mecha
            sl_dist_pct = ((sl_price - curr_price) / curr_price) * 100.0

            # Normalizar límites de SL para seguridad
            sl_dist_pct = max(min_sl_pct, min(max_sl_pct, sl_dist_pct))
            sl_price = round(curr_price * (1.0 + (sl_dist_pct / 100.0)), 4)
            tp_price = round(curr_price * (1.0 - ((sl_dist_pct * risk_ratio) / 100.0)), 4)

            return {
                "signal": "SELL",
                "pattern": f"SMC Sweep High ({sweeps['level_type']}) + iCHoCH Bear",
                "level_type": sweeps["level_type"],
                "level_price": sweeps["level_price"],
                "macro_trend": "4H BEAR (EMA 50)",
                "entry_price": curr_price,
                "sl_price": sl_price,
                "tp_price": tp_price,
                "sl_pct": round(sl_dist_pct, 2)
            }

    return None
