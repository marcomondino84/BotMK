"""
Motor central (Trading Engine) que orquesta la conexión con el exchange,
el análisis cuantitativo, el control de riesgo y la ejecución de operaciones.
"""

from src.config.settings import settings
from src.exchange.bingx_client import BingXClient
from src.risk.risk_manager import RiskManager
from src.execution.order_manager import OrderManager
from src.utils.logger import logger


class TradingEngine:
    """
    Orquestador principal del ciclo de vida del bot de trading.
    """

    def __init__(self):
        logger.info("Iniciando componentes del Trading Engine...")
        self.settings = settings
        self.client = BingXClient()
        self.risk_manager = RiskManager()
        self.order_manager = OrderManager(self.client, self.risk_manager)
        self.is_running = False

    def initialize(self) -> bool:
        """Comprueba conectividad y estado inicial de la cuenta."""
        logger.info(f"Modo de ejecución: {self.settings.ENVIRONMENT.upper()}")
        logger.info(f"Mercado objetivo: {self.settings.DEFAULT_MARKET.upper()}")
        
        if not self.settings.BINGX_API_KEY:
            logger.warning(
                "No se han especificado credenciales de BingX en .env. "
                "El bot operará en modo solo-lectura / simulación hasta configurarlas."
            )
            return False

        return self.client.check_connection()

    def start(self):
        """Inicia el ciclo de ejecución del bot."""
        self.is_running = True
        logger.info("Trading Engine iniciado y listo para recibir eventos.")

    def stop(self):
        """Detiene el bot de forma segura."""
        self.is_running = False
        logger.info("Trading Engine detenido.")

    def process_signal(self, signal: dict) -> dict:
        """
        Procesa una señal de trading externa o interna:
        signal = {
            "symbol": "BTC/USDT",
            "action": "BUY" | "SELL",
            "stop_loss": float,
            "take_profit": float,
            "risk_percent": float (opcional)
        }
        """
        raw_symbol = signal.get("symbol", self.settings.DEFAULT_SYMBOL)
        action = signal.get("action", "").upper()
        stop_loss = signal.get("stop_loss")
        take_profit = signal.get("take_profit")
        risk_pct = signal.get("risk_percent")

        if action not in ["BUY", "SELL"]:
            raise ValueError(f"Acción no soportada: {action}. Debe ser BUY o SELL.")

        # Cargar mercados si no están cargados
        if not self.client.exchange.markets:
            self.client.exchange.load_markets()

        # Normalizar símbolo según tipo de mercado (swap o spot)
        symbol = raw_symbol
        if self.settings.DEFAULT_MARKET == "swap" and ":" not in symbol:
            candidate = f"{symbol}:USDT"
            if candidate in self.client.exchange.markets:
                symbol = candidate

        if symbol not in self.client.exchange.markets:
            raise ValueError(f"El símbolo {symbol} no existe en los mercados disponibles de BingX.")

        market_info = self.client.exchange.markets[symbol]

        # Obtener precio actual de mercado
        ticker = self.client.fetch_ticker(symbol)
        current_price = ticker.get("last") or ticker.get("close")
        if not current_price:
            raise ValueError(f"No se pudo obtener el precio actual para {symbol}.")

        logger.info(f"Símbolo: {symbol} | Precio de Mercado: ${current_price:.2f}")

        # Obtener balance disponible
        balance = self.client.fetch_balance()
        free_balances = balance.get("free", {})
        # En demo Swap suele ser VST, en Live o Spot suele ser USDT
        capital = (
            free_balances.get("USDT")
            or free_balances.get("VST")
            or balance.get("total", {}).get("USDT")
            or balance.get("total", {}).get("VST")
            or 1000.0  # Fallback seguro
        )

        logger.info(f"Capital disponible para cálculo de riesgo: ${capital:.2f}")

        # Calcular tamaño de posición por riesgo
        if stop_loss is None:
            raise ValueError("El Stop Loss es mandatario para el cálculo cuantitativo de riesgo.")

        raw_size = self.risk_manager.calculate_position_size(
            capital=capital,
            entry_price=current_price,
            stop_loss_price=stop_loss,
            risk_pct=risk_pct
        )

        adjusted_size = self.risk_manager.adjust_to_market_precision(
            raw_size,
            market_info,
            exchange=self.client.exchange,
            symbol=symbol
        )

        if not self.risk_manager.validate_order(adjusted_size):
            raise ValueError(f"Tamaño de orden no válido tras ajuste: {adjusted_size}")

        logger.info(f"Tamaño final ajustado para {symbol}: {adjusted_size}")

        # Despachar orden
        order = self.order_manager.execute_bracket_order(
            symbol=symbol,
            side="buy" if action == "BUY" else "sell",
            amount=adjusted_size,
            stop_loss=stop_loss,
            take_profit=take_profit
        )

        return {
            "status": "success",
            "order_id": order.get("id"),
            "symbol": symbol,
            "action": action,
            "executed_price": current_price,
            "position_size": adjusted_size,
            "stop_loss": stop_loss,
            "take_profit": take_profit,
            "risk_percent": risk_pct or self.risk_manager.max_risk_pct,
            "order_details": order
        }
