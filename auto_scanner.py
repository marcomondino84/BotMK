"""
Scanner Cuantitativo Autónomo Multi-Temporalidad Institucional (SMC / ICT):
1. Macro 4H: Filtro directriz con EMA 50 (Solo Longs si Close > EMA50, Solo Shorts si Close < EMA50).
2. Marco 15m: Detección de Barridas de Liquidez (Sweeps de PDH/PDL o Swings fractales) + Cambio Estructural (iCHoCH).
3. Marco 5m: Gatillo de Confirmación por Vela de Rechazo y cálculo de SL técnico ceñido a la mecha.
4. Screener Dinámico de Alta Volatilidad (Top Movers 24h) para máxima actividad y liquidez.
"""

import asyncio
from datetime import datetime
from typing import Optional, Dict, Any, List, Tuple
import pandas as pd
from config import config, ASSET_CATALOG
from database import (
    get_bot_state,
    set_scanner_state,
    add_system_log,
    get_db_watchlist,
    get_symbol_cooldown,
    check_daily_drawdown_and_reset,
)
from notifications import notifier
from src.strategies.benchmark_sentinel import benchmark_sentinel
from src.strategies.smc_strategy import (
    evaluate_smc_setup,
    detect_macro_trend_4h,
    calculate_liquidity_levels,
    detect_liquidity_sweeps,
    detect_body_structure_break,
    detect_fvg_with_displacement,
)



def calculate_adx(df: pd.DataFrame, period: int = 14) -> pd.DataFrame:
    """Calcula True Range, +DI, -DI y ADX (Average Directional Index) con suavizado de Wilder."""
    tr1 = df["high"] - df["low"]
    tr2 = (df["high"] - df["close"].shift(1)).abs()
    tr3 = (df["low"] - df["close"].shift(1)).abs()
    tr = pd.concat([tr1, tr2, tr3], axis=1).max(axis=1)

    up_move = df["high"] - df["high"].shift(1)
    down_move = df["low"].shift(1) - df["low"]

    plus_dm = pd.Series(0.0, index=df.index)
    minus_dm = pd.Series(0.0, index=df.index)

    plus_dm[(up_move > down_move) & (up_move > 0)] = up_move
    minus_dm[(down_move > up_move) & (down_move > 0)] = down_move

    atr = tr.ewm(alpha=1.0 / period, adjust=False).mean()
    plus_di = 100.0 * (plus_dm.ewm(alpha=1.0 / period, adjust=False).mean() / (atr + 1e-9))
    minus_di = 100.0 * (minus_dm.ewm(alpha=1.0 / period, adjust=False).mean() / (atr + 1e-9))

    dx = 100.0 * ((plus_di - minus_di).abs() / (plus_di + minus_di + 1e-9))
    adx = dx.ewm(alpha=1.0 / period, adjust=False).mean()

    df["plus_di"] = plus_di
    df["minus_di"] = minus_di
    df["adx"] = adx
    return df


class AutoScanner:
    """Motor de análisis técnico cuantitativo institucional SMC / ICT."""

    def __init__(self, client):
        self.client = client
        benchmark_sentinel.set_client(client)
        self.is_running = False
        self.interval = config.AUTO_SCANNER_INTERVAL_SECONDS
        self._task: Optional[asyncio.Task] = None
        self.latest_scan_results: List[Dict[str, Any]] = []
        self.last_scan_time: str = ""
        self.is_scanning: bool = False

        # Restaurar modo persistente desde SQLite o valor default de config
        try:
            state = get_bot_state()
            self.scan_mode = state.get("scanner_mode") or getattr(config, "DEFAULT_SCANNER_MODE", "top_volatility")
        except Exception:
            self.scan_mode = getattr(config, "DEFAULT_SCANNER_MODE", "top_volatility")

    def set_mode(self, new_mode: str) -> str:
        """Cambia entre 'top_volatility', 'watchlist' y 'full_market' y lo persiste."""
        valid_modes = ["top_volatility", "watchlist", "full_market"]
        if new_mode in valid_modes:
            self.scan_mode = new_mode
            set_scanner_state(self.is_running, mode=new_mode)
            labels = {
                "top_volatility": "MÁXIMA VOLATILIDAD 24H (Top Movers BingX)",
                "watchlist": "SOLO WATCHLIST (Mi Lista)",
                "full_market": "TODO EL MERCADO BINGX (Top Volumen)"
            }
            mode_label = labels.get(new_mode, new_mode)
            add_system_log("INFO", f"Modo de Escáner actualizado a: {mode_label}")
        return self.scan_mode

    def get_checklist_summary(self) -> Dict[str, Any]:
        """Devuelve el estado consolidado del scanner, benchmarks de BTC/SP500 y todos los resultados."""
        results = self.latest_scan_results or []
        near_count = sum(1 for r in results if r.get("score", 0) >= 3)
        confirmed_count = sum(1 for r in results if r.get("verdict") == "CONFIRMED")
        benchmarks = benchmark_sentinel.update_regime()
        return {
            "scanner_active": self.is_running,
            "scanner_mode": self.scan_mode,
            "is_scanning": self.is_scanning,
            "last_scan_time": self.last_scan_time,
            "total_targets": len(results),
            "near_entry_count": near_count,
            "confirmed_count": confirmed_count,
            "benchmarks": benchmarks,
            "results": results
        }

    def start(self):
        """Inicia el bucle de escaneo y persiste el estado ON."""
        if not self.is_running:
            self.is_running = True
            set_scanner_state(True)
            self._task = asyncio.create_task(self._scan_loop())
            labels = {
                "top_volatility": "Máxima Volatilidad 24h",
                "watchlist": "Solo Watchlist",
                "full_market": "Top Volumen BingX"
            }
            mode_desc = labels.get(self.scan_mode, self.scan_mode)
            add_system_log("INFO", f"Auto-Scanner SMC (4H + 15m + 5m) INICIADO [ON] en modo: {mode_desc}.")

    def stop(self):
        """Detiene el auto-scanner y persiste el estado OFF."""
        if self.is_running:
            self.is_running = False
            set_scanner_state(False)
            if self._task and not self._task.done():
                self._task.cancel()
            add_system_log("INFO", "Auto-Scanner DETENIDO [OFF].")

    def toggle(self) -> bool:
        """Alterna el estado del scanner."""
        if self.is_running:
            self.stop()
        else:
            self.start()
        return self.is_running

    async def run_single_scan(self):
        """Ejecuta un ciclo completo de escaneo y actualiza el checklist en memoria."""
        if self.is_scanning:
            return
        self.is_scanning = True
        try:
            state = get_bot_state()
            is_bot_active = bool(state.get("is_active", True))
            is_scanner_active = bool(self.is_running and state.get("scanner_active", False))
            can_execute_orders = is_bot_active and is_scanner_active

            open_positions = await asyncio.to_thread(self.client.get_open_positions)
            await asyncio.to_thread(self.client.sync_closed_trades, open_positions)
            active_keys = set()
            for p in open_positions:
                sym = p.get("symbol", "")
                active_keys.add(sym.upper())
                clean_sym = sym.split(":")[0].replace("-", "/").upper()
                active_keys.add(clean_sym)
                base = clean_sym.split("/")[0].upper()
                active_keys.add(base)

            # Verificar saldo libre y Drawdown diario
            balance_info = await asyncio.to_thread(self.client.get_balance)
            free_usdt = float(balance_info.get("free", 0.0))
            total_usdt = float(balance_info.get("total", free_usdt))
            dd_status = check_daily_drawdown_and_reset(total_usdt)
            if dd_status.get("blocked"):
                can_execute_orders = False

            # Determinar lista de activos según el modo configurado
            targets = []
            if self.scan_mode == "top_volatility":
                min_vol = float(getattr(config, "MIN_24H_VOLUME_USDT", 3000000.0))
                volatile_pairs = await asyncio.to_thread(
                    self.client.get_market_symbols_by_volatility, 25, min_vol
                )
                for vp in volatile_pairs:
                    targets.append({
                        "raw_symbol": vp["ticker"],
                        "exchange_symbol": vp["symbol"],
                        "name": f"{vp['ticker']} (Vol: {vp.get('volatility_pct', 0)}%)"
                    })
            elif self.scan_mode == "watchlist":
                items = get_db_watchlist()
                if not items:
                    items = [
                        {"ticker": k, "name": v["name"], "symbol": v["symbol"]}
                        for k, v in ASSET_CATALOG.items()
                    ]
                for item in items:
                    targets.append({
                        "raw_symbol": item["ticker"],
                        "exchange_symbol": item["symbol"],
                        "name": item["name"]
                    })
            else:
                market_pairs = await asyncio.to_thread(self.client.get_market_symbols_by_volume, 25)
                for mp in market_pairs:
                    targets.append({
                        "raw_symbol": mp["ticker"],
                        "exchange_symbol": mp["symbol"],
                        "name": mp["ticker"]
                    })

            if targets:
                labels = {
                    "top_volatility": "Top Volatilidad 24h",
                    "watchlist": "Mi Watchlist",
                    "full_market": "Top Volumen BingX"
                }
                mode_label = labels.get(self.scan_mode, self.scan_mode)
                add_system_log("INFO", f"Radar SMC Activo: Analizando {len(targets)} contratos ({mode_label})...")

            batch_results = []
            for target in targets:
                if not self.is_running and not self.is_scanning:
                    break

                ex_symbol = target["exchange_symbol"]
                raw_sym = target["raw_symbol"]

                setup, checklist = await self._analyze_smc(ex_symbol, raw_sym)
                batch_results.append(checklist)
                self.latest_scan_results = sorted(batch_results, key=lambda x: x.get("score", 0), reverse=True)
                self.last_scan_time = datetime.now().strftime("%H:%M:%S")


                # Si hay señal y tenemos cupo, ejecutar orden
                target_clean = raw_sym.split(":")[0].replace("-", "/").upper()
                target_base = target_clean.split("/")[0].upper()
                is_already_open = ex_symbol.upper() in active_keys or target_clean in active_keys or target_base in active_keys
                has_capacity = len(open_positions) < config.MAX_SIMULTANEOUS_POSITIONS
                cooldown_info = get_symbol_cooldown(raw_sym, is_demo=self.client.is_demo)
                is_in_cooldown = cooldown_info.get("in_cooldown", False)

                if setup and not is_already_open and not is_in_cooldown and has_capacity and free_usdt > 0.15:
                    if not can_execute_orders:
                        # Modo Radar / Scanner OFF: Solo actualizamos el checklist informativo, NO abrimos órdenes
                        continue

                    sig_side = setup["signal"]

                    # Validación de Centinela Maestro de Régimen (BTC / S&P 500)
                    b_allowed, b_reason = benchmark_sentinel.is_entry_allowed(raw_sym, sig_side)
                    if not b_allowed:
                        add_system_log("WARNING", f"⏸️ Orden en {raw_sym} bloqueada por Centinela: {b_reason}")
                        continue

                    sl_px = setup["sl_price"]
                    tp_px = setup["tp_price"]
                    pat = setup["pattern"]

                    add_system_log(
                        "INFO",
                        f"⚡ SEÑAL SMC CONFIRMADA: {sig_side} en {raw_sym} ({pat}) | "
                        f"SL Técnico: ${sl_px} | TP: ${tp_px}"
                    )

                    try:
                        order_res = await asyncio.to_thread(
                            self.client.open_bracket_position,
                            raw_symbol=raw_sym,
                            side=sig_side,
                            custom_sl_price=sl_px,
                            custom_tp_price=tp_px
                        )

                        active_keys.add(ex_symbol.upper())
                        active_keys.add(target_clean)
                        active_keys.add(target_base)
                        open_positions.append({"symbol": ex_symbol})

                        notifier.notify_smc_signal(
                            symbol=raw_sym,
                            side=sig_side,
                            pattern=pat,
                            level_type=setup["level_type"],
                            level_price=setup["level_price"],
                            sl=sl_px,
                            tp=tp_px,
                            leverage=order_res.get("leverage", 10),
                            is_demo=self.client.is_demo
                        )
                    except Exception as exec_err:
                        err_msg = str(exec_err)
                        if "Insufficient margin" in err_msg or "Margen libre insuficiente" in err_msg:
                            add_system_log(
                                "WARNING",
                                f"⏸️ Señal en {raw_sym}: El margen libre disponible (${free_usdt:.2f} USDT) no alcanzó para cubrir el tamaño de lote y comisiones exigidas por BingX."
                            )
                        else:
                            add_system_log("ERROR", f"Fallo al ejecutar señal SMC en {raw_sym}: {exec_err}")

                await asyncio.sleep(0.15)

            # Ordenar resultados: mayor puntaje primero
            batch_results.sort(key=lambda x: x.get("score", 0), reverse=True)
            self.latest_scan_results = batch_results
            self.last_scan_time = datetime.now().strftime("%H:%M:%S")

        except Exception as loop_err:
            add_system_log("DEBUG", f"Aviso en escaneo de Radar SMC: {loop_err}")
        finally:
            self.is_scanning = False

    async def _scan_loop(self):
        """Bucle principal de escaneo periódico."""
        while self.is_running:
            try:
                await self.run_single_scan()
            except asyncio.CancelledError:
                break
            except Exception as e:
                add_system_log("DEBUG", f"Error en _scan_loop: {e}")

            await asyncio.sleep(self.interval)

    async def _analyze_smc(self, exchange_symbol: str, raw_symbol: str) -> Tuple[Optional[Dict[str, Any]], Dict[str, Any]]:
        """
        Ejecución Cuantitativa Multi-Temporalidad Institucional SMC / ICT:
        1. Macro 4H: Dirección con EMA 50.
        2. Marco 15m: Barridas de Liquidez (PDH/PDL/Swings) + quiebre iCHoCH + Filtro ADX.
        3. Marco 5m: Gatillo de Rechazo y cálculo de SL técnico ceñido a la mecha.
        Retorna (setup_para_ejecutar, checklist_detallado).
        """
        checklist: Dict[str, Any] = {
            "symbol": raw_symbol,
            "exchange_symbol": exchange_symbol,
            "price": 0.0,
            "macro_trend": "NEUTRAL",
            "macro_close": 0.0,
            "macro_ema": 0.0,
            "adx_val": 0.0,
            "adx_passed": False,
            "sweep_low": False,
            "sweep_high": False,
            "sweep_level": None,
            "sweep_price": 0.0,
            "ichoch_bull": False,
            "ichoch_bear": False,
            "ichoch_passed": False,
            "trigger_5m": False,
            "score": 0,
            "in_cooldown": False,
            "cooldown_min": 0,
            "benchmark_blocked": False,
            "benchmark_reason": "",
            "verdict": "ERROR",
            "verdict_desc": "Datos insuficientes",
            "signal": None
        }

        try:
            # 1. Obtener Velas de 4H (Tendencia Directriz Institucional y Madurez)
            min_age_days = int(getattr(config, "MIN_MARKET_AGE_DAYS", 15))
            min_required_4h = min_age_days * 6 # 6 velas 4H por día = 90 velas para 15 días

            ohlcv_4h = await asyncio.to_thread(
                self.client.exchange.fetch_ohlcv,
                exchange_symbol,
                timeframe="4h",
                limit=100
            )
            if not ohlcv_4h or len(ohlcv_4h) < min_required_4h:
                days_hist = len(ohlcv_4h) // 6 if ohlcv_4h else 0
                checklist["verdict"] = "NEW_TOKEN"
                checklist["verdict_desc"] = f"🚫 Activo recién listado ({days_hist}d de historial < {min_age_days}d mín)"
                return None, checklist

            df_4h = pd.DataFrame(ohlcv_4h, columns=["timestamp", "open", "high", "low", "close", "volume"])

            # Filtro Cuantitativo de Volatilidad 24h (Anti-Hipervolatilidad y Anti-Estancamiento)
            max_vol = float(getattr(config, "MAX_24H_VOLATILITY_PCT", 16.0))
            min_vol = float(getattr(config, "MIN_24H_VOLATILITY_PCT", 3.0))
            recent_4h = df_4h.tail(6)
            if len(recent_4h) >= 6:
                h_24h = float(recent_4h["high"].max())
                l_24h = float(recent_4h["low"].min())
                if l_24h > 0:
                    vol_24h = round(((h_24h - l_24h) / l_24h) * 100.0, 1)
                    checklist["volatility_24h"] = vol_24h
                    if vol_24h > max_vol:
                        checklist["verdict"] = "HYPER_VOLATILE"
                        checklist["verdict_desc"] = f"🚫 Hiper-volátil ({vol_24h}% 24h > {max_vol:.0f}% máx)"
                        return None, checklist
                    if vol_24h < min_vol:
                        checklist["verdict"] = "STAGNANT"
                        checklist["verdict_desc"] = f"🚫 Mercado estancado ({vol_24h}% 24h < {min_vol:.1f}% mín)"
                        return None, checklist

            macro_trend = detect_macro_trend_4h(df_4h, ema_period=50)
            df_4h_ema = df_4h["close"].ewm(span=50, adjust=False).mean()
            last_4h_close = float(df_4h["close"].iloc[-1])
            last_4h_ema = float(df_4h_ema.iloc[-1])

            checklist["macro_trend"] = macro_trend or "NEUTRAL"
            checklist["macro_close"] = round(last_4h_close, 4)
            checklist["macro_ema"] = round(last_4h_ema, 4)

            # 2. Obtener Velas de 15m (Estructura, Barridas y ChoCH)
            ohlcv_15m = await asyncio.to_thread(
                self.client.exchange.fetch_ohlcv,
                exchange_symbol,
                timeframe="15m",
                limit=80
            )
            if not ohlcv_15m or len(ohlcv_15m) < 30:
                checklist["verdict_desc"] = "Historial 15m insuficiente"
                return None, checklist
            df_15 = pd.DataFrame(ohlcv_15m, columns=["timestamp", "open", "high", "low", "close", "volume"])

            # Filtro ADX
            adx_enabled = getattr(config, "ADX_FILTER_ENABLED", True)
            adx_threshold = float(getattr(config, "ADX_MIN_THRESHOLD", 22.0))
            adx_val = 0.0
            adx_passed = True
            if adx_enabled:
                df_15 = calculate_adx(df_15, period=14)
                adx_val = round(float(df_15.iloc[-1].get("adx", 0.0)), 1)
                adx_passed = bool(adx_val >= adx_threshold)

            checklist["adx_val"] = float(adx_val)
            checklist["adx_passed"] = bool(adx_passed)

            # 3. Obtener Velas de 5m (Gatillo de Entrada)
            ohlcv_5m = await asyncio.to_thread(
                self.client.exchange.fetch_ohlcv,
                exchange_symbol,
                timeframe="5m",
                limit=60
            )
            if not ohlcv_5m or len(ohlcv_5m) < 25:
                checklist["verdict_desc"] = "Historial 5m insuficiente"
                return None, checklist
            df_5 = pd.DataFrame(ohlcv_5m, columns=["timestamp", "open", "high", "low", "close", "volume"])

            curr_price = float(df_5["close"].iloc[-1])
            checklist["price"] = round(curr_price, 4)

            # Estructura 15m, Barridas de Liquidez y Ruptura por Cuerpo (Toni Maura)
            tol_pct = float(getattr(config, "SMC_TOLERANCE_PCT", 0.08))
            risk_ratio = float(getattr(config, "DEFAULT_RISK_RATIO", 2.0))
            min_sl_pct = float(getattr(config, "MIN_SL_PCT", 0.8))
            levels = calculate_liquidity_levels(df_15, lookback=12, exclude_recent=8)
            sweeps = detect_liquidity_sweeps(df_15, levels, tolerance_pct=tol_pct, lookback_candles=10)
            brk_15m = detect_body_structure_break(df_15, lookback=6, recent_window=8, min_body_ratio=0.40)
            brk_5m = detect_body_structure_break(df_5, lookback=8, recent_window=14, min_body_ratio=0.40)

            sweep_low = bool(sweeps.get("sweep_low", False))
            sweep_high = bool(sweeps.get("sweep_high", False))
            checklist["sweep_low"] = sweep_low
            checklist["sweep_high"] = sweep_high
            checklist["sweep_level"] = str(sweeps.get("level_type")) if sweeps.get("level_type") else None
            checklist["sweep_price"] = round(float(sweeps.get("level_price", 0.0)), 4)

            bull_body_break = bool(brk_15m["bull_break"] or brk_5m["bull_break"])
            bear_body_break = bool(brk_15m["bear_break"] or brk_5m["bear_break"])
            checklist["ichoch_bull"] = bull_body_break
            checklist["ichoch_bear"] = bear_body_break

            is_ich_valid = False
            if macro_trend == "BULL":
                is_ich_valid = bull_body_break
            elif macro_trend == "BEAR":
                is_ich_valid = bear_body_break
            checklist["ichoch_passed"] = bool(is_ich_valid)

            # FVG + Displacement en 5m alineado a la dirección macro
            fvg_info = None
            if macro_trend in ("BULL", "BEAR"):
                fvg_info = detect_fvg_with_displacement(df_5, direction=macro_trend)
            checklist["fvg_detected"] = bool(fvg_info is not None)
            checklist["displacement_atr"] = fvg_info["displacement_atr"] if fvg_info else 0.0

            # Gatillo 5m
            curr_5 = df_5.iloc[-1]
            is_trigger = False
            if macro_trend == "BULL":
                is_trigger = bool(float(curr_5["close"]) > float(curr_5["open"]))
            elif macro_trend == "BEAR":
                is_trigger = bool(float(curr_5["close"]) < float(curr_5["open"]))
            checklist["trigger_5m"] = bool(is_trigger)

            # Evaluar configuración completa con el motor matemático Toni Maura
            setup = None
            if adx_passed and macro_trend in ("BULL", "BEAR"):
                setup = evaluate_smc_setup(
                    df_4h=df_4h,
                    df_15m=df_15,
                    df_5m=df_5,
                    tolerance_pct=tol_pct,
                    risk_ratio=risk_ratio,
                    min_sl_pct=min_sl_pct
                )

            # Alineación direccional institucional (Sweep o Continuación con Body Break)
            is_buy_candidate = (macro_trend == "BULL") and (sweep_low or bull_body_break)
            is_sell_candidate = (macro_trend == "BEAR") and (sweep_high or bear_body_break)

            # Puntaje real (0 a 5) según pilares Toni Maura
            score = 0
            if macro_trend in ("BULL", "BEAR"):
                score += 1
            if adx_passed:
                score += 1
            if is_buy_candidate or is_sell_candidate:
                score += 1
            if is_ich_valid:
                score += 1
            if fvg_info is not None and is_trigger:
                score += 1
            checklist["score"] = int(score)

            # Diagnóstico legible y coherente
            cooldown_info = get_symbol_cooldown(raw_symbol, is_demo=self.client.is_demo)
            if cooldown_info.get("in_cooldown"):
                checklist["in_cooldown"] = True
                checklist["cooldown_min"] = int(cooldown_info["remaining_minutes"])
                checklist["verdict"] = "COOLDOWN"
                checklist["verdict_desc"] = f"⏳ {cooldown_info['reason']}"
                setup = None
            elif macro_trend not in ("BULL", "BEAR"):
                checklist["verdict"] = "MACRO_NEUTRAL"
                checklist["verdict_desc"] = "4H sin tendencia clara (EMA50 plana/neutral)"
            elif not adx_passed:
                checklist["verdict"] = "ADX_LOW"
                checklist["verdict_desc"] = f"ADX {adx_val} < {adx_threshold} (Mercado lateral)"
            elif not is_ich_valid:
                checklist["verdict"] = "WAITING_ICHOCH"
                dir_txt = "alcista" if macro_trend == "BULL" else "bajista"
                checklist["verdict_desc"] = f"4H {macro_trend}: Esperando ruptura con CUERPO {dir_txt}"
            elif fvg_info is None:
                checklist["verdict"] = "WAITING_FVG"
                checklist["verdict_desc"] = "Ruptura confirmada. Esperando Displacement + FVG limpio en 5m"
            elif not is_trigger:
                checklist["verdict"] = "WAITING_TRIGGER"
                checklist["verdict_desc"] = f"FVG listo ({fvg_info['displacement_atr']}x ATR). Esperando mitigación y vela gatillo 5m"
            elif setup is not None:
                sig = setup["signal"]
                b_allowed, b_reason = benchmark_sentinel.is_entry_allowed(raw_symbol, sig)
                if not b_allowed:
                    checklist["benchmark_blocked"] = True
                    checklist["benchmark_reason"] = b_reason
                    checklist["verdict"] = "BENCHMARK_BLOCKED"
                    checklist["verdict_desc"] = f"🚫 {b_reason}"
                    setup = None
                else:
                    checklist["signal"] = sig
                    checklist["verdict"] = "CONFIRMED"
                    checklist["verdict_desc"] = f"🚀 ¡SEÑAL {sig} TONI MAURA CONFIRMADA!"
            else:
                cand_side = "BUY" if is_buy_candidate else ("SELL" if is_sell_candidate else None)
                if cand_side:
                    b_allowed, b_reason = benchmark_sentinel.is_entry_allowed(raw_symbol, cand_side)
                    if not b_allowed:
                        checklist["benchmark_blocked"] = True
                        checklist["benchmark_reason"] = b_reason

                checklist["verdict"] = "WAITING"
                checklist["verdict_desc"] = "Esperando retroceso exacto al FVG o filtro SL/R:R"

            return setup, checklist


        except Exception as e:
            checklist["verdict_desc"] = f"Error al procesar: {str(e)[:40]}"
            return None, checklist

