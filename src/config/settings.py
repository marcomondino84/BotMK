"""
Módulo de configuración centralizada y tipada utilizando Pydantic.
"""

from pydantic_settings import BaseSettings, SettingsConfigDict
from pydantic import Field
from typing import Literal


class Settings(BaseSettings):
    """
    Configuración global de la aplicación.
    Carga variables de entorno desde el archivo .env o del sistema.
    """
    # Credenciales BingX
    BINGX_API_KEY: str = Field(default="", description="Clave de API de BingX")
    BINGX_SECRET_KEY: str = Field(default="", description="Clave secreta de BingX")
    
    # Modo de ejecución
    ENVIRONMENT: Literal["demo", "live"] = Field(
        default="demo",
        description="Entorno de operativa: demo (testnet/sandbox) o live (producción)"
    )
    DEFAULT_MARKET: Literal["swap", "spot"] = Field(
        default="swap",
        description="Tipo de mercado predeterminado: swap (perpetuos USDT-M) o spot"
    )
    
    # Parámetros cuantitativos y de riesgo
    DEFAULT_SYMBOL: str = Field(default="BTC/USDT:USDT", description="Par base de trading")
    DEFAULT_LEVERAGE: int = Field(default=5, ge=1, le=125, description="Apalancamiento predeterminado")
    MAX_RISK_PER_TRADE_PCT: float = Field(
        default=1.5,
        ge=0.1,
        le=10.0,
        description="Porcentaje máximo de capital a arriesgar por trade"
    )
    MAX_DAILY_DRAWDOWN_PCT: float = Field(
        default=5.0,
        ge=1.0,
        le=50.0,
        description="Límite de pérdida diaria permitida antes de pausar operaciones"
    )

    # Configuración de FastAPI
    API_HOST: str = Field(default="0.0.0.0", description="Host del servidor API")
    API_PORT: int = Field(default=8000, description="Puerto del servidor API")
    WEBHOOK_SECRET: str = Field(
        default="super_secret_token",
        description="Token de autenticación para webhooks entrantes (TradingView)"
    )

    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore"
    )


# Instancia única reutilizable en toda la aplicación
settings = Settings()
