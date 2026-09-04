"""
Cliente de conectividad con BingX utilizando la librería CCXT.
Soporta mercados Spot y Futuros Swap (Perpetuos USDT-M) con manejo de errores y sandbox.
"""

from typing import Dict, Any, Optional
import ccxt
from src.config.settings import settings
from src.utils.logger import logger


class BingXClient:
    """
    Wrapper optimizado para la API de BingX vía CCXT.
    """

    def __init__(
        self,
        api_key: Optional[str] = None,
        secret_key: Optional[str] = None,
        testnet: Optional[bool] = None,
        market_type: Optional[str] = None
    ):
        self.api_key = api_key or settings.BINGX_API_KEY
        self.secret_key = secret_key or settings.BINGX_SECRET_KEY
        self.testnet = testnet if testnet is not None else (settings.ENVIRONMENT == "demo")
        self.market_type = market_type or settings.DEFAULT_MARKET

        self.exchange = self._init_exchange()

    def _init_exchange(self) -> ccxt.bingx:
        """Inicializa la instancia de CCXT BingX con las opciones configuradas."""
        exchange_params: Dict[str, Any] = {
            "apiKey": self.api_key,
            "secret": self.secret_key,
            "enableRateLimit": True,
            "options": {
                "defaultType": self.market_type,  # 'swap' o 'spot'
            }
        }

        exchange = ccxt.bingx(exchange_params)

        if self.testnet:
            # Habilitar sandbox/testnet si está configurado
            try:
                exchange.set_sandbox_mode(True)
                logger.info("BingX configurado en modo Sandbox / Testnet.")
            except Exception as e:
                logger.warning(f"No se pudo habilitar el modo sandbox en BingX CCXT: {e}")

        return exchange

    def check_connection(self) -> bool:
        """Verifica si la conexión y las credenciales con BingX son válidas."""
        try:
            self.exchange.load_markets()
            logger.info("Conexión con BingX establecida y mercados cargados exitosamente.")
            return True
        except Exception as err:
            logger.error(f"Error al conectar con BingX: {err}")
            return False

    def fetch_balance(self) -> Dict[str, Any]:
        """Obtiene el balance de la cuenta."""
        return self.exchange.fetch_balance()

    def fetch_ticker(self, symbol: str) -> Dict[str, Any]:
        """Obtiene la cotización actual en tiempo real para un símbolo."""
        return self.exchange.fetch_ticker(symbol)
