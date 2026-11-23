"""
Centinela Cuantitativo de Régimen de Mercado (Bitcoin & S&P 500).
Monitorea la tendencia y momentum de los índices directores globales para evitar
operar en contra de la marea macro:
1. Bitcoin (BTC/USDT): Rector de criptomonedas. Si BTC entra en corrección en 15m,
   bloquea automáticamente todas las compras (LONGS) en altcoins. Si está en impulso,
   bloquea ventas (SHORTS).
2. S&P 500 (NCSISP5002USD/USDT): Rector de índices bursátiles y apetito por riesgo global.
   Si el SP500 entra en corrección, bloquea compras en índices y acciones.
"""

import time
import asyncio
from typing import Dict, Any, Tuple, Optional
import pandas as pd
from config import config, ASSET_CATALOG, TICKER_ALIASES


class BenchmarkSentinel:
    def __init__(self, client=None):
        self.client = client
        self._cache: Dict[str, Any] = {}
        self._cache_timestamp = 0.0
        self._cache_ttl = 45.0  # Segundos de validez de caché

        # Símbolos oficiales en BingX Swap
        self.btc_symbol = "BTC/USDT:USDT"
        self.sp500_symbol = "NCSISP5002USD/USDT:USDT"

    def set_client(self, client):
        self.client = client

    def _determine_category(self, raw_symbol: str) -> str:
        """Determina si un activo pertenece a Cripto, Índices, Acciones o Commodities."""
        sym_upper = raw_symbol.upper()
        # Prefijos estándar de BingX para contratos tradicionales
        if sym_upper.startswith("NCSI") or "SP500" in sym_upper or "NASDAQ" in sym_upper or "DOW" in sym_upper:
            return "index"
        if sym_upper.startswith("NCSK"):
            return "stock"
        if sym_upper.startswith("NCCO"):
            return "commodity"

        clean = raw_symbol.split(":")[0].replace("-", "/").upper()
        base = clean.split("/")[0].upper()
        alias_key = TICKER_ALIASES.get(base, base)

        if alias_key in ASSET_CATALOG:
            cat = ASSET_CATALOG[alias_key].get("category", "")
            if cat:
                return cat

        if base in ["SP500", "SPX500", "NASDAQ", "NDX", "DOW", "DJI"]:
            return "index"
        if base in ["TSLA", "NVDA", "AAPL", "MSFT", "AMZN"]:
            return "stock"
        if base in ["GOLD", "OIL", "WTI", "CRUDE", "XAU"]:
            return "commodity"

        # Por defecto, cualquier par de intercambio cripto es Criptomoneda
        return "crypto"

    def update_regime(self) -> Dict[str, Any]:
        """
        Consulta las velas de BTC y SP500 y calcula su régimen técnico (CORRECTION, BULL_IMPULSE, NEUTRAL).
        Ejecución síncrona / threaded.
        """
        now = time.time()
        if self._cache and (now - self._cache_timestamp) < self._cache_ttl:
            return self._cache

        if not self.client or not getattr(self.client, "exchange", None):
            return self._cache or self._default_state()

        timeframe = getattr(config, "BENCHMARK_TIMEFRAME", "15m")
        fast_period = int(getattr(config, "BENCHMARK_EMA_FAST", 20))
        slow_period = int(getattr(config, "BENCHMARK_EMA_SLOW", 50))

        results = {
            "timestamp": now,
            "timeframe": timeframe,
            "btc": self._analyze_symbol(self.btc_symbol, timeframe, fast_period, slow_period, "Bitcoin"),
            "sp500": self._analyze_symbol(self.sp500_symbol, timeframe, fast_period, slow_period, "S&P 500")
        }

        self._cache = results
        self._cache_timestamp = now
        return results

    async def update_regime_async(self) -> Dict[str, Any]:
        """Versión asíncrona no bloqueante de update_regime."""
        return await asyncio.to_thread(self.update_regime)

    def _analyze_symbol(
        self,
        exchange_symbol: str,
        timeframe: str,
        fast_p: int,
        slow_p: int,
        label: str
    ) -> Dict[str, Any]:
        """Descarga velas y determina si el benchmark está en corrección o impulso."""
        state = {
            "label": label,
            "symbol": exchange_symbol,
            "price": 0.0,
            "ema_fast": 0.0,
            "ema_slow": 0.0,
            "regime": "NEUTRAL",
            "desc": "Sin datos",
            "longs_allowed": True,
            "shorts_allowed": True
        }

        try:
            ohlcv = self.client.exchange.fetch_ohlcv(exchange_symbol, timeframe=timeframe, limit=60)
            if not ohlcv or len(ohlcv) < slow_p:
                state["desc"] = "Velas insuficientes"
                return state

            df = pd.DataFrame(ohlcv, columns=["timestamp", "open", "high", "low", "close", "volume"])
            df["ema_fast"] = df["close"].ewm(span=fast_p, adjust=False).mean()
            df["ema_slow"] = df["close"].ewm(span=slow_p, adjust=False).mean()

            last = df.iloc[-1]
            close_px = float(last["close"])
            ema_f = float(last["ema_fast"])
            ema_s = float(last["ema_slow"])

            state["price"] = round(close_px, 2)
            state["ema_fast"] = round(ema_f, 2)
            state["ema_slow"] = round(ema_s, 2)

            # Régimen: Corrección si el precio está por debajo de ambas EMAs
            if close_px < ema_f and close_px < ema_s:
                state["regime"] = "CORRECTION"
                state["desc"] = f"🔴 Corrección ({timeframe} < EMA{fast_p})"
                state["longs_allowed"] = False
                state["shorts_allowed"] = True
            # Impulso alcista si el precio está por encima de ambas EMAs
            elif close_px > ema_f and close_px > ema_s:
                state["regime"] = "BULL_IMPULSE"
                state["desc"] = f"🟢 Impulso Alcista ({timeframe} > EMA{fast_p})"
                state["longs_allowed"] = True
                state["shorts_allowed"] = False
            else:
                state["regime"] = "NEUTRAL"
                state["desc"] = "⚪ Zona Neutra / Transición"
                state["longs_allowed"] = True
                state["shorts_allowed"] = True

            return state

        except Exception as err:
            state["desc"] = f"Error: {str(err)[:30]}"
            return state

    def is_entry_allowed(self, raw_symbol: str, side: str) -> Tuple[bool, str]:
        """
        Valida si la entrada propuesta está autorizada por el Centinela Maestro.
        Retorna (autorizado: bool, motivo: str).
        """
        side_upper = side.upper()
        category = self._determine_category(raw_symbol)
        data = self.update_regime()

        # 1. Regla para Criptomonedas (Gobernadas por Bitcoin)
        if category in ["crypto", "crypto_major", "crypto_alt"]:
            if getattr(config, "BTC_SENTINEL_ENABLED", True):
                btc = data.get("btc", {})
                regime = btc.get("regime", "NEUTRAL")
                btc_price = btc.get("price", 0.0)
                tf = data.get("timeframe", "15m")

                if side_upper in ["BUY", "LONG"] and regime == "CORRECTION":
                    return (
                        False,
                        f"Filtro Maestro BTC: Bitcoin en corrección (${btc_price:,.0f} < EMA {tf}). Compras pausadas."
                    )
                if side_upper in ["SELL", "SHORT"] and regime == "BULL_IMPULSE":
                    return (
                        False,
                        f"Filtro Maestro BTC: Bitcoin en impulso alcista (${btc_price:,.0f} > EMA {tf}). Ventas pausadas."
                    )

        # 2. Regla para Índices y Acciones (Gobernadas por S&P 500)
        elif category in ["index", "stock"]:
            if getattr(config, "SP500_SENTINEL_ENABLED", True):
                sp = data.get("sp500", {})
                regime = sp.get("regime", "NEUTRAL")
                sp_price = sp.get("price", 0.0)
                tf = data.get("timeframe", "15m")

                if side_upper in ["BUY", "LONG"] and regime == "CORRECTION":
                    return (
                        False,
                        f"Filtro Maestro SP500: S&P 500 en corrección (${sp_price:,.0f} < EMA {tf}). Compras pausadas."
                    )
                if side_upper in ["SELL", "SHORT"] and regime == "BULL_IMPULSE":
                    return (
                        False,
                        f"Filtro Maestro SP500: S&P 500 en impulso alcista (${sp_price:,.0f} > EMA {tf}). Ventas pausadas."
                    )

        return (True, "OK")

    def _default_state(self) -> Dict[str, Any]:
        return {
            "timestamp": time.time(),
            "timeframe": "15m",
            "btc": {
                "label": "Bitcoin",
                "price": 0.0,
                "regime": "NEUTRAL",
                "desc": "Iniciando...",
                "longs_allowed": True,
                "shorts_allowed": True
            },
            "sp500": {
                "label": "S&P 500",
                "price": 0.0,
                "regime": "NEUTRAL",
                "desc": "Iniciando...",
                "longs_allowed": True,
                "shorts_allowed": True
            }
        }


# Instancia singleton para ser reutilizada en todo el sistema
benchmark_sentinel = BenchmarkSentinel()
