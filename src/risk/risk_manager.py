"""
Módulo de Gestión Cuantitativa de Riesgo y Capital (Risk Management).
Protege la cuenta validando límites de pérdidas, tamaño de posición y drawdown.
"""

from src.config.settings import settings
from src.utils.logger import logger


class RiskManager:
    """
    Controlador de riesgo previo a la ejecución de órdenes.
    """

    def __init__(
        self,
        max_risk_pct: float = settings.MAX_RISK_PER_TRADE_PCT,
        max_drawdown_pct: float = settings.MAX_DAILY_DRAWDOWN_PCT
    ):
        self.max_risk_pct = max_risk_pct
        self.max_drawdown_pct = max_drawdown_pct

    def calculate_position_size(
        self,
        capital: float,
        entry_price: float,
        stop_loss_price: float,
        risk_pct: float = None
    ) -> float:
        """
        Calcula el tamaño de posición (en unidades del activo base)
        basado en la pérdida monetaria máxima permitida por trade:
        
        Riesgo Monetario = Capital * (risk_pct / 100)
        Distancia al SL = |entry_price - stop_loss_price|
        Tamaño = Riesgo Monetario / Distancia al SL
        """
        if entry_price <= 0 or stop_loss_price <= 0:
            raise ValueError("Los precios de entrada y stop loss deben ser positivos.")

        price_distance = abs(entry_price - stop_loss_price)
        if price_distance == 0:
            logger.warning("El precio de entrada coincide con el stop loss. Retornando tamaño 0.")
            return 0.0

        effective_risk = risk_pct if risk_pct is not None else self.max_risk_pct
        risk_amount = capital * (effective_risk / 100.0)
        position_size = risk_amount / price_distance

        logger.info(
            f"Cálculo de riesgo: Capital=${capital:.2f}, Riesgo=${risk_amount:.2f} "
            f"({effective_risk}%), Distancia SL=${price_distance:.2f}, Tamaño Sugerido={position_size:.4f}"
        )
        return position_size

    def adjust_to_market_precision(
        self,
        amount: float,
        market: dict,
        exchange = None,
        symbol: str = None
    ) -> float:
        """
        Ajusta la cantidad a la precisión y límites de lote mínimo del mercado de BingX.
        Utiliza el método amount_to_precision de CCXT si está disponible, o redondeo por tick size.
        """
        import math

        if exchange and symbol:
            try:
                prec_str = exchange.amount_to_precision(symbol, amount)
                return float(prec_str)
            except Exception as e:
                logger.warning(f"Error al usar amount_to_precision de CCXT: {e}. Usando fallback manual.")

        precision_val = market.get("precision", {}).get("amount", 0.0001)
        limits = market.get("limits", {}).get("amount", {})
        min_amount = limits.get("min", 0.0001) or 0.0001
        max_amount = limits.get("max", float("inf")) or float("inf")

        # Determinar si precision_val es tick_size (ej. 0.0001) o número de decimales (ej. 4)
        if isinstance(precision_val, float) and precision_val < 1.0:
            decimals = max(0, int(round(-math.log10(precision_val))))
        else:
            decimals = int(precision_val)

        factor = 10 ** decimals
        adjusted_amount = math.floor(amount * factor) / factor

        if adjusted_amount < min_amount:
            logger.warning(
                f"Tamaño {adjusted_amount} menor al mínimo permitido ({min_amount}) por el mercado."
            )
            return 0.0

        if adjusted_amount > max_amount:
            adjusted_amount = max_amount

        return adjusted_amount

    def validate_order(self, position_size: float, max_allowed_size: float = float("inf")) -> bool:
        """
        Verifica que el tamaño propuesto no supere los límites de apalancamiento y capital.
        """
        if position_size <= 0:
            logger.warning("Orden rechazada: Tamaño de posición menor o igual a cero.")
            return False
            
        if position_size > max_allowed_size:
            logger.warning(
                f"Orden rechazada: Tamaño propuesto ({position_size:.4f}) excede "
                f"el máximo permitido ({max_allowed_size:.4f})."
            )
            return False

        return True
