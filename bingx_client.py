"""
Cliente especializado para BingX Futuros Perpetuos y Estándar.
Soporta Cripto, Índices Bursátiles (SP500, NASDAQ, DOW), Materias Primas y Acciones.
Implementa Apalancamiento Adaptativo, dimensionamiento del 10% y conmutador dinámico Demo/Real.
"""

import math
from datetime import datetime
import ccxt
from typing import Dict, Any, List, Optional
from config import config, ASSET_CATALOG, TICKER_ALIASES
from database import add_system_log, record_trade_opened, record_trade_closed, SessionLocal, TradeRecord
from notifications import notifier


class BingXFuturesClient:
    """Cliente de Trading Multiactivo con soporte para modo dual Demo/Real."""

    def __init__(self):
        self.api_key = config.BINGX_API_KEY
        self.secret_key = config.BINGX_SECRET_KEY
        self.is_demo = config.IS_DEMO
        self.break_even_protected = set()
        self.stagnant_notified = set()
        self.exchange = self._init_exchange()

    def _init_exchange(self) -> ccxt.bingx:
        """Inicializa CCXT para BingX en modo Perpetuos Swap."""
        exchange = ccxt.bingx({
            "apiKey": self.api_key,
            "secret": self.secret_key,
            "enableRateLimit": True,
            "options": {
                "defaultType": "swap",
            }
        })

        if self.is_demo:
            try:
                exchange.set_sandbox_mode(True)
                add_system_log("INFO", "BingX configurado en modo DEMO / VST.")
            except Exception as e:
                add_system_log("WARNING", f"No se pudo forzar sandbox mode: {e}")
        else:
            try:
                exchange.set_sandbox_mode(False)
                add_system_log("WARNING", "⚠️ ATENCIÓN: BingX configurado en modo REAL (LIVE USDT).")
            except Exception as e:
                add_system_log("WARNING", f"Nota modo live: {e}")

        return exchange

    def switch_environment(self, to_demo: bool) -> bool:
        """
        Alterna dinámicamente entre la cuenta Demo (VST) y la cuenta Real (USDT)
        sin cerrar ninguna posición abierta.
        """
        self.is_demo = to_demo
        config.IS_DEMO = to_demo
        self.exchange = self._init_exchange()
        self.ensure_markets()

        env_name = "DEMO (VST)" if to_demo else "REAL (USDT)"
        add_system_log("INFO", f"Entorno alternado a: {env_name} exitosamente.")
        return self.is_demo

    def update_api_credentials(
        self,
        api_key: Optional[str] = None,
        secret_key: Optional[str] = None,
        is_demo: Optional[bool] = None
    ) -> bool:
        """Actualiza las claves de BingX, el entorno y reinicializa la conexión en caliente."""
        if api_key:
            self.api_key = api_key.strip()
            config.BINGX_API_KEY = self.api_key
        if secret_key:
            self.secret_key = secret_key.strip()
            config.BINGX_SECRET_KEY = self.secret_key
        if is_demo is not None:
            self.is_demo = is_demo
            config.IS_DEMO = is_demo

        self.exchange = self._init_exchange()
        self.ensure_markets()
        add_system_log("INFO", "Claves y entorno de BingX actualizados y reconectados en caliente.")
        return True

    def ensure_markets(self):
        """Carga el catálogo de mercados si aún no está inicializado."""
        if not self.exchange.markets:
            self.exchange.load_markets()

    def resolve_symbol(self, raw_symbol: str) -> Dict[str, Any]:
        """
        Resuelve alias (ej: NASDAQ, SPX500, GOLD, TSLA, BTC/USDT)
        al símbolo exacto de BingX y extrae metadatos.
        """
        self.ensure_markets()
        clean = raw_symbol.strip().upper().replace(" ", "")

        mapped_alias = TICKER_ALIASES.get(clean, clean)

        if mapped_alias in ASSET_CATALOG:
            meta = ASSET_CATALOG[mapped_alias].copy()
            target = meta["symbol"]
            if target in self.exchange.markets:
                meta["exchange_symbol"] = target
                meta["display_name"] = mapped_alias
                return meta

        candidate = mapped_alias if ":" in mapped_alias else f"{mapped_alias}:USDT"
        if candidate in self.exchange.markets:
            return {
                "category": "custom",
                "name": mapped_alias,
                "target_sl": config.DEFAULT_SL_PCT,
                "symbol": candidate,
                "exchange_symbol": candidate,
                "display_name": mapped_alias
            }

        if mapped_alias in self.exchange.markets:
            return {
                "category": "custom",
                "name": mapped_alias,
                "target_sl": config.DEFAULT_SL_PCT,
                "symbol": mapped_alias,
                "exchange_symbol": mapped_alias,
                "display_name": mapped_alias
            }

        raise ValueError(f"El símbolo '{raw_symbol}' no existe en el catálogo de BingX.")

    def get_adaptive_leverage(self, asset_info: Dict[str, Any], sl_pct: Optional[float] = None) -> int:
        """
        Calcula el apalancamiento cuantitativo.
        Si config.AUTO_LEVERAGE_BY_SL está activo, calcula el apalancamiento matemáticamente
        para asegurar que el Precio de Liquidación esté SIEMPRE mucho más lejos que el Stop Loss.
        """
        effective_sl = sl_pct or asset_info.get("target_sl", config.DEFAULT_SL_PCT)

        # MODO AUTOMÁTICO ANTILIQUIDACIÓN MATEMÁTICO:
        # Distancia a Liquidación = 100 / Leverage
        # Para que Liquidación >= effective_sl * safety_factor:
        # Leverage <= 100 / (effective_sl * safety_factor)
        if config.AUTO_LEVERAGE_BY_SL and effective_sl > 0:
            safety = max(1.2, config.SAFETY_MARGIN_FACTOR)
            raw_lev = 100.0 / (effective_sl * safety)
            safe_lev = max(2, min(config.MAX_ALLOWED_LEVERAGE, int(raw_lev)))
            return safe_lev

        # Modo manual por categorías fijas (si el usuario apaga el modo automático)
        cat = asset_info.get("category", "")
        if cat == "index":
            base_lev = config.LEVERAGE_INDICES
        elif cat == "commodity":
            base_lev = config.LEVERAGE_COMMODITIES
        elif cat == "crypto_major":
            base_lev = config.LEVERAGE_CRYPTO_MAJORS
        elif cat == "crypto_alt":
            base_lev = config.LEVERAGE_CRYPTO_ALTS
        elif cat == "stock":
            base_lev = config.LEVERAGE_STOCKS
        else:
            base_lev = 10

        return max(2, min(config.MAX_ALLOWED_LEVERAGE, base_lev))

    def get_balance(self) -> Dict[str, Any]:
        """Obtiene el balance libre y total (Patrimonio / Equity) de la cuenta activa (Demo o Real)."""
        try:
            raw_balance = self.exchange.fetch_balance()
            total_b = raw_balance.get("total", {})
            free_b = raw_balance.get("free", {})
            used_b = raw_balance.get("used", {})

            if self.is_demo and "VST" in total_b and total_b["VST"] > 0:
                asset = "VST"
            else:
                asset = "USDT"

            total = float(total_b.get(asset, 0.0) or 0.0)
            free = float(free_b.get(asset, 0.0) or 0.0)
            used = float(used_b.get(asset, 0.0) or 0.0)
            wallet_balance = total
            unrealized_pnl = 0.0

            # En cuenta Real (USDT Swap), BingX devuelve equity, balance y availableMargin en info.data
            info_data = raw_balance.get("info", {}).get("data", [])
            target_data = None
            if isinstance(info_data, list):
                for item in info_data:
                    if isinstance(item, dict) and item.get("asset") == asset:
                        target_data = item
                        break
            elif isinstance(info_data, dict):
                b_info = info_data.get("balance", {})
                if isinstance(b_info, dict) and b_info.get("asset") == asset:
                    target_data = b_info

            if target_data:
                eq = target_data.get("equity")
                wb = target_data.get("balance")
                am = target_data.get("availableMargin")
                um = target_data.get("usedMargin")
                upnl = target_data.get("unrealizedProfit")

                if eq is not None:
                    total = float(eq)
                if wb is not None:
                    wallet_balance = float(wb)
                if am is not None:
                    free = float(am)
                if um is not None:
                    used = float(um)
                if upnl is not None:
                    unrealized_pnl = float(upnl)

            return {
                "asset": asset,
                "is_demo": self.is_demo,
                "total": round(total, 2),
                "wallet_balance": round(wallet_balance, 2),
                "equity": round(total, 2),
                "free": round(free, 2),
                "used": round(used, 2),
                "unrealized_pnl": round(unrealized_pnl, 2)
            }
        except Exception as err:
            add_system_log("ERROR", f"Error al consultar balance: {err}")
            return {"asset": "USDT", "is_demo": self.is_demo, "total": 0.0, "wallet_balance": 0.0, "equity": 0.0, "free": 0.0, "used": 0.0, "unrealized_pnl": 0.0}

    def get_ticker_price(self, exchange_symbol: str) -> float:
        """Obtiene el último precio de mercado."""
        self.ensure_markets()
        ticker = self.exchange.fetch_ticker(exchange_symbol)
        price = ticker.get("last") or ticker.get("close")
        if not price:
            raise ValueError(f"No se pudo obtener precio para {exchange_symbol}")
        return float(price)

    def set_leverage(self, exchange_symbol: str, leverage: int, side: Optional[str] = None) -> int:
        """
        Configura el apalancamiento en BingX para LONG y SHORT garantizando el Hedge Mode.
        Verifica el límite máximo de apalancamiento permitido por BingX para ese contrato específico.
        Retorna el apalancamiento real efectivo para que el dimensionamiento no exceda el margen.
        """
        try:
            market = self.exchange.market(exchange_symbol)
            market_id = market.get("id", exchange_symbol.replace("/", "-").split(":")[0])

            # Consultar topes reales de apalancamiento que impone BingX en este contrato
            try:
                info_res = self.exchange.swapV2PrivateGetTradeLeverage({"symbol": market_id})
                data = info_res.get("data", {})
                target_side = (side or "LONG").upper()
                max_key = "maxLongLeverage" if target_side == "LONG" else "maxShortLeverage"
                max_allowed = int(data.get(max_key) or data.get("maxLongLeverage") or 50)
                if leverage > max_allowed:
                    add_system_log(
                        "WARNING",
                        f"Apalancamiento {leverage}x supera el tope de BingX ({max_allowed}x) para {exchange_symbol}. Ajustando a {max_allowed}x."
                    )
                    leverage = max_allowed
            except Exception as check_err:
                add_system_log("DEBUG", f"No se pudo consultar maxLeverage para {exchange_symbol}: {check_err}")

            target_sides = [side] if side else ["LONG", "SHORT"]
            for s in target_sides:
                try:
                    self.exchange.set_leverage(leverage, exchange_symbol, params={"side": s})
                except Exception as inner:
                    add_system_log("DEBUG", f"Aviso set_leverage {s} en {exchange_symbol}: {inner}")

            add_system_log("INFO", f"Apalancamiento fijado en {leverage}x para {exchange_symbol}")
            return leverage
        except Exception as e:
            add_system_log("WARNING", f"Error fijando apalancamiento {exchange_symbol}: {e}")
            return min(leverage, 20)


    def get_open_positions(self) -> List[Dict[str, Any]]:
        """Obtiene las posiciones abiertas activas de la cuenta seleccionada con métricas TP/SL y progreso."""
        self.ensure_markets()
        positions_out = []
        try:
            raw_positions = self.exchange.fetch_positions()

            # Mapear órdenes abiertas de Stop Loss y Take Profit en BingX
            order_sl_tp = {}
            try:
                open_orders = self.exchange.fetch_open_orders()
                for o in open_orders:
                    o_sym = o.get("symbol", "")
                    o_info = o.get("info", {})
                    o_pos_side = str(o_info.get("positionSide", "")).upper()
                    o_type = str(o_info.get("type", "")).upper()
                    stop_px = float(o_info.get("stopPrice") or o.get("stopLossPrice") or o.get("takeProfitPrice") or 0.0)

                    k = f"{o_sym}_{o_pos_side}"
                    if k not in order_sl_tp:
                        order_sl_tp[k] = {}
                    if "STOP" in o_type:
                        order_sl_tp[k]["sl"] = stop_px
                    elif "TAKE_PROFIT" in o_type:
                        order_sl_tp[k]["tp"] = stop_px
            except Exception as orders_err:
                add_system_log("DEBUG", f"Aviso al consultar órdenes para SL/TP: {orders_err}")

            def_sl_pct = float(getattr(config, "DEFAULT_SL_PCT", 1.5))
            def_rr = float(getattr(config, "DEFAULT_RISK_RATIO", 1.5))

            for pos in raw_positions:
                contracts = float(pos.get("contracts") or pos.get("amount") or 0.0)
                if contracts > 0:
                    symbol = pos.get("symbol", "")
                    side = pos.get("side", "").upper()
                    entry_price = float(pos.get("entryPrice") or 0.0)
                    current_price = float(pos.get("markPrice") or entry_price)
                    unrealized_pnl = float(pos.get("unrealizedPnl") or 0.0)
                    leverage = int(pos.get("leverage") or 10)

                    notional = float(pos.get("notional") or 0.0)
                    if notional == 0.0 and entry_price > 0 and contracts > 0:
                        notional = contracts * entry_price

                    margin = float(pos.get("initialMargin") or pos.get("info", {}).get("initialMargin") or 0.0)
                    if margin == 0.0 and leverage > 0:
                        margin = notional / leverage if notional > 0 else (contracts * entry_price) / leverage

                    is_be_active = symbol in self.break_even_protected

                    pos_key = f"{symbol}_{side}"
                    sl_price = order_sl_tp.get(pos_key, {}).get("sl")
                    tp_price = order_sl_tp.get(pos_key, {}).get("tp")

                    clean_sym = symbol.replace("/USDT:USDT", "").replace("/USDT", "")
                    db_trade = None
                    try:
                        with SessionLocal() as db_session:
                            db_trade = db_session.query(TradeRecord).filter(
                                TradeRecord.symbol.like(f"%{clean_sym}%"),
                                TradeRecord.status == "OPEN"
                            ).order_by(TradeRecord.id.desc()).first()
                            if db_trade:
                                sl_price = sl_price or db_trade.sl_price
                                tp_price = tp_price or db_trade.tp_price
                    except Exception:
                        pass

                    # Calcular tiempo que lleva abierta la posición de forma clara y legible
                    delta_sec = None
                    if db_trade and db_trade.opened_at:
                        delta_sec = max(0.0, (datetime.now() - db_trade.opened_at).total_seconds())
                    else:
                        ts = pos.get("info", {}).get("updateTime") or pos.get("timestamp") or pos.get("info", {}).get("time")
                        if ts:
                            try:
                                delta_sec = max(0.0, datetime.now().timestamp() - (float(ts) / 1000.0))
                            except Exception:
                                pass

                    if delta_sec is not None:
                        h = int(delta_sec // 3600)
                        m = int((delta_sec % 3600) // 60)
                        if h >= 24:
                            d = h // 24
                            rem_h = h % 24
                            duration_str = f"{d}d {rem_h:02d}h"
                        elif h > 0:
                            duration_str = f"{h}h {m:02d}m"
                        elif m >= 1:
                            duration_str = f"{m} min"
                        else:
                            sec = max(1, int(delta_sec))
                            duration_str = f"{sec}s (recién)"
                    else:
                        duration_str = "Recién abierta"

                    is_long = side in ["BUY", "LONG"]
                    if not sl_price:
                        sl_price = entry_price * (1.0 - (def_sl_pct / 100.0)) if is_long else entry_price * (1.0 + (def_sl_pct / 100.0))
                    if not tp_price:
                        tp_price = entry_price * (1.0 + ((def_sl_pct * def_rr) / 100.0)) if is_long else entry_price * (1.0 - ((def_sl_pct * def_rr) / 100.0))

                    total_range = (tp_price - sl_price) if is_long else (sl_price - tp_price)
                    if total_range > 0 and current_price > 0:
                        if is_long:
                            progress_pct = ((current_price - sl_price) / total_range) * 100.0
                            entry_progress_pct = ((entry_price - sl_price) / total_range) * 100.0
                            dist_tp_pct = max(0.0, ((tp_price - current_price) / current_price) * 100.0)
                            dist_sl_pct = max(0.0, ((current_price - sl_price) / current_price) * 100.0)
                        else:
                            progress_pct = ((sl_price - current_price) / total_range) * 100.0
                            entry_progress_pct = ((sl_price - entry_price) / total_range) * 100.0
                            dist_tp_pct = max(0.0, ((current_price - tp_price) / current_price) * 100.0)
                            dist_sl_pct = max(0.0, ((sl_price - current_price) / current_price) * 100.0)
                    else:
                        progress_pct = 50.0
                        entry_progress_pct = 50.0
                        dist_tp_pct = 0.0
                        dist_sl_pct = 0.0

                    roe_pct = round((unrealized_pnl / margin) * 100.0, 2) if margin > 0 else 0.0

                    positions_out.append({
                        "symbol": symbol,
                        "side": side,
                        "amount": contracts,
                        "leverage": leverage,
                        "entry_price": round(entry_price, 4),
                        "current_price": round(current_price, 4),
                        "unrealized_pnl": round(unrealized_pnl, 2),
                        "initial_margin": round(margin, 2),
                        "margin_usdt": round(margin, 2),
                        "notional_usdt": round(notional, 2),
                        "is_break_even_protected": is_be_active,
                        "sl_price": round(sl_price, 4),
                        "tp_price": round(tp_price, 4),
                        "roe_pct": roe_pct,
                        "progress_pct": round(max(0.0, min(100.0, progress_pct)), 1),
                        "entry_progress_pct": round(max(0.0, min(100.0, entry_progress_pct)), 1),
                        "dist_tp_pct": round(dist_tp_pct, 2),
                        "dist_sl_pct": round(dist_sl_pct, 2),
                        "duration_str": duration_str
                    })

            # Limpiar del set de break-even aquellos activos que ya se cerraron
            current_active_symbols = {p["symbol"] for p in positions_out}
            self.break_even_protected = {s for s in self.break_even_protected if s in current_active_symbols}

        except Exception as err:
            add_system_log("ERROR", f"Error al consultar posiciones: {err}")

        return positions_out

    def calculate_position_size(
        self,
        exchange_symbol: str,
        current_price: float,
        leverage: int,
        balance_pct: float = config.POSITION_BALANCE_PCT
    ) -> float:
        """
        Calcula el tamaño exacto del lote.
        Soporta dos modalidades:
        1. 'percent_total': % del balance total (equitativo, no decrece con cada trade).
        2. 'fixed_usdt': monto fijo exacto en USDT de margen por operación.
        """
        self.ensure_markets()
        balance_info = self.get_balance()
        free_balance = float(balance_info.get("free", 0.0))
        total_balance = float(balance_info.get("total", free_balance))

        # Colchón de seguridad del 95% para cubrir comisiones taker sin ser rechazado por BingX
        usable_free = max(0.0, free_balance * 0.95)
        if usable_free <= 0.05:
            raise ValueError(f"Saldo libre agotado (${free_balance:.2f} USDT). Esperando liberación de margen.")

        sizing_mode = getattr(config, "POSITION_SIZING_MODE", "percent_total")
        if sizing_mode == "fixed_usdt":
            target_margin = float(getattr(config, "FIXED_POSITION_MARGIN_USDT", 10.0))
            mode_desc = f"Monto Fijo ${target_margin:.2f} USDT"
        else:
            # Porcentaje del balance total para garantizar tamaño constante
            target_margin = total_balance * (balance_pct / 100.0)
            mode_desc = f"{balance_pct:.1f}% Balance Total (${target_margin:.2f} USDT)"

        # USAR LO QUE RESTA DE TODAS FORMAS:
        # Si el saldo libre disponible no alcanza para el objetivo, usamos lo que queda disponible
        if usable_free < target_margin:
            margin_to_use = usable_free
            mode_desc += f" -> Usando remanente disponible: ${margin_to_use:.2f} USDT"
        else:
            margin_to_use = target_margin

        notional_value = margin_to_use * leverage
        raw_amount = notional_value / current_price

        try:
            prec_str = self.exchange.amount_to_precision(exchange_symbol, raw_amount)
            amount = float(prec_str)
        except Exception:
            amount = round(raw_amount, 4)

        market = self.exchange.markets.get(exchange_symbol, {})
        limits = market.get("limits", {})
        min_amount = float(limits.get("amount", {}).get("min", 0.0001) or 0.0001)
        min_cost = float(limits.get("cost", {}).get("min", 0.0) or 0.0)

        if amount < min_amount:
            raise ValueError(f"Cantidad ({amount}) inferior al mínimo de {min_amount} para {exchange_symbol}")

        if min_cost > 0 and notional_value < min_cost:
            raise ValueError(
                f"El valor de la orden (${notional_value:.2f} USDT con margen restante ${margin_to_use:.2f} y {leverage}x) "
                f"es inferior al mínimo requerido por BingX (${min_cost:.2f} USDT)."
            )

        add_system_log(
            "INFO",
            f"Dimensionamiento ({mode_desc}): Margen=${margin_to_use:.2f} con {leverage}x -> Lote={amount} {exchange_symbol}"
        )
        return amount

    def check_and_apply_break_even(self, positions: List[Dict[str, Any]]) -> None:
        """
        NIVEL 4: Blindaje a Break-Even Automático (Riesgo Cero) y por Estancamiento Temporal.
        Monitorea las posiciones abiertas:
        1. Por Ganancia: En cuanto superan el umbral (+1.0%), mueve el SL a precio de entrada + comisión.
        2. Por Estancamiento: Si la posición lleva >= 4 horas abierta sin avance, blinda el SL a Break-Even.
        """
        if not getattr(config, "BREAK_EVEN_ENABLED", True) or not positions:
            return

        trigger_pct = float(getattr(config, "BREAK_EVEN_TRIGGER_PCT", 1.0))
        offset_pct = float(getattr(config, "BREAK_EVEN_OFFSET_PCT", 0.08))
        stagnant_hours = float(getattr(config, "STAGNANT_HOURS_THRESHOLD", 4.0))
        stagnant_enabled = getattr(config, "STAGNANT_BE_ENABLED", True)

        for pos in positions:
            symbol = pos.get("symbol")
            if not symbol or symbol in self.break_even_protected:
                continue

            entry_price = float(pos.get("entry_price") or 0.0)
            current_price = float(pos.get("current_price") or 0.0)
            side = str(pos.get("side", "")).upper()
            contracts = float(pos.get("amount") or 0.0)

            if entry_price <= 0 or current_price <= 0 or contracts <= 0:
                continue

            is_long = side in ["BUY", "LONG"]
            if is_long:
                gain_pct = ((current_price - entry_price) / entry_price) * 100.0
                be_price = entry_price * (1.0 + (offset_pct / 100.0))
                pos_side = "LONG"
                close_side = "sell"
            else:
                gain_pct = ((entry_price - current_price) / entry_price) * 100.0
                be_price = entry_price * (1.0 - (offset_pct / 100.0))
                pos_side = "SHORT"
                close_side = "buy"

            # Calcular antigüedad de la posición en horas
            open_hours = 0.0
            clean_sym = symbol.replace("/USDT:USDT", "").replace("/USDT", "")
            try:
                with SessionLocal() as db_session:
                    db_trade = db_session.query(TradeRecord).filter(
                        TradeRecord.symbol.like(f"%{clean_sym}%"),
                        TradeRecord.status == "OPEN"
                    ).order_by(TradeRecord.id.desc()).first()
                    if db_trade and db_trade.opened_at:
                        delta = (datetime.now() - db_trade.opened_at).total_seconds() / 3600.0
                        open_hours = max(0.0, delta)
            except Exception:
                pass

            if open_hours == 0.0:
                ts = pos.get("timestamp") or pos.get("info", {}).get("updateTime")
                if ts:
                    try:
                        open_hours = max(0.0, (datetime.now().timestamp() - (float(ts) / 1000.0)) / 3600.0)
                    except Exception:
                        pass

            is_gain_be = gain_pct >= trigger_pct
            is_stagnant_be = bool(stagnant_enabled and open_hours >= stagnant_hours and not is_gain_be)

            # Evaluar si procede blindar a Break-Even
            if is_gain_be or is_stagnant_be:
                try:
                    self.ensure_markets()
                    formatted_be_price = float(self.exchange.price_to_precision(symbol, be_price))

                    # Validación matemática estricta para BingX:
                    # Para LONG: un Stop Loss debe ser menor al precio actual (formatted_be_price < current_price).
                    # Para SHORT: un Stop Loss debe ser mayor al precio actual (formatted_be_price > current_price).
                    is_valid_sl = (formatted_be_price < current_price) if is_long else (formatted_be_price > current_price)

                    if not is_valid_sl:
                        # La posición está actualmente en leve retroceso/drawdown respecto a entrada.
                        # No se puede enviar un Stop Loss del lado incorrecto del mercado (evita error BingX 110412).
                        # Se mantiene el Stop Loss técnico original y se esperará a que el precio vuelva a entrada.
                        if is_stagnant_be and symbol not in self.stagnant_notified:
                            self.stagnant_notified.add(symbol)
                            add_system_log(
                                "INFO",
                                f"⏳ Posición en {symbol} ({pos_side}, {open_hours:.1f}h): Actualmente en retroceso flotante "
                                f"(${current_price} vs BE ${formatted_be_price}). Manteniendo SL técnico original; el blindaje "
                                f"se activará cuando el precio cruce a Break-Even."
                            )
                        continue

                    # 1. Cancelar Stop Loss condicional previo en BingX si existe
                    try:
                        open_orders = self.exchange.fetch_open_orders(symbol)
                        for order in open_orders:
                            o_type = str(order.get("type", "")).upper()
                            if "STOP" in o_type:
                                self.exchange.cancel_order(order["id"], symbol)
                    except Exception as cancel_err:
                        add_system_log("DEBUG", f"Aviso cancelando SL previo para BE en {symbol}: {cancel_err}")

                    # 2. Colocar nueva orden de Stop Loss en precio de Break-Even blindado
                    self.exchange.create_order(
                        symbol=symbol,
                        type="STOP_MARKET",
                        side=close_side,
                        amount=contracts,
                        params={"stopPrice": formatted_be_price, "positionSide": pos_side}
                    )

                    self.break_even_protected.add(symbol)

                    if is_gain_be:
                        msg = (
                            f"🛡️ BREAK-EVEN ACTIVADO en {symbol} ({pos_side}): "
                            f"Ganancia alcanzó +{gain_pct:.2f}%. Stop Loss blindado a ${formatted_be_price} (Riesgo Cero)."
                        )
                        add_system_log("INFO", msg)

                        tele_text = (
                            f"🛡️ *PROTECCIÓN BREAK-EVEN ACTIVADA* [Riesgo Cero]\n\n"
                            f"📌 Activo: *{symbol}* ({pos_side})\n"
                            f"📈 Ganancia alcanzada: *+{gain_pct:.2f}%*\n"
                            f"🎯 Entrada: ${entry_price}\n"
                            f"🛡️ Nuevo SL Blindado: *${formatted_be_price}*\n\n"
                            f"✨ _La operación ahora está protegida contra pérdidas._"
                        )
                        notifier.send_telegram(tele_text)
                    else:
                        msg = (
                            f"⏳ ESTANCAMIENTO DETECTADO en {symbol} ({pos_side}): "
                            f"Lleva {open_hours:.1f}h abierta sin avance. SL blindado a Break-Even ${formatted_be_price}."
                        )
                        add_system_log("WARNING", msg)

                        notifier.notify_stagnant_break_even(
                            symbol=symbol,
                            side=pos_side,
                            open_hours=open_hours,
                            be_price=formatted_be_price,
                            current_roe=float(pos.get("roe_pct", 0.0)),
                            is_demo=self.is_demo
                        )

                except Exception as be_err:
                    add_system_log("ERROR", f"Fallo al aplicar Break-Even en {symbol}: {be_err}")

    def sync_closed_trades(self, current_open_positions: List[Dict[str, Any]]) -> None:
        """
        Sincroniza con BingX los trades que ya se cerraron por Stop Loss, Take Profit
        o cierre manual externo, actualizando su precio de salida y PnL real en SQLite y estadísticas.
        """
        try:
            active_symbols = set()
            for p in current_open_positions:
                sym = p.get("symbol", "")
                if sym:
                    active_symbols.add(sym.upper())
                    clean = sym.split(":")[0].replace("-", "/").upper()
                    active_symbols.add(clean)
                    active_symbols.add(clean.split("/")[0])

            with SessionLocal() as session:
                open_trades = session.query(TradeRecord).filter(TradeRecord.status == "OPEN").all()
                if not open_trades:
                    return

                # Consultar historial de ingresos de BingX para obtener PnL realizado exacto
                recent_income = []
                try:
                    inc_res = self.exchange.swapV2PrivateGetUserIncome({"limit": 50})
                    recent_income = inc_res.get("data", [])
                except Exception as inc_err:
                    add_system_log("DEBUG", f"Aviso al consultar UserIncome de BingX: {inc_err}")

                for t in open_trades:
                    clean_db_sym = t.symbol.replace("-", "/").upper()
                    base = clean_db_sym.split("/")[0]

                    is_still_open = (
                        t.symbol.upper() in active_symbols or
                        clean_db_sym in active_symbols or
                        base in active_symbols
                    )

                    if not is_still_open:
                        exit_price = 0.0
                        pnl_usd = 0.0
                        matched_income = False

                        # 1. Intentar obtener PnL realizado exacto desde BingX UserIncome
                        bingx_sym = clean_db_sym.replace("/", "-")
                        for inc in recent_income:
                            if inc.get("incomeType") == "REALIZED_PNL" and (
                                inc.get("symbol") == bingx_sym or
                                base in inc.get("symbol", "")
                            ):
                                pnl_usd = float(inc.get("income") or 0.0)
                                matched_income = True
                                break

                        # 2. Si no se encontró en UserIncome, consultar trades recientes de la moneda
                        if not matched_income:
                            try:
                                resolved = self.resolve_symbol(t.symbol)
                                ex_sym = resolved["exchange_symbol"]
                                my_trades = self.exchange.fetch_my_trades(ex_sym, limit=5)
                                if my_trades:
                                    last_trade = my_trades[-1]
                                    exit_price = float(last_trade.get("price") or 0.0)
                                    comm = abs(float(last_trade.get("info", {}).get("commission") or 0.0))
                                    if exit_price > 0 and t.entry_price > 0:
                                        mult = 1.0 if t.side.upper() in ["BUY", "LONG"] else -1.0
                                        raw_pnl = mult * (exit_price - t.entry_price) * t.amount
                                        pnl_usd = raw_pnl - comm
                            except Exception as fetch_err:
                                add_system_log("DEBUG", f"Aviso consultando trade cerrado en BingX para {t.symbol}: {fetch_err}")

                        # 3. Fallback a precio actual de mercado si aún no hay precio de salida
                        if exit_price <= 0:
                            try:
                                exit_price = self.get_ticker_price(t.symbol)
                            except Exception:
                                exit_price = t.entry_price

                        # 4. Filtro de seguridad matemática: el PnL no puede superar el margen razonable
                        approx_margin = (t.entry_price * t.amount) / max(1, t.leverage or 1)
                        if approx_margin > 0:
                            max_bound = approx_margin * 5.0
                            if abs(pnl_usd) > max_bound:
                                # Normalizar en caso de discrepancia severa de unidades
                                pnl_usd = max(-approx_margin, min(max_bound, pnl_usd))

                        record_trade_closed(
                            trade_id=t.id,
                            order_id=t.order_id,
                            exit_price=round(exit_price, 4),
                            pnl_usd=round(pnl_usd, 2)
                        )

                        add_system_log(
                            "INFO",
                            f"🏁 POSICIÓN CERRADA en BingX: {t.symbol} ({t.side}) | "
                            f"Salida: ${exit_price:,.4f} | PnL: ${pnl_usd:+,.2f}"
                        )

                        notifier.notify_trade_closed(
                            symbol=t.symbol,
                            side=t.side,
                            exit_price=exit_price,
                            pnl_usd=round(pnl_usd, 2),
                            is_demo=self.is_demo
                        )

        except Exception as sync_err:
            add_system_log("DEBUG", f"Aviso al sincronizar trades cerrados: {sync_err}")

    def open_bracket_position(
        self,
        raw_symbol: str,
        side: str,
        custom_leverage: Optional[int] = None,
        custom_sl_pct: Optional[float] = None,
        custom_sl_price: Optional[float] = None,
        custom_tp_price: Optional[float] = None,
        risk_ratio: float = config.DEFAULT_RISK_RATIO
    ) -> Dict[str, Any]:
        """Abre posición con margen configurado, apalancamiento adaptativo y SL/TP técnicos u obligatorios."""
        self.ensure_markets()
        asset_info = self.resolve_symbol(raw_symbol)
        exchange_symbol = asset_info["exchange_symbol"]
        action = side.upper()

        # 1. Precio actual
        entry_price = self.get_ticker_price(exchange_symbol)

        # 2. Niveles SL y TP (técnicos SMC o basados en % de catálogo)
        if custom_sl_price and custom_tp_price:
            sl_price = float(custom_sl_price)
            tp_price = float(custom_tp_price)
            sl_pct = abs((entry_price - sl_price) / entry_price) * 100.0
        else:
            sl_pct = custom_sl_pct or asset_info.get("target_sl", config.DEFAULT_SL_PCT)
            tp_pct = sl_pct * risk_ratio
            if action == "BUY":
                sl_price = entry_price * (1.0 - (sl_pct / 100.0))
                tp_price = entry_price * (1.0 + (tp_pct / 100.0))
            elif action == "SELL":
                sl_price = entry_price * (1.0 + (sl_pct / 100.0))
                tp_price = entry_price * (1.0 - (tp_pct / 100.0))
            else:
                raise ValueError(f"Acción '{action}' inválida.")

        leverage = custom_leverage or self.get_adaptive_leverage(asset_info, sl_pct)
        pos_side = "LONG" if action == "BUY" else "SHORT"
        order_side = "buy" if action == "BUY" else "sell"
        close_side = "sell" if action == "BUY" else "buy"

        # 3. Configurar apalancamiento seguro y respetar tope máximo permitido por BingX
        effective_leverage = self.set_leverage(exchange_symbol, leverage, side=pos_side)
        add_system_log("INFO", f"🛡️ Apalancamiento asignado: {effective_leverage}x para {exchange_symbol} (SL Técnico: {sl_pct:.2f}%)")

        sl_price = float(self.exchange.price_to_precision(exchange_symbol, sl_price))
        tp_price = float(self.exchange.price_to_precision(exchange_symbol, tp_price))

        # Validación de coherencia para evitar rechazos de BingX (ej: TP Price must be greater than Last Price)
        if action == "BUY":
            if tp_price <= entry_price:
                tp_price = float(self.exchange.price_to_precision(exchange_symbol, entry_price * 1.005))
            if sl_price >= entry_price:
                sl_price = float(self.exchange.price_to_precision(exchange_symbol, entry_price * 0.995))
        elif action == "SELL":
            if tp_price >= entry_price:
                tp_price = float(self.exchange.price_to_precision(exchange_symbol, entry_price * 0.995))
            if sl_price <= entry_price:
                sl_price = float(self.exchange.price_to_precision(exchange_symbol, entry_price * 1.005))

        # 4. Tamaño calculado con el apalancamiento real efectivo para respetar el margen programado
        amount = self.calculate_position_size(exchange_symbol, entry_price, effective_leverage)


        order_params = {
            "positionSide": pos_side,
            "stopLoss": {"triggerPrice": sl_price},
            "takeProfit": {"triggerPrice": tp_price}
        }

        try:
            order = self.exchange.create_order(
                symbol=exchange_symbol,
                type="market",
                side=order_side,
                amount=amount,
                params=order_params
            )
            order_id = str(order.get("id", "N/A"))
        except Exception as direct_err:
            add_system_log("WARNING", f"Despacho condicional alternativo en Hedge Mode ({direct_err})...")
            try:
                order = self.exchange.create_order(
                    symbol=exchange_symbol,
                    type="market",
                    side=order_side,
                    amount=amount,
                    params={"positionSide": pos_side}
                )
                order_id = str(order.get("id", "N/A"))
            except Exception as order_err:
                add_system_log("ERROR", f"Fallo al abrir orden en BingX: {order_err}")
                raise order_err

            # Bracket orders condicionales para Hedge Mode (sin reduceOnly)
            try:
                self.exchange.create_order(
                    symbol=exchange_symbol,
                    type="STOP_MARKET",
                    side=close_side,
                    amount=amount,
                    params={"stopPrice": sl_price, "positionSide": pos_side}
                )
            except Exception as sl_err:
                add_system_log("WARNING", f"Aviso SL condicional: {sl_err}")

            try:
                self.exchange.create_order(
                    symbol=exchange_symbol,
                    type="TAKE_PROFIT_MARKET",
                    side=close_side,
                    amount=amount,
                    params={"stopPrice": tp_price, "positionSide": pos_side}
                )
            except Exception as tp_err:
                add_system_log("WARNING", f"Aviso TP condicional: {tp_err}")

        # 5. Registro y Notificación WhatsApp
        record_trade_opened(
            order_id=order_id,
            symbol=asset_info["display_name"],
            side=action,
            amount=amount,
            entry_price=entry_price,
            leverage=leverage,
            sl_price=sl_price,
            tp_price=tp_price,
            is_demo=self.is_demo
        )

        notifier.notify_trade_opened(
            symbol=asset_info["display_name"],
            side=action,
            amount=amount,
            price=entry_price,
            leverage=leverage,
            sl=sl_price,
            tp=tp_price,
            is_demo=self.is_demo
        )

        return {
            "status": "success",
            "order_id": order_id,
            "symbol": asset_info["display_name"],
            "side": action,
            "amount": amount,
            "entry_price": entry_price,
            "leverage": leverage,
            "sl_price": sl_price,
            "tp_price": tp_price
        }

    def close_position_market(self, raw_symbol: str) -> Dict[str, Any]:
        """Cierra inmediatamente la posición a mercado en Hedge Mode y notifica."""
        self.ensure_markets()
        asset_info = self.resolve_symbol(raw_symbol)
        exchange_symbol = asset_info["exchange_symbol"]

        positions = self.get_open_positions()
        target = next((p for p in positions if p["symbol"] == exchange_symbol), None)

        if not target:
            raise ValueError(f"No existe posición abierta activa para {raw_symbol}")

        side = target["side"]
        amount = target["amount"]
        pos_side = "LONG" if side in ["BUY", "LONG"] else "SHORT"
        close_side = "sell" if pos_side == "LONG" else "buy"

        order = self.exchange.create_order(
            symbol=exchange_symbol,
            type="market",
            side=close_side,
            amount=amount,
            params={"positionSide": pos_side}
        )

        exit_price = self.get_ticker_price(exchange_symbol)
        pnl = target.get("unrealized_pnl", 0.0)

        record_trade_closed(
            order_id=str(order.get("id")),
            exit_price=exit_price,
            pnl_usd=pnl
        )

        notifier.notify_trade_closed(
            symbol=raw_symbol,
            side=side,
            exit_price=exit_price,
            pnl_usd=pnl,
            is_demo=self.is_demo
        )

        return {
            "status": "closed",
            "symbol": raw_symbol,
            "amount": amount,
            "exit_price": exit_price,
            "pnl": pnl
        }

    def get_watchlist_summary(self) -> List[Dict[str, Any]]:
        """Retorna la lista de activos monitoreados guardados en la BD con cotización en tiempo real."""
        self.ensure_markets()
        from database import get_db_watchlist
        db_items = get_db_watchlist()

        # Si la BD aún no tiene items, usar catálogo base
        items_to_query = db_items if db_items else [
            {"ticker": k, "name": v["name"], "category": v["category"], "symbol": v["symbol"], "target_sl": v.get("target_sl", 1.5)}
            for k, v in ASSET_CATALOG.items()
        ]

        summary = []
        for item in items_to_query:
            ex_sym = item["symbol"]
            price = 0.0
            try:
                ticker = self.exchange.fetch_ticker(ex_sym)
                price = float(ticker.get("last") or ticker.get("close") or 0.0)
            except Exception:
                price = 0.0

            lev = self.get_adaptive_leverage(item)

            summary.append({
                "ticker": item["ticker"],
                "name": item["name"],
                "category": item["category"],
                "price": round(price, 4) if price < 10 else round(price, 2),
                "leverage": lev,
                "target_sl_pct": item.get("target_sl", 1.5)
            })

        return summary

    def add_custom_ticker(self, raw_input: str) -> Dict[str, Any]:
        """
        Busca un ticker en BingX (ej: DOGE, ADA, EUR/USD, TSLA, etc.) y lo agrega a la Watchlist.
        """
        self.ensure_markets()
        from database import add_db_watchlist_ticker

        clean = raw_input.strip().upper().replace(" ", "")
        resolved = self.resolve_symbol(clean)
        ex_symbol = resolved["exchange_symbol"]
        ticker_label = resolved["display_name"]

        # Determinar categoría y SL base
        cat = resolved.get("category", "crypto")
        target_sl = resolved.get("target_sl", 1.5)
        name = resolved.get("name", ticker_label)

        success = add_db_watchlist_ticker(
            ticker=ticker_label,
            name=name,
            category=cat,
            symbol=ex_symbol,
            target_sl=target_sl
        )

        if not success:
            raise ValueError(f"El activo {ticker_label} ya está en tu lista de seguimiento.")

        add_system_log("INFO", f"Nuevo ticker añadido a la Watchlist: {ticker_label} ({ex_symbol})")
        return {
            "ticker": ticker_label,
            "name": name,
            "symbol": ex_symbol,
            "category": cat
        }

    def remove_custom_ticker(self, ticker: str) -> bool:
        """Elimina un ticker de la watchlist."""
        from database import delete_db_watchlist_ticker
        success = delete_db_watchlist_ticker(ticker)
        if success:
            add_system_log("INFO", f"Ticker eliminado de la Watchlist: {ticker}")
        return success

    def get_market_symbols_by_volume(self, limit: int = 50) -> List[Dict[str, Any]]:
        """
        Obtiene los contratos Swap USDT más líquidos de BingX ordenados por volumen 24h.
        Ideal para el modo 'Todo el Mercado BingX' buscando oportunidades institucionales.
        """
        self.ensure_markets()
        try:
            tickers = self.exchange.fetch_tickers()
            swap_pairs = []
            for s, t in tickers.items():
                if ":USDT" in s:
                    vol = t.get("quoteVolume", 0) or t.get("baseVolume", 0) or 0
                    swap_pairs.append({
                        "symbol": s,
                        "ticker": s.split(":")[0],
                        "volume_24h": vol,
                        "last_price": t.get("last", 0)
                    })
            swap_pairs.sort(key=lambda x: x["volume_24h"], reverse=True)
            return swap_pairs[:limit]
        except Exception as e:
            add_system_log("WARNING", f"Error obteniendo tickers por volumen: {e}")
            symbols = [s for s, m in self.exchange.markets.items() if m.get("swap") and m.get("quote") == "USDT" and m.get("active")]
            return [{"symbol": s, "ticker": s.split(":")[0], "volume_24h": 0, "last_price": 0} for s in symbols[:limit]]

    def get_market_symbols_by_volatility(
        self,
        limit: int = 25,
        min_volume: Optional[float] = None
    ) -> List[Dict[str, Any]]:
        """
        Screener Cuantitativo de Volatilidad Saludable (Anti-Shitcoins y Anti-Estancamiento):
        Filtra pares cuyo movimiento 24h esté dentro de la franja óptima:
        - Mínimo (ej. 3.0%) para descartar activos planos/estancados.
        - Máximo (ej. 16.0%) para descartar memecoins y pumps manipulados.
        - Volumen saludable (ej. >= $6,000,000 USDT) para evitar baja liquidez.
        Ordena priorizando liquidez y actividad institucional.
        """
        self.ensure_markets()
        if min_volume is None:
            min_volume = float(getattr(config, "MIN_24H_VOLUME_USDT", 6000000.0))

        max_vol = float(getattr(config, "MAX_24H_VOLATILITY_PCT", 16.0))
        min_vol_pct = float(getattr(config, "MIN_24H_VOLATILITY_PCT", 3.0))

        try:
            tickers = self.exchange.fetch_tickers()
            healthy_pairs = []
            for s, t in tickers.items():
                if ":USDT" in s:
                    vol = float(t.get("quoteVolume", 0) or t.get("baseVolume", 0) or 0.0)
                    if vol < min_volume:
                        continue
                    high = float(t.get("high") or 0.0)
                    low = float(t.get("low") or 0.0)
                    last = float(t.get("last") or 0.0)
                    percentage = float(t.get("percentage") or 0.0)

                    # Rango de amplitud 24h o porcentaje absoluto
                    if low > 0 and high >= low:
                        range_pct = ((high - low) / low) * 100.0
                    else:
                        range_pct = abs(percentage)

                    # FILTRO: Descartar si excede el techo máximo (hiper-volátil) o no llega al mínimo (estancado)
                    if range_pct > max_vol:
                        continue
                    if range_pct < min_vol_pct:
                        continue

                    healthy_pairs.append({
                        "symbol": s,
                        "ticker": s.split(":")[0],
                        "volume_24h": vol,
                        "last_price": last,
                        "volatility_pct": round(range_pct, 2),
                        "change_pct": round(percentage, 2)
                    })

            # Ordenar priorizando mayor volumen institucional dentro de la franja saludable
            healthy_pairs.sort(key=lambda x: x["volume_24h"], reverse=True)
            return healthy_pairs[:limit]
        except Exception as e:
            add_system_log("WARNING", f"Error en screener de volatilidad saludable: {e}")
            return self.get_market_symbols_by_volume(limit=limit)

