"""
Cliente especializado para BingX Futuros Perpetuos y Estándar.
Soporta Cripto, Índices Bursátiles (SP500, NASDAQ, DOW), Materias Primas y Acciones.
Implementa Apalancamiento Adaptativo, dimensionamiento del 10% y conmutador dinámico Demo/Real.
"""

import math
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
        """Obtiene el balance libre y total de la cuenta activa (Demo o Real)."""
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

            return {
                "asset": asset,
                "is_demo": self.is_demo,
                "total": round(total, 2),
                "free": round(free, 2),
                "used": round(used, 2)
            }
        except Exception as err:
            add_system_log("ERROR", f"Error al consultar balance: {err}")
            return {"asset": "USDT", "is_demo": self.is_demo, "total": 0.0, "free": 0.0, "used": 0.0}

    def get_ticker_price(self, exchange_symbol: str) -> float:
        """Obtiene el último precio de mercado."""
        self.ensure_markets()
        ticker = self.exchange.fetch_ticker(exchange_symbol)
        price = ticker.get("last") or ticker.get("close")
        if not price:
            raise ValueError(f"No se pudo obtener precio para {exchange_symbol}")
        return float(price)

    def set_leverage(self, exchange_symbol: str, leverage: int, side: Optional[str] = None):
        """Configura el apalancamiento en BingX para LONG y SHORT garantizando el Hedge Mode."""
        try:
            target_sides = [side] if side else ["LONG", "SHORT"]
            for s in target_sides:
                try:
                    self.exchange.set_leverage(leverage, exchange_symbol, params={"side": s})
                except Exception as inner:
                    add_system_log("DEBUG", f"Aviso set_leverage {s} en {exchange_symbol}: {inner}")
            add_system_log("INFO", f"Apalancamiento fijado en {leverage}x para {exchange_symbol}")
        except Exception as e:
            add_system_log("WARNING", f"Error fijando apalancamiento {exchange_symbol}: {e}")

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

                    # Si no está en open_orders, buscar en TradeRecord
                    if not sl_price or not tp_price:
                        clean_sym = symbol.replace("/USDT:USDT", "").replace("/USDT", "")
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
                        "dist_sl_pct": round(dist_sl_pct, 2)
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

        if free_balance <= 0:
            raise ValueError("Balance libre insuficiente para operar.")

        sizing_mode = getattr(config, "POSITION_SIZING_MODE", "percent_total")
        if sizing_mode == "fixed_usdt":
            target_margin = float(getattr(config, "FIXED_POSITION_MARGIN_USDT", 10.0))
            mode_desc = f"Monto Fijo ${target_margin:.2f} USDT"
        else:
            # Porcentaje del balance total para garantizar tamaño constante en todas las órdenes
            target_margin = total_balance * (balance_pct / 100.0)
            mode_desc = f"{balance_pct:.1f}% Balance Total (${target_margin:.2f} USDT)"

        # Ajuste por seguridad: no usar más del saldo libre disponible en la cuenta
        margin_to_use = min(target_margin, free_balance)
        if margin_to_use <= 0:
            raise ValueError("Saldo libre insuficiente para cubrir el margen requerido.")

        notional_value = margin_to_use * leverage
        raw_amount = notional_value / current_price

        try:
            prec_str = self.exchange.amount_to_precision(exchange_symbol, raw_amount)
            amount = float(prec_str)
        except Exception:
            amount = round(raw_amount, 4)

        market = self.exchange.markets.get(exchange_symbol, {})
        min_amount = market.get("limits", {}).get("amount", {}).get("min", 0.0001) or 0.0001
        if amount < min_amount:
            raise ValueError(f"Cantidad ({amount}) inferior al mínimo de {min_amount} para {exchange_symbol}")

        add_system_log(
            "INFO",
            f"Dimensionamiento ({mode_desc}): Margen=${margin_to_use:.2f} con {leverage}x -> Lote={amount} {exchange_symbol}"
        )
        return amount

    def check_and_apply_break_even(self, positions: List[Dict[str, Any]]) -> None:
        """
        NIVEL 4: Blindaje a Break-Even Automático (Riesgo Cero).
        Monitorea las posiciones abiertas y, en cuanto alcanzan el umbral de ganancia (+1.0%),
        mueve el Stop Loss al precio de entrada más comisiones (+0.08%).
        """
        if not getattr(config, "BREAK_EVEN_ENABLED", True) or not positions:
            return

        trigger_pct = float(getattr(config, "BREAK_EVEN_TRIGGER_PCT", 1.0))
        offset_pct = float(getattr(config, "BREAK_EVEN_OFFSET_PCT", 0.08))

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

            # Evaluar si la posición alcanzó el umbral para activar el Break-Even
            if gain_pct >= trigger_pct:
                try:
                    self.ensure_markets()
                    formatted_be_price = float(self.exchange.price_to_precision(symbol, be_price))

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
                    msg = (
                        f"🛡️ BREAK-EVEN ACTIVADO en {symbol} ({pos_side}): "
                        f"Ganancia alcanzó +{gain_pct:.2f}%. Stop Loss blindado a ${formatted_be_price} (Riesgo Cero)."
                    )
                    add_system_log("INFO", msg)

                    # 3. Notificación a Telegram
                    from notifications import notifier
                    tele_text = (
                        f"🛡️ *PROTECCIÓN BREAK-EVEN ACTIVADA* [Riesgo Cero]\n\n"
                        f"📌 Activo: *{symbol}* ({pos_side})\n"
                        f"📈 Ganancia alcanzada: *+{gain_pct:.2f}%*\n"
                        f"🎯 Entrada: ${entry_price}\n"
                        f"🛡️ Nuevo SL Blindado: *${formatted_be_price}*\n\n"
                        f"✨ _La operación ahora está protegida contra pérdidas._"
                    )
                    notifier.send_telegram(tele_text)

                except Exception as be_err:
                    add_system_log("ERROR", f"Fallo al aplicar Break-Even en {symbol}: {be_err}")

    def open_bracket_position(
        self,
        raw_symbol: str,
        side: str,
        custom_leverage: Optional[int] = None,
        custom_sl_pct: Optional[float] = None,
        risk_ratio: float = config.DEFAULT_RISK_RATIO
    ) -> Dict[str, Any]:
        """Abre posición con 10% de balance, apalancamiento adaptativo y SL/TP obligatorios."""
        self.ensure_markets()
        asset_info = self.resolve_symbol(raw_symbol)
        exchange_symbol = asset_info["exchange_symbol"]
        action = side.upper()

        sl_pct = custom_sl_pct or asset_info.get("target_sl", config.DEFAULT_SL_PCT)
        tp_pct = sl_pct * risk_ratio
        leverage = custom_leverage or self.get_adaptive_leverage(asset_info, sl_pct)

        pos_side = "LONG" if action == "BUY" else "SHORT"

        # 1. Configurar apalancamiento seguro para el lado exacto
        self.set_leverage(exchange_symbol, leverage, side=pos_side)
        add_system_log("INFO", f"🛡️ Apalancamiento asignado: {leverage}x para {exchange_symbol} (SL: {sl_pct:.2f}%)")

        # 2. Precio actual
        entry_price = self.get_ticker_price(exchange_symbol)

        # 3. Niveles SL y TP
        if action == "BUY":
            sl_price = entry_price * (1.0 - (sl_pct / 100.0))
            tp_price = entry_price * (1.0 + (tp_pct / 100.0))
            order_side = "buy"
            close_side = "sell"
        elif action == "SELL":
            sl_price = entry_price * (1.0 + (sl_pct / 100.0))
            tp_price = entry_price * (1.0 - (tp_pct / 100.0))
            order_side = "sell"
            close_side = "buy"
        else:
            raise ValueError(f"Acción '{action}' inválida.")

        sl_price = float(self.exchange.price_to_precision(exchange_symbol, sl_price))
        tp_price = float(self.exchange.price_to_precision(exchange_symbol, tp_price))

        # 4. Tamaño (10% balance libre)
        amount = self.calculate_position_size(exchange_symbol, entry_price, leverage)

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
            tp_price=tp_price
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

