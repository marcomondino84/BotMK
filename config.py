"""
Módulo de configuración centralizada con Pydantic Settings.
Define parámetros de conexión a BingX, catálogo multiactivo y apalancamiento adaptativo.
"""

from typing import Dict, Any, List
from pydantic_settings import BaseSettings, SettingsConfigDict
from pydantic import Field


class Config(BaseSettings):
    """Configuración principal del Bot."""
    # Credenciales de API BingX
    BINGX_API_KEY: str = Field(default="", description="Clave de API de BingX")
    BINGX_SECRET_KEY: str = Field(default="", description="Clave secreta de BingX")

    # Modo de ejecución (Predeterminado: Real USDT)
    IS_DEMO: bool = Field(default=False, description="True para Testnet/Sandbox/VST, False para Cuenta Real")

    # Gestión de posición y margen (Equitativo o Fijo)
    POSITION_SIZING_MODE: str = Field(
        default="percent_total",
        description="'percent_total' (porcentaje del balance total) o 'fixed_usdt' (monto fijo en USDT por operación)"
    )
    POSITION_BALANCE_PCT: float = Field(
        default=10.0,
        ge=1.0,
        le=100.0,
        description="Porcentaje del balance total a usar como margen en cada operación (10%)"
    )
    FIXED_POSITION_MARGIN_USDT: float = Field(
        default=10.0,
        ge=1.0,
        le=10000.0,
        description="Monto fijo de margen en USDT por cada trade si se usa el modo 'fixed_usdt'"
    )

    # Nivel 4: Blindaje a Break-Even Automático (Riesgo Cero) y por Estancamiento Temporal
    BREAK_EVEN_ENABLED: bool = Field(
        default=True,
        description="Activa mover el Stop Loss al precio de entrada para asegurar riesgo cero"
    )
    BREAK_EVEN_TRIGGER_PCT: float = Field(
        default=1.0,
        ge=0.2,
        le=15.0,
        description="Porcentaje de ganancia flotante para gatillar la protección Break-Even (+1.0%)"
    )
    BREAK_EVEN_OFFSET_PCT: float = Field(
        default=0.08,
        ge=0.0,
        le=2.0,
        description="Holgura sobre precio de entrada para pagar comisiones de apertura y cierre (+0.08%)"
    )
    STAGNANT_BE_ENABLED: bool = Field(
        default=True,
        description="Activa mover el Stop Loss a Break-Even si la posición se estanca sin movimiento"
    )
    STAGNANT_HOURS_THRESHOLD: float = Field(
        default=4.0,
        ge=1.0,
        le=24.0,
        description="Horas de inactividad/estancamiento antes de mover el Stop Loss a Break-Even (default: 4.0h)"
    )

    # Parámetros Estrategia SMC / ICT Multi-Temporalidad
    MACRO_TIMEFRAME: str = Field(default="4h", description="Temporalidad macro directriz institucional (4h)")
    MACRO_EMA_PERIOD: int = Field(default=50, description="Periodo de EMA directriz de tendencia en 4h (default: 50)")
    SMC_SWEEP_LOOKBACK: int = Field(default=15, description="Lookback de velas para niveles de liquidez en 15m")
    SMC_ICHOCH_LOOKBACK: int = Field(default=5, description="Lookback de velas para fractales de quiebre iCHoCH")
    SMC_TOLERANCE_PCT: float = Field(default=0.08, description="Tolerancia porcentual para detectar mechas de barrida")
    MIN_24H_VOLUME_USDT: float = Field(default=6000000.0, description="Volumen mínimo 24h en USDT para screener de volatilidad")
    MAX_24H_VOLATILITY_PCT: float = Field(default=16.0, description="Tope máximo de volatilidad 24h permitido (%) para evitar memecoins/pumps descontrolados")
    MIN_24H_VOLATILITY_PCT: float = Field(default=3.0, description="Mínimo de volatilidad 24h requerido (%) para descartar activos estancados")
    MIN_MARKET_AGE_DAYS: int = Field(default=15, description="Antigüedad mínima requerida en días para filtrar activos recién listados")
    DEFAULT_SCANNER_MODE: str = Field(default="top_volatility", description="Modo default: 'top_volatility', 'watchlist' o 'full_market'")

    # Nivel 1: Filtro Anti-Lateralidad (ADX > 22)
    ADX_FILTER_ENABLED: bool = Field(
        default=True,
        description="Bloquea entradas automáticas si el ADX < 22 en 15m para evitar pérdidas en mercados laterales"
    )
    ADX_MIN_THRESHOLD: float = Field(
        default=22.0,
        ge=10.0,
        le=50.0,
        description="Umbral mínimo de fuerza de tendencia ADX para validar operaciones (default: 22.0)"
    )

    # Nivel 1.5: Centinela Maestro de Régimen de Mercado (Bitcoin y S&P 500)
    BTC_SENTINEL_ENABLED: bool = Field(
        default=True,
        description="Bloquea compras en altcoins si Bitcoin entra en corrección en 15m, y bloquea ventas si está en impulso"
    )
    SP500_SENTINEL_ENABLED: bool = Field(
        default=True,
        description="Bloquea compras en índices y acciones si el S&P 500 entra en corrección, y bloquea ventas si está en impulso"
    )
    BENCHMARK_TIMEFRAME: str = Field(
        default="15m",
        description="Temporalidad de análisis del Centinela (default: 15m)"
    )
    BENCHMARK_EMA_FAST: int = Field(
        default=20,
        ge=5,
        le=50,
        description="Periodo de EMA rápida para el Centinela (default: 20)"
    )
    BENCHMARK_EMA_SLOW: int = Field(
        default=50,
        ge=20,
        le=200,
        description="Periodo de EMA lenta para el Centinela (default: 50)"
    )

    # Reglas de salida obligatorias (SL / TP)
    DEFAULT_RISK_RATIO: float = Field(
        default=2.0,
        ge=1.0,
        le=5.0,
        description="Ratio Riesgo/Beneficio mínimo para Take Profit (1:2.0)"
    )
    DEFAULT_SL_PCT: float = Field(
        default=1.5,
        ge=0.2,
        le=10.0,
        description="Porcentaje técnico base de Stop Loss respecto al precio de entrada"
    )
    MIN_SL_PCT: float = Field(
        default=0.8,
        ge=0.3,
        le=5.0,
        description="Distancia mínima de Stop Loss para evitar que ruido o comisiones salten el SL de inmediato (default: 0.8%)"
    )

    # Protección Anti-Overtrading / Anti-Revenge (Enfriamiento por activo)
    COOLDOWN_AFTER_LOSS_MINUTES: int = Field(
        default=120,
        ge=5,
        le=720,
        description="Minutos de enfriamiento obligatorio tras tocar Stop Loss antes de volver a operar el mismo activo (default: 120 min)"
    )
    COOLDOWN_AFTER_WIN_MINUTES: int = Field(
        default=15,
        ge=0,
        le=180,
        description="Minutos de enfriamiento tras cerrar en Take Profit antes de volver a operar el mismo activo (default: 15 min)"
    )
    MAX_LOSSES_PER_SYMBOL_DAILY: int = Field(
        default=2,
        ge=1,
        le=10,
        description="Máximo de pérdidas permitidas en el mismo activo durante el día antes de bloquearlo hasta mañana"
    )

    # Rangos de apalancamiento adaptativo y modo automático antiliquidación
    AUTO_LEVERAGE_BY_SL: bool = Field(
        default=True,
        description="True para calcular apalancamiento automático antiliquidación según distancia al Stop Loss"
    )
    SAFETY_MARGIN_FACTOR: float = Field(
        default=2.0,
        ge=1.2,
        le=5.0,
        description="Factor de seguridad: la liquidación estará a este múltiplo de distancia del Stop Loss (ej: 2.0x)"
    )
    MAX_ALLOWED_LEVERAGE: int = Field(
        default=10,
        ge=2,
        le=50,
        description="Tope máximo de apalancamiento de seguridad permitido (default: 10x)"
    )

    # Valores base por categoría si el modo automático está desactivado
    LEVERAGE_INDICES: int = Field(default=20, description="Apalancamiento para Índices Bursátiles (SP500, NASDAQ, DOW)")
    LEVERAGE_COMMODITIES: int = Field(default=15, description="Apalancamiento para Oro / Petróleo")
    LEVERAGE_CRYPTO_MAJORS: int = Field(default=15, description="Apalancamiento para BTC y ETH")
    LEVERAGE_CRYPTO_ALTS: int = Field(default=12, description="Apalancamiento para Altcoins (SOL, NEAR, etc.)")
    LEVERAGE_STOCKS: int = Field(default=10, description="Apalancamiento para Acciones (TSLA, NVDA)")

    # Circuit Breaker
    MAX_CONSECUTIVE_LOSSES: int = Field(
        default=3,
        ge=1,
        description="Pérdidas consecutivas máximas antes del apagado automático"
    )
    MAX_DAILY_DRAWDOWN_PCT: float = Field(
        default=5.0,
        ge=1.0,
        le=50.0,
        description="Caída diaria porcentual máxima permitida antes de pausar el bot"
    )

    # Auto Scanner autónomo
    AUTO_SCANNER_INTERVAL_SECONDS: int = Field(default=60, description="Intervalo de escaneo en segundos")

    # Servidor FastAPI y Webhook
    HOST: str = Field(default="0.0.0.0", description="Host del servidor web")
    PORT: int = Field(default=8000, description="Puerto del servidor web")
    WEBHOOK_SECRET: str = Field(default="", description="Token secreto opcional para TradingView")

    # Notificaciones WhatsApp y Telegram
    WHATSAPP_PHONE: str = Field(default="5493462698956", description="Número de WhatsApp formato internacional (Argentina +549)")
    WHATSAPP_APIKEY: str = Field(default="", description="API Key de CallMeBot para WhatsApp")
    WHATSAPP_ENABLED: bool = Field(default=True, description="Habilitar notificaciones WhatsApp")

    # Notificaciones Telegram (Recomendado - 100% confiable y sin bloqueos)
    TELEGRAM_BOT_TOKEN: str = Field(default="", description="Token de BotFather en Telegram")
    TELEGRAM_CHAT_ID: str = Field(default="", description="Chat ID de Telegram donde recibir las alertas")
    TELEGRAM_ENABLED: bool = Field(default=True, description="Habilitar notificaciones Telegram")

    # Gestión de concurrencia y límites
    MAX_SIMULTANEOUS_POSITIONS: int = Field(
        default=3,
        ge=1,
        le=10,
        description="Límite máximo de operaciones simultáneas abiertas en toda la cuenta"
    )

    # Base de Datos
    DATABASE_URL: str = Field(default="sqlite:///./data/trading_bot.db", description="URL de conexión SQLite")

    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore"
    )

    def reload_from_env(self):
        """Recarga las variables desde el archivo .env actualizado."""
        from dotenv import dotenv_values
        new_values = dotenv_values(".env")
        for key, val in new_values.items():
            if hasattr(self, key) and val is not None:
                orig_type = type(getattr(self, key))
                if orig_type == bool:
                    setattr(self, key, str(val).lower() in ["true", "1", "yes"])
                elif orig_type == int:
                    try:
                        setattr(self, key, int(val))
                    except ValueError:
                        pass
                elif orig_type == float:
                    try:
                        setattr(self, key, float(val))
                    except ValueError:
                        pass
                else:
                    setattr(self, key, str(val))


config = Config()

# Catálogo multiactivo predefinido y aliases hacia BingX
ASSET_CATALOG = {
    # Criptomonedas
    "BTC/USDT": {"category": "crypto_major", "name": "Bitcoin", "target_sl": 1.5, "symbol": "BTC/USDT:USDT"},
    "ETH/USDT": {"category": "crypto_major", "name": "Ethereum", "target_sl": 1.8, "symbol": "ETH/USDT:USDT"},
    "SOL/USDT": {"category": "crypto_alt", "name": "Solana", "target_sl": 2.5, "symbol": "SOL/USDT:USDT"},
    
    # Índices Bursátiles
    "SPX500": {"category": "index", "name": "S&P 500", "target_sl": 0.5, "symbol": "NCSISP5002USD/USDT:USDT"},
    "NASDAQ": {"category": "index", "name": "Nasdaq 100", "target_sl": 0.6, "symbol": "NCSINASDAQ1002USD/USDT:USDT"},
    "DOW": {"category": "index", "name": "Dow Jones", "target_sl": 0.5, "symbol": "NCSIDOWJONES2USD/USDT:USDT"},

    # Materias Primas / Acciones
    "GOLD": {"category": "commodity", "name": "Oro (XAU/USD)", "target_sl": 0.8, "symbol": "NCCOGOLD2USD/USDT:USDT"},
    "OIL": {"category": "commodity", "name": "Petróleo WTI", "target_sl": 1.5, "symbol": "NCCO1OILWTI2USD/USDT:USDT"},
    "TSLA": {"category": "stock", "name": "Tesla Inc.", "target_sl": 2.0, "symbol": "NCSKTSLA2USD/USDT:USDT"},
    "NVDA": {"category": "stock", "name": "Nvidia Corp.", "target_sl": 2.0, "symbol": "NCSKNVDA2USD/USDT:USDT"}
}

# Aliases para normalizar alertas que vengan de TradingView o del usuario
TICKER_ALIASES = {
    "BTC": "BTC/USDT",
    "BTCUSDT": "BTC/USDT",
    "ETH": "ETH/USDT",
    "ETHUSDT": "ETH/USDT",
    "SOL": "SOL/USDT",
    "SOLUSDT": "SOL/USDT",
    "SP500": "SPX500",
    "SPX": "SPX500",
    "US500": "SPX500",
    "NDX": "NASDAQ",
    "US100": "NASDAQ",
    "NAS100": "NASDAQ",
    "US30": "DOW",
    "DJI": "DOW",
    "DJI30": "DOW",
    "XAU": "GOLD",
    "XAUUSD": "GOLD",
    "XAU/USD": "GOLD",
    "XAUT": "GOLD",
    "WTI": "OIL",
    "CRUDE": "OIL",
    "TESLA": "TSLA",
    "NVIDIA": "NVDA"
}
