"""
Estrategia Cuantitativa Institucional SMC / ICT (Metodología Toni Maura):
1. Contexto Macro HTF (4H): Tendencia directriz con EMA 50 y EMA 200 + Zona Premium/Discount.
   (Solo Longs en sesgo alcista, solo Shorts en sesgo bajista).
2. Liquidez y Barrida (15m): Detección de PDH/PDL y Swings fractales, esperando barrida de liquidez (Sweep).
3. Ruptura de Zona con Cuerpo de Vela (15m / 5m): Quiebre estructural (MSS / BOS) confirmado
   obligatoriamente por CIERRE DE CUERPO de vela (nunca solo mechas).
4. Displacement + FVG (5m): Impulso institucional medido con ATR(14) y Volumen, que deja un
   Fair Value Gap (FVG) limpio sin invalidar.
5. Entrada en Mitigación del FVG + Confirmación + Indicadores (ADX, ATR, RSI) + SL Estructural.
"""

from typing import Optional, Dict, Any, List
import pandas as pd
import numpy as np


def compute_indicators(df: pd.DataFrame) -> pd.DataFrame:
    """Calcula ATR(14), RSI(14), Volumen SMA(20) y ratio de cuerpo de vela."""
    data = df.copy()

    # Rango de vela y cuerpo
    candle_range = (data["high"] - data["low"]).replace(0, np.nan)
    body = (data["close"] - data["open"]).abs()
    data["body"] = body
    data["body_ratio"] = (body / candle_range).fillna(0.0)

    # ATR(14)
    prev_close = data["close"].shift(1)
    tr = pd.concat(
        [
            data["high"] - data["low"],
            (data["high"] - prev_close).abs(),
            (data["low"] - prev_close).abs(),
        ],
        axis=1,
    ).max(axis=1)
    data["atr"] = tr.ewm(alpha=1.0 / 14.0, adjust=False).mean()

    # RSI(14)
    delta = data["close"].diff()
    gain = delta.clip(lower=0.0).ewm(alpha=1.0 / 14.0, adjust=False).mean()
    loss = (-delta.clip(upper=0.0)).ewm(alpha=1.0 / 14.0, adjust=False).mean()
    rs = gain / (loss + 1e-9)
    data["rsi"] = 100.0 - (100.0 / (1.0 + rs))

    # Media de volumen
    if "volume" in data.columns:
        data["vol_sma20"] = data["volume"].rolling(window=20, min_periods=5).mean()
    else:
        data["vol_sma20"] = 0.0

    return data


def detect_macro_trend_4h(df_4h: pd.DataFrame, ema_period: int = 50) -> Optional[str]:
    """
    Evalúa la tendencia dominante en 4H usando EMA 50 y pendiente reciente.
    Retorna: 'BULL', 'BEAR' o None.
    """
    if df_4h is None or len(df_4h) < ema_period:
        return None

    df = df_4h.copy()
    df["ema_macro"] = df["close"].ewm(span=ema_period, adjust=False).mean()

    curr = df.iloc[-1]
    prev = df.iloc[-4] if len(df) >= 4 else df.iloc[-2]
    close_price = float(curr["close"])
    ema_val = float(curr["ema_macro"])
    ema_prev = float(prev["ema_macro"])

    # Exigir alineación entre precio y EMA50 (y pendiente no fuertemente contraria)
    if close_price > ema_val and ema_val >= ema_prev * 0.997:
        return "BULL"
    elif close_price < ema_val and ema_val <= ema_prev * 1.003:
        return "BEAR"

    return None


def calculate_liquidity_levels(
    df_15m: pd.DataFrame, lookback: int = 12, exclude_recent: int = 8
) -> Dict[str, float]:
    """
    Calcula niveles clave de liquidez previos en 15m (excluyendo la ventana reciente de barrida):
    - PDH / PDL: Máximo y mínimo de últimas 24h (96 velas de 15m).
    - Swing High / Swing Low recientes para detectar barridas reales.
    """
    levels: Dict[str, float] = {}
    if df_15m is None or len(df_15m) < lookback + exclude_recent + 2:
        return levels

    bars_24h = min(96, len(df_15m) - exclude_recent)
    df_prev = df_15m.iloc[-bars_24h:-exclude_recent]
    levels["pdh"] = float(df_prev["high"].max())
    levels["pdl"] = float(df_prev["low"].min())

    df_recent = df_15m.iloc[-(lookback + exclude_recent):-exclude_recent]
    levels["swing_high"] = float(df_recent["high"].max())
    levels["swing_low"] = float(df_recent["low"].min())

    return levels


def detect_liquidity_sweeps(
    df_15m: pd.DataFrame,
    levels: Dict[str, float],
    tolerance_pct: float = 0.08,
    lookback_candles: int = 10,
) -> Dict[str, Any]:
    """
    Detecta si en las últimas velas de 15m hubo una barrida de liquidez (Sweep):
    - Sweep Low (para LONG): Low <= Level + Tol y cierre recuperando por encima o con rechazo.
    - Sweep High (para SHORT): High >= Level - Tol y cierre recuperando por debajo o con rechazo.
    """
    result = {
        "sweep_high": False,
        "sweep_low": False,
        "level_type": None,
        "level_price": 0.0,
        "sweep_wick_price": 0.0,
    }

    if df_15m is None or len(df_15m) < lookback_candles + 2 or not levels:
        return result

    recent_candles = [df_15m.iloc[-i] for i in range(1, min(lookback_candles + 1, len(df_15m)))]

    high_levels = [("PDH", levels.get("pdh", 0.0)), ("Swing_High", levels.get("swing_high", 0.0))]
    low_levels = [("PDL", levels.get("pdl", 0.0)), ("Swing_Low", levels.get("swing_low", 0.0))]

    for name, lvl in high_levels:
        if lvl <= 0:
            continue
        tol_val = lvl * (tolerance_pct / 100.0)
        for c in recent_candles:
            if float(c["high"]) > lvl and float(c["close"]) < lvl:
                result["sweep_high"] = True
                result["level_type"] = name
                result["level_price"] = lvl
                result["sweep_wick_price"] = max(float(c["high"]), result["sweep_wick_price"])
                break
        if result["sweep_high"]:
            break

    for name, lvl in low_levels:
        if lvl <= 0:
            continue
        tol_val = lvl * (tolerance_pct / 100.0)
        for c in recent_candles:
            if float(c["low"]) < lvl and float(c["close"]) > lvl:
                result["sweep_low"] = True
                result["level_type"] = name
                result["level_price"] = lvl
                if result["sweep_wick_price"] == 0.0 or float(c["low"]) < result["sweep_wick_price"]:
                    result["sweep_wick_price"] = float(c["low"])
                break
        if result["sweep_low"]:
            break

    return result


def detect_body_structure_break(
    df: pd.DataFrame,
    lookback: int = 8,
    recent_window: int = 8,
    min_body_ratio: float = 0.45,
) -> Dict[str, Any]:
    """
    Regla Toni Maura: Ruptura de zona/estructura OBLIGATORIAMENTE con CUERPO DE VELA.
    - No cuentan mechas: close debe superar el máximo/mínimo fractal previo.
    - La vela de ruptura debe tener cuerpo sólido (body_ratio >= min_body_ratio).
    """
    res = {
        "bull_break": False,
        "bear_break": False,
        "bull_break_idx": -1,
        "bear_break_idx": -1,
        "broken_high": 0.0,
        "broken_low": 0.0,
    }

    if df is None or len(df) < lookback + recent_window + 2:
        return res

    data = compute_indicators(df)
    data["prev_high"] = data["high"].shift(1).rolling(window=lookback).max()
    data["prev_low"] = data["low"].shift(1).rolling(window=lookback).min()

    start_idx = len(data) - recent_window
    for i in range(start_idx, len(data)):
        row = data.iloc[i]
        prev_h = row["prev_high"]
        prev_l = row["prev_low"]
        if pd.isna(prev_h) or pd.isna(prev_l):
            continue

        body_ok = float(row["body_ratio"]) >= min_body_ratio
        if body_ok and float(row["close"]) > float(prev_h) and float(row["close"]) > float(row["open"]):
            res["bull_break"] = True
            res["bull_break_idx"] = i
            res["broken_high"] = float(prev_h)

        if body_ok and float(row["close"]) < float(prev_l) and float(row["close"]) < float(row["open"]):
            res["bear_break"] = True
            res["bear_break_idx"] = i
            res["broken_low"] = float(prev_l)

    return res


def detect_ichoch(df_15m: pd.DataFrame, lookback: int = 5) -> Dict[str, Any]:
    """
    Compatibilidad con checklist del scanner: detecta quiebre estructural por cuerpo en 15m.
    """
    brk = detect_body_structure_break(df_15m, lookback=lookback, recent_window=5, min_body_ratio=0.40)
    curr_high = float(df_15m["high"].iloc[-(lookback + 1):-1].max()) if len(df_15m) > lookback + 1 else 0.0
    curr_low = float(df_15m["low"].iloc[-(lookback + 1):-1].min()) if len(df_15m) > lookback + 1 else 0.0
    return {
        "recent_ich_bull": brk["bull_break"],
        "recent_ich_bear": brk["bear_break"],
        "highest_val": brk["broken_high"] or curr_high,
        "lowest_val": brk["broken_low"] or curr_low,
    }


def detect_fvg_with_displacement(
    df_5m: pd.DataFrame,
    direction: str,
    lookback_bars: int = 20,
    min_displacement_atr: float = 1.0,
    min_body_ratio: float = 0.45,
) -> Optional[Dict[str, Any]]:
    """
    Detecta un Fair Value Gap (FVG) reciente generado por Displacement institucional en 5m:
    - BULL: candle[i].low > candle[i-2].high, con candle[i-1] alcista de gran cuerpo (displacement).
    - BEAR: candle[i].high < candle[i-2].low, con candle[i-1] bajista de gran cuerpo (displacement).
    - Verifica que el FVG NO haya sido invalidado (cierre con cuerpo más allá del extremo opuesto).
    """
    if df_5m is None or len(df_5m) < max(25, lookback_bars + 3):
        return None

    data = compute_indicators(df_5m)
    start_i = max(2, len(data) - lookback_bars)
    latest_fvg: Optional[Dict[str, Any]] = None

    for i in range(start_i, len(data) - 1):
        c0 = data.iloc[i - 2]
        c1 = data.iloc[i - 1]
        c2 = data.iloc[i]

        atr_val = float(c1["atr"]) if pd.notna(c1["atr"]) and c1["atr"] > 0 else 0.0
        if atr_val <= 0:
            continue

        c1_range = float(c1["high"] - c1["low"])
        c1_body_ratio = float(c1["body_ratio"])
        vol_ok = True
        if pd.notna(c1["vol_sma20"]) and float(c1["vol_sma20"]) > 0 and "volume" in data.columns:
            vol_ok = float(c1["volume"]) >= float(c1["vol_sma20"]) * 0.9

        has_displacement = (c1_range >= atr_val * min_displacement_atr) and (c1_body_ratio >= min_body_ratio) and vol_ok
        if not has_displacement:
            continue

        if direction == "BULL":
            is_bull_c1 = float(c1["close"]) > float(c1["open"])
            gap_low = float(c0["high"])
            gap_high = float(c2["low"])
            if is_bull_c1 and gap_high > gap_low:
                # Verificar que ninguna vela posterior (excluyendo la actual) haya cerrado con cuerpo debajo de gap_low
                invalidated = False
                for j in range(i + 1, len(data)):
                    if float(data.iloc[j]["close"]) < gap_low:
                        invalidated = True
                        break
                if not invalidated:
                    latest_fvg = {
                        "type": "BULL_FVG",
                        "low": gap_low,
                        "high": gap_high,
                        "mid": (gap_low + gap_high) / 2.0,
                        "displacement_atr": round(c1_range / atr_val, 2),
                        "swing_origin": float(min(c0["low"], c1["low"])),
                        "index": i,
                    }

        elif direction == "BEAR":
            is_bear_c1 = float(c1["close"]) < float(c1["open"])
            gap_high = float(c0["low"])
            gap_low = float(c2["high"])
            if is_bear_c1 and gap_high > gap_low:
                invalidated = False
                for j in range(i + 1, len(data)):
                    if float(data.iloc[j]["close"]) > gap_high:
                        invalidated = True
                        break
                if not invalidated:
                    latest_fvg = {
                        "type": "BEAR_FVG",
                        "low": gap_low,
                        "high": gap_high,
                        "mid": (gap_low + gap_high) / 2.0,
                        "displacement_atr": round(c1_range / atr_val, 2),
                        "swing_origin": float(max(c0["high"], c1["high"])),
                        "index": i,
                    }

    return latest_fvg


def evaluate_smc_setup(
    df_4h: pd.DataFrame,
    df_15m: pd.DataFrame,
    df_5m: pd.DataFrame,
    tolerance_pct: float = 0.08,
    risk_ratio: float = 2.0,
    min_sl_pct: float = 1.3,
    max_sl_pct: float = 3.0,
) -> Optional[Dict[str, Any]]:
    """
    Motor Cuantitativo Toni Maura (SMC + FVG + Displacement + Ruptura con Cuerpo):
    1. Filtro Macro 4H OBLIGATORIO: Solo BUY si 4H es BULL; solo SELL si 4H es BEAR.
    2. Liquidez 15m: Barrida de PDH/PDL o Swing High/Low reciente.
    3. Ruptura de Zona con Cuerpo de Vela: Confirmada en 15m o 5m (MSS/BOS).
    4. Displacement + FVG en 5m: Impulso fuerte que deja FVG válido y no invalidado.
    5. Mitigación y Gatillo: El precio actual retrocede al FVG (o zona inmediata) y confirma
       con vela de rechazo en dirección de la tendencia, con RSI saludable.
    """
    if df_4h is None or df_15m is None or df_5m is None:
        return None
    if len(df_15m) < 25 or len(df_5m) < 25:
        return None

    # 1. Contexto Macro 4H (Filtro Estricto)
    macro_trend = detect_macro_trend_4h(df_4h, ema_period=50)
    if macro_trend not in ("BULL", "BEAR"):
        return None

    # 2. Niveles de Liquidez y Barrida en 15m
    levels = calculate_liquidity_levels(df_15m, lookback=12, exclude_recent=8)
    sweeps = detect_liquidity_sweeps(df_15m, levels, tolerance_pct=tolerance_pct, lookback_candles=10)

    # 3. Ruptura estructural con CUERPO DE VELA (15m o 5m)
    brk_15m = detect_body_structure_break(df_15m, lookback=6, recent_window=8, min_body_ratio=0.40)
    brk_5m = detect_body_structure_break(df_5m, lookback=8, recent_window=14, min_body_ratio=0.40)

    df_5m_ind = compute_indicators(df_5m)
    curr_5 = df_5m_ind.iloc[-1]
    closed_5 = df_5m_ind.iloc[-2]
    prev_closed_5 = df_5m_ind.iloc[-3]
    curr_price = float(curr_5["close"])
    curr_rsi = float(closed_5["rsi"]) if pd.notna(closed_5["rsi"]) else 50.0

    # ================== CASO BUY (LONG) ==================
    # Válido con (Sweep Low + Body Break) O (Continuación: Body Break en 15m/5m) + Displacement + FVG
    if macro_trend == "BULL":
        has_body_break = brk_15m["bull_break"] or brk_5m["bull_break"]
        if not has_body_break:
            return None

        fvg = detect_fvg_with_displacement(df_5m_ind, direction="BULL")
        if not fvg:
            return None

        # Exigir vela de 5m CERRADA (closed_5) confirmando mitigación y rechazo del FVG
        touch_tol = curr_price * 0.0015
        price_tapped_fvg = (float(closed_5["low"]) <= fvg["high"] + touch_tol) or (
            float(prev_closed_5["low"]) <= fvg["high"] + touch_tol
        )
        not_broken_below = curr_price >= fvg["low"] * 0.998 and float(closed_5["close"]) >= fvg["low"]
        bull_confirm = (float(closed_5["close"]) > float(closed_5["open"])) and (curr_rsi < 72.0)

        if price_tapped_fvg and not_broken_below and bull_confirm:
            sweep_low_px = float(sweeps["sweep_wick_price"]) if sweeps["sweep_low"] and sweeps["sweep_wick_price"] > 0 else float(fvg["swing_origin"])
            struct_low = min(sweep_low_px, float(fvg["swing_origin"]))
            sl_price = round(struct_low * 0.9985, 6)
            sl_dist_pct = ((curr_price - sl_price) / curr_price) * 100.0

            # Si el SL estructural es demasiado amplio (> max_sl_pct), descartar setup (mal R:R)
            if sl_dist_pct > max_sl_pct:
                return None

            sl_dist_pct = max(min_sl_pct, sl_dist_pct)
            sl_price = round(curr_price * (1.0 - (sl_dist_pct / 100.0)), 6)

            # Take Profit por liquidez (PDH / Swing High) si supera el R:R mínimo, sino R:R objetivo
            target_liq = max(levels.get("pdh", 0.0), levels.get("swing_high", 0.0))
            min_tp_price = curr_price * (1.0 + ((sl_dist_pct * risk_ratio) / 100.0))
            if target_liq > min_tp_price:
                tp_price = round(target_liq, 6)
            else:
                tp_price = round(min_tp_price, 6)

            entry_mode = f"Sweep({sweeps['level_type']})" if sweeps["sweep_low"] else "Cont"
            return {
                "signal": "BUY",
                "pattern": f"ToniMaura {entry_mode} + BodyBreak + Disp({fvg['displacement_atr']}xATR) + FVG",
                "level_type": sweeps["level_type"] or "FVG_CONT",
                "level_price": sweeps["level_price"] or round(fvg["mid"], 6),
                "macro_trend": "4H BULL (EMA 50)",
                "fvg_zone": [round(fvg["low"], 6), round(fvg["high"], 6)],
                "displacement_atr": fvg["displacement_atr"],
                "entry_price": curr_price,
                "sl_price": sl_price,
                "tp_price": tp_price,
                "sl_pct": round(sl_dist_pct, 2),
            }

    # ================== CASO SELL (SHORT) ==================
    # Válido con (Sweep High + Body Break) O (Continuación: Body Break en 15m/5m) + Displacement + FVG
    if macro_trend == "BEAR":
        has_body_break = brk_15m["bear_break"] or brk_5m["bear_break"]
        if not has_body_break:
            return None

        fvg = detect_fvg_with_displacement(df_5m_ind, direction="BEAR")
        if not fvg:
            return None

        touch_tol = curr_price * 0.0015
        price_tapped_fvg = (float(closed_5["high"]) >= fvg["low"] - touch_tol) or (
            float(prev_closed_5["high"]) >= fvg["low"] - touch_tol
        )
        not_broken_above = curr_price <= fvg["high"] * 1.002 and float(closed_5["close"]) <= fvg["high"]
        bear_confirm = (float(closed_5["close"]) < float(closed_5["open"])) and (curr_rsi > 28.0)

        if price_tapped_fvg and not_broken_above and bear_confirm:
            sweep_high_px = float(sweeps["sweep_wick_price"]) if sweeps["sweep_high"] and sweeps["sweep_wick_price"] > 0 else float(fvg["swing_origin"])
            struct_high = max(sweep_high_px, float(fvg["swing_origin"]))
            sl_price = round(struct_high * 1.0015, 6)
            sl_dist_pct = ((sl_price - curr_price) / curr_price) * 100.0

            if sl_dist_pct > max_sl_pct:
                return None

            sl_dist_pct = max(min_sl_pct, sl_dist_pct)
            sl_price = round(curr_price * (1.0 + (sl_dist_pct / 100.0)), 6)

            target_liq = min(
                p for p in [levels.get("pdl", 0.0), levels.get("swing_low", 0.0)] if p > 0
            ) if any(p > 0 for p in [levels.get("pdl", 0.0), levels.get("swing_low", 0.0)]) else 0.0
            min_tp_price = curr_price * (1.0 - ((sl_dist_pct * risk_ratio) / 100.0))
            if 0 < target_liq < min_tp_price:
                tp_price = round(target_liq, 6)
            else:
                tp_price = round(min_tp_price, 6)

            entry_mode = f"Sweep({sweeps['level_type']})" if sweeps["sweep_high"] else "Cont"
            return {
                "signal": "SELL",
                "pattern": f"ToniMaura {entry_mode} + BodyBreak + Disp({fvg['displacement_atr']}xATR) + FVG",
                "level_type": sweeps["level_type"] or "FVG_CONT",
                "level_price": sweeps["level_price"] or round(fvg["mid"], 6),
                "macro_trend": "4H BEAR (EMA 50)",
                "fvg_zone": [round(fvg["low"], 6), round(fvg["high"], 6)],
                "displacement_atr": fvg["displacement_atr"],
                "entry_price": curr_price,
                "sl_price": sl_price,
                "tp_price": tp_price,
                "sl_pct": round(sl_dist_pct, 2),
            }

    return None
