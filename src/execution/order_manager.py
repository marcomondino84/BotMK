"""
Gestor de ejecución de órdenes en BingX.
Responsable de coordinar el envío de órdenes límite/mercado, Stop Loss y Take Profit.
"""

from typing import Dict, Any, Optional
from src.exchange.bingx_client import BingXClient
from src.risk.risk_manager import RiskManager
from src.utils.logger import logger


class OrderManager:
    """
    Controla el ciclo de vida y ejecución de órdenes.
    """

    def __init__(self, client: BingXClient, risk_manager: RiskManager):
        self.client = client
        self.risk_manager = risk_manager

    def execute_market_order(
        self,
        symbol: str,
        side: str,  # 'buy' o 'sell'
        amount: float,
        params: Optional[Dict[str, Any]] = None
    ) -> Dict[str, Any]:
        """
        Ejecuta una orden a mercado en BingX previa validación.
        """
        params = params or {}
        logger.info(f"Enviando orden a mercado: {side.upper()} {amount} {symbol}")

        try:
            order = self.client.exchange.create_order(
                symbol=symbol,
                type="market",
                side=side.lower(),
                amount=amount,
                params=params
            )
            logger.info(f"Orden ejecutada con éxito. ID: {order.get('id', 'N/A')}")
            return order
        except Exception as err:
            logger.error(f"Fallo al ejecutar orden a mercado para {symbol}: {err}")
            raise err

    def execute_bracket_order(
        self,
        symbol: str,
        side: str,
        amount: float,
        stop_loss: Optional[float] = None,
        take_profit: Optional[float] = None,
        extra_params: Optional[Dict[str, Any]] = None
    ) -> Dict[str, Any]:
        """
        Ejecuta una orden a mercado principal con Stop Loss y Take Profit asociados.
        En BingX Swap Perpetuo, CCXT admite vincular SL y TP en la creación de la orden.
        """
        params: Dict[str, Any] = extra_params.copy() if extra_params else {}

        # Configurar SL y TP en params si aplican
        if stop_loss is not None:
            params["stopLoss"] = {"triggerPrice": stop_loss}
        if take_profit is not None:
            params["takeProfit"] = {"triggerPrice": take_profit}

        logger.info(
            f"Ejecutando Bracket Order: {side.upper()} {amount} {symbol} | "
            f"SL={stop_loss} | TP={take_profit}"
        )

        try:
            order = self.client.exchange.create_order(
                symbol=symbol,
                type="market",
                side=side.lower(),
                amount=amount,
                params=params
            )
            logger.info(f"Bracket Order enviada exitosamente. ID: {order.get('id', 'N/A')}")
            return order
        except Exception as err:
            logger.warning(
                f"No se pudo adjuntar SL/TP directo en un solo llamado ({err}). "
                "Intentando enviar orden principal a mercado primero..."
            )
            # Intentar orden principal limpia
            order = self.client.exchange.create_order(
                symbol=symbol,
                type="market",
                side=side.lower(),
                amount=amount
            )
            logger.info(f"Orden principal ejecutada: ID {order.get('id')}. Procediendo a colocar órdenes SL/TP condicionales...")

            # Colocar SL condicional si fue especificado
            close_side = "sell" if side.lower() == "buy" else "buy"
            if stop_loss is not None:
                try:
                    sl_order = self.client.exchange.create_order(
                        symbol=symbol,
                        type="STOP_MARKET",
                        side=close_side,
                        amount=amount,
                        params={"stopPrice": stop_loss, "reduceOnly": True}
                    )
                    logger.info(f"Stop Loss colocado con éxito. ID: {sl_order.get('id')}")
                except Exception as sl_err:
                    logger.error(f"Error al colocar Stop Loss condicional: {sl_err}")

            # Colocar TP condicional si fue especificado
            if take_profit is not None:
                try:
                    tp_order = self.client.exchange.create_order(
                        symbol=symbol,
                        type="TAKE_PROFIT_MARKET",
                        side=close_side,
                        amount=amount,
                        params={"stopPrice": take_profit, "reduceOnly": True}
                    )
                    logger.info(f"Take Profit colocado con éxito. ID: {tp_order.get('id')}")
                except Exception as tp_err:
                    logger.error(f"Error al colocar Take Profit condicional: {tp_err}")

            return order
        except Exception as final_err:
            err_str = str(final_err)
            if "100004" in err_str or "Permission denied" in err_str:
                logger.error(
                    "[PERMISOS INSUFICIENTES] Tu API Key de BingX no tiene habilitado el permiso "
                    "de 'Perpetual Futures Trading' (Trading de Futuros Perpetuos). "
                    "Por favor, actívalo en https://bingx.com/en/account/api editando tu API Key."
                )
            raise final_err
