"""
Definición de endpoints FastAPI para monitorización, estado y recepción de webhooks.
"""

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, Field
from typing import Optional, Dict, Any
from src.config.settings import settings
from src.utils.logger import logger

router = APIRouter(tags=["Trading Bot"])


class WebhookPayload(BaseModel):
    """Esquema de señal de trading (TradingView u origen externo)."""
    symbol: str = Field(default="BTC/USDT", description="Par a operar")
    action: str = Field(..., description="Acción a tomar: BUY o SELL")
    stop_loss: float = Field(..., description="Nivel de Stop Loss mandatario para gestión de riesgo")
    take_profit: Optional[float] = Field(default=None, description="Nivel de Take Profit opcional")
    risk_percent: Optional[float] = Field(default=None, description="Porcentaje de riesgo para este trade")
    secret: Optional[str] = Field(default=None, description="Token secreto para validación de origen")
    extra: Optional[Dict[str, Any]] = Field(default_factory=dict, description="Metadatos adicionales")


@router.get("/health")
def health_check():
    """Endpoint de comprobación de salud del servicio (Heartbeat)."""
    return {
        "status": "online",
        "environment": settings.ENVIRONMENT,
        "market": settings.DEFAULT_MARKET
    }


@router.post("/webhook/signal")
async def receive_signal(payload: WebhookPayload):
    """
    Recibe señales externas (ej. alertas de TradingView) y las procesa a través del TradingEngine.
    """
    # Validar secret si está configurado en .env
    if settings.WEBHOOK_SECRET and payload.secret and payload.secret != settings.WEBHOOK_SECRET:
        logger.warning("Intento de acceso no autorizado al webhook de señales.")
        raise HTTPException(status_code=401, detail="Token secreto inválido.")

    logger.info(f"Señal recibida: {payload.action} {payload.symbol} | SL: {payload.stop_loss} | TP: {payload.take_profit}")

    # Importación diferida para evitar dependencias circulares
    from main import engine

    try:
        trade_result = engine.process_signal(payload.model_dump())
        return {
            "status": "executed",
            "data": trade_result
        }
    except Exception as err:
        logger.error(f"Error procesando señal: {err}")
        raise HTTPException(status_code=400, detail=str(err))
