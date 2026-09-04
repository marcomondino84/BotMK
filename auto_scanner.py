"""
Scanner Cuantitativo Autónomo Multi-Temporalidad (MTF).
Analiza velas de 15 minutos (Tendencia Macro con EMA 21/50 + RSI)
y velas de 5 minutos (Gatillo de Entrada con Cruce EMA 9/21).
"""

import asyncio
import pandas as pd
from typing import Optional, Dict
from config import config, ASSET_CATALOG
from database import get_bot_state, set_scanner_state, add_system_log, get_db_watchlist


class AutoScanner:
    """Motor de análisis técnico cuantitativo multi-temporalidad (5m y 15m)."""

    def __init__(self, client):
        self.client = client
        self.is_running = False
        self.interval = config.AUTO_SCANNER_INTERVAL_SECONDS
        self._task: Optional[asyncio.Task] = None
        
        # Restaurar modo persistente desde SQLite
        try:
            state = get_bot_state()
            self.scan_mode = state.get("scanner_mode") or "watchlist"
        except Exception:
            self.scan_mode = "watchlist"

    def set_mode(self, new_mode: str) -> str:
        """Cambia entre modo 'watchlist' y 'full_market' y lo persiste en la BD."""
        if new_mode in ["watchlist", "full_market"]:
            self.scan_mode = new_mode
            set_scanner_state(self.is_running, mode=new_mode)
            mode_label = "SOLO WATCHLIST (Mi Lista)" if new_mode == "watchlist" else "TODO EL MERCADO BINGX (Top 40 Volumen)"
            add_system_log("INFO", f"Modo de Escáner actualizado a: {mode_label}")
        return self.scan_mode

    def start(self):
        """Inicia el bucle de escaneo y persiste el estado ON."""
        if not self.is_running:
            self.is_running = True
            set_scanner_state(True)
            self._task = asyncio.create_task(self._scan_loop())
            mode_desc = "Solo Watchlist" if self.scan_mode == "watchlist" else "Todo el Mercado BingX"
            add_system_log("INFO", f"Auto-Scanner MTF (5m + 15m) INICIADO [ON] en modo: {mode_desc}.")

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
        from database import get_db_watchlist
        while self.is_running:
            try:
                state = get_bot_state()
                if not state["is_active"]:
                    await asyncio.sleep(self.interval)
                    continue

                open_positions = await asyncio.to_thread(self.client.get_open_positions)
                
                # Conjunto de normalización para evitar estrictamente duplicados en el mismo activo
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
                if self.scan_mode == "watchlist":
                    # Escanear activos de la base de datos (Watchlist del usuario)
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
                    # Escanear los 25 pares más líquidos de BingX
                    market_pairs = await asyncio.to_thread(self.client.get_market_symbols_by_volume, 25)
                    for mp in market_pairs:
                        targets.append({
                            "raw_symbol": mp["ticker"],
                            "exchange_symbol": mp["symbol"],
                            "name": mp["ticker"]
                        })

                if targets:
                    mode_label = "Mi Watchlist" if self.scan_mode == "watchlist" else "Top Mercado BingX"
                    add_system_log("INFO", f"Radar MTF activo: Escaneando {len(targets)} contratos ({mode_label})...")

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

                    signal = await self._analyze_mtf(ex_symbol)
                    if signal:
                        add_system_log(
                            "INFO",
                            f"⚡ SEÑAL MTF (15m + 5m): {signal} confirmada en {raw_sym} ({target['name']})!"
                        )
                        try:
                            await asyncio.to_thread(
                                self.client.open_bracket_position,
                                raw_symbol=raw_sym,
                                side=signal
                            )
                            active_keys.add(ex_symbol.upper())
                            active_keys.add(target_clean)
                            active_keys.add(target_base)
                            open_positions.append({"symbol": ex_symbol})
                        except Exception as exec_err:
                            add_system_log("ERROR", f"Fallo al ejecutar señal MTF en {raw_sym}: {exec_err}")

                    await asyncio.sleep(0.3)

            except asyncio.CancelledError:
                break
            except Exception as loop_err:
                add_system_log("DEBUG", f"Aviso en ciclo de Auto-Scanner: {loop_err}")

            await asyncio.sleep(self.interval)

    async def _analyze_mtf(self, exchange_symbol: str) -> Optional[str]:
        """
        Análisis Cuantitativo Multi-Temporalidad Institucional:
        1. Marco 15m: Filtro de Tendencia Macro (EMA 21/50 y RSI 14).
        2. Marco 5m: Gatillo de Cruce reciente (últimos 15 min) o Momentum confirmado.
        """
        try:
            # --- PASO 1: MARCO DE 15 MINUTOS (TENDENCIA DOMINANTE) ---
            ohlcv_15m = await asyncio.to_thread(
                self.client.exchange.fetch_ohlcv,
                exchange_symbol,
                timeframe="15m",
                limit=50
            )
            if not ohlcv_15m or len(ohlcv_15m) < 30:
                return None

            df_15 = pd.DataFrame(ohlcv_15m, columns=["timestamp", "open", "high", "low", "close", "volume"])
            df_15["ema_21"] = df_15["close"].ewm(span=21, adjust=False).mean()
            df_15["ema_50"] = df_15["close"].ewm(span=50, adjust=False).mean()

            # RSI en 15m
            delta_15 = df_15["close"].diff()
            gain_15 = (delta_15.where(delta_15 > 0, 0)).rolling(window=14).mean()
            loss_15 = (-delta_15.where(delta_15 < 0, 0)).rolling(window=14).mean()
            rs_15 = gain_15 / (loss_15 + 1e-9)
            df_15["rsi"] = 100 - (100 / (1 + rs_15))

            c15 = df_15.iloc[-1]

            # Tendencia macro estructurada en 15m
            is_bullish_15m = (c15["ema_21"] > c15["ema_50"] or c15["close"] > c15["ema_21"]) and c15["rsi"] >= 48
            is_bearish_15m = (c15["ema_21"] < c15["ema_50"] or c15["close"] < c15["ema_21"]) and c15["rsi"] <= 52

            if not is_bullish_15m and not is_bearish_15m:
                return None

            # --- PASO 2: MARCO DE 5 MINUTOS (GATILLO / TIMING) ---
            ohlcv_5m = await asyncio.to_thread(
                self.client.exchange.fetch_ohlcv,
                exchange_symbol,
                timeframe="5m",
                limit=35
            )
            if not ohlcv_5m or len(ohlcv_5m) < 20:
                return None

            df_5 = pd.DataFrame(ohlcv_5m, columns=["timestamp", "open", "high", "low", "close", "volume"])
            df_5["ema_9"] = df_5["close"].ewm(span=9, adjust=False).mean()
            df_5["ema_21"] = df_5["close"].ewm(span=21, adjust=False).mean()

            curr_5 = df_5.iloc[-1]

            # Cruce en las últimas 3 velas de 5m (últimos 15 minutos)
            recent_cross_buy = any(
                df_5.iloc[i-1]["ema_9"] <= df_5.iloc[i-1]["ema_21"] and df_5.iloc[i]["ema_9"] > df_5.iloc[i]["ema_21"]
                for i in range(-3, 0)
            )
            recent_cross_sell = any(
                df_5.iloc[i-1]["ema_9"] >= df_5.iloc[i-1]["ema_21"] and df_5.iloc[i]["ema_9"] < df_5.iloc[i]["ema_21"]
                for i in range(-3, 0)
            )

            # Momentum limpio activo a favor de la tendencia
            active_bull_5m = curr_5["ema_9"] > curr_5["ema_21"] and curr_5["close"] > curr_5["ema_9"]
            active_bear_5m = curr_5["ema_9"] < curr_5["ema_21"] and curr_5["close"] < curr_5["ema_9"]

            if is_bullish_15m and (recent_cross_buy or active_bull_5m):
                return "BUY"

            if is_bearish_15m and (recent_cross_sell or active_bear_5m):
                return "SELL"

            return None

        except Exception:
            return None
