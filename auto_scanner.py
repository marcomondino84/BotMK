"""
Scanner Cuantitativo Autónomo Multi-Temporalidad Institucional (SMC / ICT):
1. Macro 4H: Filtro directriz con EMA 50 (Solo Longs si Close > EMA50, Solo Shorts si Close < EMA50).
2. Marco 15m: Detección de Barridas de Liquidez (Sweeps de PDH/PDL o Swings fractales) + Cambio Estructural (iCHoCH).
3. Marco 5m: Gatillo de Confirmación por Vela de Rechazo y cálculo de SL técnico ceñido a la mecha.
4. Screener Dinámico de Alta Volatilidad (Top Movers 24h) para máxima actividad y liquidez.
"""

import asyncio
import pandas as pd
from typing import Optional, Dict, Any
from config import config, ASSET_CATALOG
from database import get_bot_state, set_scanner_state, add_system_log, get_db_watchlist
from notifications import notifier
from src.strategies.smc_strategy import evaluate_smc_setup


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
        self.is_running = False
        self.interval = config.AUTO_SCANNER_INTERVAL_SECONDS
        self._task: Optional[asyncio.Task] = None

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

    async def _scan_loop(self):
        """Bucle principal de escaneo periódico."""
        while self.is_running:
            try:
                state = get_bot_state()
                if not state["is_active"]:
                    await asyncio.sleep(self.interval)
                    continue

                open_positions = await asyncio.to_thread(self.client.get_open_positions)

                # Conjunto de normalización para evitar duplicados en el mismo activo
                active_keys = set()
                for p in open_positions:
                    sym = p.get("symbol", "")
                    active_keys.add(sym.upper())
                    clean_sym = sym.split(":")[0].replace("-", "/").upper()
                    active_keys.add(clean_sym)
                    base = clean_sym.split("/")[0].upper()
                    active_keys.add(base)

                # Determinar lista de activos según el modo configurado
                targets = []
                if self.scan_mode == "top_volatility":
                    # MODO MÁXIMA ACTIVIDAD: Pares con mayor rango de volatilidad diaria y volumen real
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
                    # Escanear activos de la Watchlist del usuario
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
                    # Modo full_market: Top por volumen simple
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

                for target in targets:
                    if not self.is_running:
                        break

                    ex_symbol = target["exchange_symbol"]
                    raw_sym = target["raw_symbol"]

                    target_clean = raw_sym.split(":")[0].replace("-", "/").upper()
                    target_base = target_clean.split("/")[0].upper()

                    # Regla estricta: No duplicar posición en el mismo activo
                    if ex_symbol.upper() in active_keys or target_clean in active_keys or target_base in active_keys:
                        continue

                    # Respetar límite configurado de posiciones simultáneas
                    if len(open_positions) >= config.MAX_SIMULTANEOUS_POSITIONS:
                        break

                    setup = await self._analyze_smc(ex_symbol)
                    if setup:
                        sig_side = setup["signal"]
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

                            # Notificación detallada por Telegram
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
                            add_system_log("ERROR", f"Fallo al ejecutar señal SMC en {raw_sym}: {exec_err}")

                    await asyncio.sleep(0.3)

            except asyncio.CancelledError:
                break
            except Exception as loop_err:
                add_system_log("DEBUG", f"Aviso en ciclo de Auto-Scanner: {loop_err}")

            await asyncio.sleep(self.interval)

    async def _analyze_smc(self, exchange_symbol: str) -> Optional[Dict[str, Any]]:
        """
        Ejecución Cuantitativa Multi-Temporalidad Institucional SMC / ICT:
        1. Macro 4H: Dirección con EMA 50.
        2. Marco 15m: Barridas de Liquidez (PDH/PDL/Swings) + quiebre iCHoCH + Filtro ADX.
        3. Marco 5m: Gatillo de Rechazo y cálculo de SL técnico ceñido a la mecha.
        """
        try:
            # 1. Obtener Velas de 4H (Tendencia Directriz Institucional)
            ohlcv_4h = await asyncio.to_thread(
                self.client.exchange.fetch_ohlcv,
                exchange_symbol,
                timeframe="4h",
                limit=60
            )
            if not ohlcv_4h or len(ohlcv_4h) < 45:
                return None
            df_4h = pd.DataFrame(ohlcv_4h, columns=["timestamp", "open", "high", "low", "close", "volume"])

            # 2. Obtener Velas de 15m (Estructura, Barridas y ChoCH)
            ohlcv_15m = await asyncio.to_thread(
                self.client.exchange.fetch_ohlcv,
                exchange_symbol,
                timeframe="15m",
                limit=80
            )
            if not ohlcv_15m or len(ohlcv_15m) < 30:
                return None
            df_15 = pd.DataFrame(ohlcv_15m, columns=["timestamp", "open", "high", "low", "close", "volume"])

            # Filtro opcional Anti-Lateralidad (ADX > 22 en 15m)
            adx_enabled = getattr(config, "ADX_FILTER_ENABLED", True)
            adx_threshold = float(getattr(config, "ADX_MIN_THRESHOLD", 22.0))
            if adx_enabled:
                df_15 = calculate_adx(df_15, period=14)
                last_adx = float(df_15.iloc[-1].get("adx", 0.0))
                if last_adx < adx_threshold:
                    return None

            # 3. Obtener Velas de 5m (Gatillo de Entrada)
            ohlcv_5m = await asyncio.to_thread(
                self.client.exchange.fetch_ohlcv,
                exchange_symbol,
                timeframe="5m",
                limit=30
            )
            if not ohlcv_5m or len(ohlcv_5m) < 15:
                return None
            df_5 = pd.DataFrame(ohlcv_5m, columns=["timestamp", "open", "high", "low", "close", "volume"])

            # Evaluar configuración con el motor matemático SMC
            tol_pct = float(getattr(config, "SMC_TOLERANCE_PCT", 0.08))
            risk_ratio = float(getattr(config, "DEFAULT_RISK_RATIO", 1.5))

            setup = evaluate_smc_setup(
                df_4h=df_4h,
                df_15m=df_15,
                df_5m=df_5,
                tolerance_pct=tol_pct,
                risk_ratio=risk_ratio
            )
            return setup

        except Exception:
            return None
