"""
Servidor principal del Bot de Trading BingX Multiactivo.
Orquesta FastAPI, FileResponse, WebSockets en tiempo real, persistencia SQLite,
Auto-Scanner Multi-Temporalidad (5m+15m), Notificaciones WhatsApp y Conmutador Demo/Real.
"""

import asyncio
import json
import os
from contextlib import asynccontextmanager
from typing import List, Dict, Any, Optional

from fastapi import FastAPI, WebSocket, WebSocketDisconnect, HTTPException, Response
from fastapi.responses import FileResponse
from pydantic import BaseModel, Field
import uvicorn

from config import config, ASSET_CATALOG
from database import (
    init_db,
    get_bot_state,
    set_bot_active,
    get_performance_stats,
    get_recent_trades,
    get_recent_logs,
    add_system_log
)
from bingx_client import BingXFuturesClient
from auto_scanner import AutoScanner
from notifications import notifier


bingx = BingXFuturesClient()
auto_scanner = AutoScanner(bingx)


class WebSocketManager:
    def __init__(self):
        self.active_connections: List[WebSocket] = []

    async def connect(self, websocket: WebSocket):
        await websocket.accept()
        self.active_connections.append(websocket)

    def disconnect(self, websocket: WebSocket):
        if websocket in self.active_connections:
            self.active_connections.remove(websocket)

    async def broadcast(self, message: dict):
        for connection in list(self.active_connections):
            try:
                await connection.send_json(message)
            except Exception:
                self.disconnect(connection)


ws_manager = WebSocketManager()


async def background_streamer():
    """Streaming periódico de estado a clientes WebSockets."""
    last_log_id = 0
    while True:
        try:
            if ws_manager.active_connections:
                balance = bingx.get_balance()
                positions = bingx.get_open_positions()
                
                # Sincronizar automáticamente trades cerrados en BingX (por SL, TP o externo)
                await asyncio.to_thread(bingx.sync_closed_trades, positions)

                # Nivel 4: Evaluar y aplicar blindaje Break-Even si procede
                if positions:
                    await asyncio.to_thread(bingx.check_and_apply_break_even, positions)

                stats = get_performance_stats()
                state = get_bot_state()
                recent_trades = get_recent_trades(limit=25)

                await ws_manager.broadcast({
                    "type": "update",
                    "data": {
                        "balance": balance,
                        "positions": positions,
                        "trades": recent_trades,
                        "stats": stats,
                        "state": state,
                        "is_demo": bingx.is_demo,
                        "scanner_active": auto_scanner.is_running
                    }
                })

                recent_logs = get_recent_logs(limit=10)
                if recent_logs:
                    newest = recent_logs[-1]
                    if newest["id"] != last_log_id:
                        last_log_id = newest["id"]
                        await ws_manager.broadcast({
                            "type": "log",
                            "data": newest
                        })

        except Exception:
            pass

        await asyncio.sleep(3)


@asynccontextmanager
async def lifespan(app: FastAPI):
    init_db()
    state = get_bot_state()
    add_system_log("INFO", f"Servidor iniciado. Modo activo: {'DEMO (VST)' if bingx.is_demo else 'REAL (USDT)'}")

    # Restaurar Auto-Scanner si el usuario lo dejó encendido en la base de datos
    if state.get("scanner_active") and state.get("is_active"):
        auto_scanner.start()

    stream_task = asyncio.create_task(background_streamer())
    yield
    if auto_scanner._task and not auto_scanner._task.done():
        auto_scanner._task.cancel()
    stream_task.cancel()
    add_system_log("INFO", "Servidor apagándose.")


app = FastAPI(
    title="BotMK BingX Quant Terminal",
    version="2.2.0",
    lifespan=lifespan
)


@app.middleware("http")
async def add_ngrok_header(request, call_next):
    response = await call_next(request)
    response.headers["ngrok-skip-browser-warning"] = "true"
    return response


class SignalPayload(BaseModel):
    symbol: str = Field(default="BTC/USDT")
    signal: str = Field(...)
    secret: str = Field(default="")
    leverage: Optional[int] = None
    sl_pct: Optional[float] = None


class ManualOrderPayload(BaseModel):
    symbol: str
    side: str
    leverage: Optional[int] = None


class SwitchEnvPayload(BaseModel):
    is_demo: bool


class ConfigPayload(BaseModel):
    bingx_api_key: Optional[str] = None
    bingx_secret_key: Optional[str] = None
    is_demo: Optional[bool] = None
    position_sizing_mode: Optional[str] = None
    position_balance_pct: Optional[float] = None
    fixed_position_margin_usdt: Optional[float] = None
    break_even_enabled: Optional[bool] = None
    break_even_trigger_pct: Optional[float] = None
    break_even_offset_pct: Optional[float] = None
    stagnant_be_enabled: Optional[bool] = None
    stagnant_hours_threshold: Optional[float] = None
    macro_ema_period: Optional[int] = None
    min_24h_volume_usdt: Optional[float] = None
    adx_filter_enabled: Optional[bool] = None
    adx_min_threshold: Optional[float] = None
    default_risk_ratio: Optional[float] = None
    default_sl_pct: Optional[float] = None
    max_simultaneous_positions: Optional[int] = None
    max_consecutive_losses: Optional[int] = None
    max_daily_drawdown_pct: Optional[float] = None
    leverage_crypto_majors: Optional[int] = None
    leverage_crypto_alts: Optional[int] = None
    leverage_indices: Optional[int] = None
    leverage_commodities: Optional[int] = None
    leverage_stocks: Optional[int] = None
    auto_leverage_by_sl: Optional[bool] = None
    safety_margin_factor: Optional[float] = None
    max_allowed_leverage: Optional[int] = None
    telegram_enabled: Optional[bool] = None
    telegram_bot_token: Optional[str] = None
    telegram_chat_id: Optional[str] = None
    whatsapp_enabled: Optional[bool] = None
    whatsapp_phone: Optional[str] = None
    whatsapp_apikey: Optional[str] = None
    webhook_secret: Optional[str] = None


def update_env_file(updates: Dict[str, Any]):
    """Actualiza o añade variables en el archivo .env de forma limpia."""
    env_path = ".env"
    lines = []
    if os.path.exists(env_path):
        with open(env_path, "r", encoding="utf-8") as f:
            lines = f.readlines()

    applied_keys = set()
    new_lines = []
    for line in lines:
        stripped = line.strip()
        if stripped and not stripped.startswith("#") and "=" in stripped:
            key = stripped.split("=", 1)[0].strip()
            if key in updates and updates[key] is not None:
                new_val = str(updates[key])
                new_lines.append(f"{key}={new_val}\n")
                applied_keys.add(key)
                continue
        new_lines.append(line)

    for key, val in updates.items():
        if key not in applied_keys and val is not None:
            new_lines.append(f"{key}={val}\n")

    with open(env_path, "w", encoding="utf-8") as f:
        f.writelines(new_lines)


@app.get("/", response_class=FileResponse)
async def serve_dashboard():
    return FileResponse("templates/index.html")


@app.get("/favicon.ico")
async def favicon_ico():
    svg_icon = """<svg xmlns='http://www.w3.org/2000/svg' viewBox='0 0 100 100'><circle cx='50' cy='50' r='48' fill='#0f1422' stroke='#f59e0b' stroke-width='4'/><path d='M54 16 L28 54 L48 54 L44 84 L72 46 L52 46 Z' fill='#f59e0b'/></svg>"""
    return Response(content=svg_icon, media_type="image/svg+xml")


@app.websocket("/ws")
async def websocket_endpoint(websocket: WebSocket):
    await ws_manager.connect(websocket)
    try:
        logs = get_recent_logs(limit=25)
        for log in logs:
            await websocket.send_json({"type": "log", "data": log})

        while True:
            await websocket.receive_text()
    except WebSocketDisconnect:
        ws_manager.disconnect(websocket)
    except Exception:
        ws_manager.disconnect(websocket)


@app.get("/health")
def health_check():
    return {
        "status": "online",
        "is_demo": bingx.is_demo,
        "scanner_active": auto_scanner.is_running
    }


@app.get("/api/logs")
def get_logs(limit: int = 40):
    """Retorna los logs más recientes del sistema."""
    return get_recent_logs(limit=limit)


@app.get("/api/stats")
def get_stats():
    state = get_bot_state()
    stats = get_performance_stats()
    balance = bingx.get_balance()
    positions = bingx.get_open_positions()
    trades = get_recent_trades(limit=10)

    return {
        "state": state,
        "stats": stats,
        "balance": balance,
        "positions": positions,
        "trades": trades,
        "scanner_active": auto_scanner.is_running,
        "scanner_mode": auto_scanner.scan_mode,
        "config": {
            "is_demo": bingx.is_demo,
            "position_pct": config.POSITION_BALANCE_PCT,
            "risk_ratio": config.DEFAULT_RISK_RATIO,
            "default_sl_pct": config.DEFAULT_SL_PCT,
            "max_simultaneous_positions": config.MAX_SIMULTANEOUS_POSITIONS,
            "whatsapp_phone": config.WHATSAPP_PHONE,
            "telegram_enabled": config.TELEGRAM_ENABLED,
            "telegram_configured": bool(config.TELEGRAM_BOT_TOKEN and config.TELEGRAM_CHAT_ID),
            "telegram_bot": "@botmkbingx_bot"
        }
    }


@app.get("/api/watchlist")
def get_watchlist():
    return bingx.get_watchlist_summary()


@app.post("/api/toggle")
def toggle_bot_state():
    current_state = get_bot_state()
    new_state = not current_state["is_active"]
    set_bot_active(new_state)

    msg = f"Bot {'ENCENDIDO [ON]' if new_state else 'APAGADO [OFF]'} manualmente desde el panel web."
    add_system_log("INFO", msg)

    return {"is_active": new_state, "message": msg}


@app.post("/api/scanner/toggle")
async def toggle_auto_scanner():
    is_now_active = auto_scanner.toggle()
    return {
        "scanner_active": is_now_active,
        "scanner_mode": auto_scanner.scan_mode,
        "message": f"Auto-Scanner {'ACTIVADO' if is_now_active else 'DESACTIVADO'}"
    }


class ScannerModePayload(BaseModel):
    mode: str


@app.post("/api/scanner/mode")
def set_scanner_mode(payload: ScannerModePayload):
    new_mode = auto_scanner.set_mode(payload.mode)
    labels = {
        "top_volatility": "MÁXIMA VOLATILIDAD 24H (Top Movers)",
        "watchlist": "Solo Watchlist",
        "full_market": "Top Volumen BingX"
    }
    return {
        "scanner_mode": new_mode,
        "mode_label": labels.get(new_mode, new_mode)
    }


@app.post("/api/environment/switch")
def switch_environment(payload: SwitchEnvPayload):
    """Alterna en caliente entre cuenta Demo (VST) y Real (USDT)."""
    new_mode = bingx.switch_environment(payload.is_demo)
    return {
        "is_demo": new_mode,
        "mode": "DEMO (VST)" if new_mode else "REAL (USDT)",
        "balance": bingx.get_balance()
    }


@app.post("/api/close-position/{symbol:path}")
def close_position(symbol: str):
    try:
        res = bingx.close_position_market(symbol)
        return res
    except Exception as e:
        add_system_log("ERROR", f"Error al cerrar posición {symbol}: {e}")
        raise HTTPException(status_code=400, detail=str(e))


class AddTickerPayload(BaseModel):
    symbol: str


@app.post("/api/watchlist/add")
def add_watchlist_ticker(payload: AddTickerPayload):
    """Busca y agrega un nuevo activo a la Watchlist (cripto, índices, acciones)."""
    try:
        res = bingx.add_custom_ticker(payload.symbol)
        return res
    except Exception as e:
        raise HTTPException(status_code=400, detail=str(e))


@app.delete("/api/watchlist/{ticker:path}")
def remove_watchlist_ticker(ticker: str):
    """Elimina un ticker de la Watchlist."""
    success = bingx.remove_custom_ticker(ticker)
    if not success:
        raise HTTPException(status_code=404, detail="Ticker no encontrado.")
    return {"status": "deleted", "ticker": ticker}


@app.post("/api/telegram/test")
def test_telegram():
    """Envía un mensaje de prueba al chat de Telegram configurado."""
    token = config.TELEGRAM_BOT_TOKEN
    chat_id = config.TELEGRAM_CHAT_ID

    if not token or not chat_id:
        raise HTTPException(status_code=400, detail="Falta configurar TELEGRAM_BOT_TOKEN o TELEGRAM_CHAT_ID en .env")

    notifier.tg_token = token
    notifier.tg_chat_id = chat_id
    notifier.tg_enabled = True

    success = notifier.send_telegram("🔔 *Prueba de Notificación Exitosa desde BotMK BingX!*\nTu bot está vinculado y listo para enviar alertas en vivo.")
    if not success:
        raise HTTPException(status_code=500, detail="Fallo al enviar mensaje a Telegram. Verifica que hayas iniciado el bot.")
    return {"status": "sent", "message": "Mensaje de prueba enviado con éxito."}


@app.post("/api/telegram/toggle")
def toggle_telegram():
    """Silencia o activa instantáneamente las alertas de Telegram."""
    new_state = not config.TELEGRAM_ENABLED
    update_env_file({"TELEGRAM_ENABLED": new_state})
    config.TELEGRAM_ENABLED = new_state
    notifier.tg_enabled = new_state
    
    msg = f"Alertas de Telegram {'ACTIVADAS [🔔]' if new_state else 'SILENCIADAS [🔕]'}"
    add_system_log("INFO", msg)
    return {
        "telegram_enabled": new_state,
        "message": msg
    }


@app.get("/api/config")
def get_full_config():
    """Retorna la configuración completa del sistema para el panel de ajustes."""
    return {
        "bingx_api_key": config.BINGX_API_KEY,
        "bingx_secret_key": config.BINGX_SECRET_KEY,
        "is_demo": config.IS_DEMO,
        "position_sizing_mode": getattr(config, "POSITION_SIZING_MODE", "percent_total"),
        "position_balance_pct": config.POSITION_BALANCE_PCT,
        "fixed_position_margin_usdt": getattr(config, "FIXED_POSITION_MARGIN_USDT", 10.0),
        "break_even_enabled": getattr(config, "BREAK_EVEN_ENABLED", True),
        "break_even_trigger_pct": getattr(config, "BREAK_EVEN_TRIGGER_PCT", 1.0),
        "break_even_offset_pct": getattr(config, "BREAK_EVEN_OFFSET_PCT", 0.08),
        "stagnant_be_enabled": getattr(config, "STAGNANT_BE_ENABLED", True),
        "stagnant_hours_threshold": getattr(config, "STAGNANT_HOURS_THRESHOLD", 4.0),
        "macro_ema_period": getattr(config, "MACRO_EMA_PERIOD", 50),
        "min_24h_volume_usdt": getattr(config, "MIN_24H_VOLUME_USDT", 3000000.0),
        "adx_filter_enabled": getattr(config, "ADX_FILTER_ENABLED", True),
        "adx_min_threshold": getattr(config, "ADX_MIN_THRESHOLD", 22.0),
        "default_risk_ratio": config.DEFAULT_RISK_RATIO,
        "default_sl_pct": config.DEFAULT_SL_PCT,
        "max_simultaneous_positions": config.MAX_SIMULTANEOUS_POSITIONS,
        "max_consecutive_losses": config.MAX_CONSECUTIVE_LOSSES,
        "max_daily_drawdown_pct": config.MAX_DAILY_DRAWDOWN_PCT,
        "leverage_crypto_majors": config.LEVERAGE_CRYPTO_MAJORS,
        "leverage_crypto_alts": config.LEVERAGE_CRYPTO_ALTS,
        "leverage_indices": config.LEVERAGE_INDICES,
        "leverage_commodities": config.LEVERAGE_COMMODITIES,
        "leverage_stocks": config.LEVERAGE_STOCKS,
        "auto_leverage_by_sl": config.AUTO_LEVERAGE_BY_SL,
        "safety_margin_factor": config.SAFETY_MARGIN_FACTOR,
        "max_allowed_leverage": config.MAX_ALLOWED_LEVERAGE,
        "telegram_enabled": config.TELEGRAM_ENABLED,
        "telegram_bot_token": config.TELEGRAM_BOT_TOKEN,
        "telegram_chat_id": config.TELEGRAM_CHAT_ID,
        "whatsapp_enabled": config.WHATSAPP_ENABLED,
        "whatsapp_phone": config.WHATSAPP_PHONE,
        "whatsapp_apikey": config.WHATSAPP_APIKEY,
        "webhook_secret": config.WEBHOOK_SECRET,
    }


@app.post("/api/config")
def update_full_config(payload: ConfigPayload):
    """Actualiza los parámetros en .env y recarga en memoria inmediatamente."""
    mapping = {
        "BINGX_API_KEY": payload.bingx_api_key,
        "BINGX_SECRET_KEY": payload.bingx_secret_key,
        "IS_DEMO": payload.is_demo,
        "POSITION_SIZING_MODE": payload.position_sizing_mode,
        "POSITION_BALANCE_PCT": payload.position_balance_pct,
        "FIXED_POSITION_MARGIN_USDT": payload.fixed_position_margin_usdt,
        "BREAK_EVEN_ENABLED": payload.break_even_enabled,
        "BREAK_EVEN_TRIGGER_PCT": payload.break_even_trigger_pct,
        "BREAK_EVEN_OFFSET_PCT": payload.break_even_offset_pct,
        "STAGNANT_BE_ENABLED": payload.stagnant_be_enabled,
        "STAGNANT_HOURS_THRESHOLD": payload.stagnant_hours_threshold,
        "MACRO_EMA_PERIOD": payload.macro_ema_period,
        "MIN_24H_VOLUME_USDT": payload.min_24h_volume_usdt,
        "ADX_FILTER_ENABLED": payload.adx_filter_enabled,
        "ADX_MIN_THRESHOLD": payload.adx_min_threshold,
        "DEFAULT_RISK_RATIO": payload.default_risk_ratio,
        "DEFAULT_SL_PCT": payload.default_sl_pct,
        "MAX_SIMULTANEOUS_POSITIONS": payload.max_simultaneous_positions,
        "MAX_CONSECUTIVE_LOSSES": payload.max_consecutive_losses,
        "MAX_DAILY_DRAWDOWN_PCT": payload.max_daily_drawdown_pct,
        "AUTO_LEVERAGE_BY_SL": payload.auto_leverage_by_sl,
        "SAFETY_MARGIN_FACTOR": payload.safety_margin_factor,
        "MAX_ALLOWED_LEVERAGE": payload.max_allowed_leverage,
        "LEVERAGE_CRYPTO_MAJORS": payload.leverage_crypto_majors,
        "LEVERAGE_CRYPTO_ALTS": payload.leverage_crypto_alts,
        "LEVERAGE_INDICES": payload.leverage_indices,
        "LEVERAGE_COMMODITIES": payload.leverage_commodities,
        "LEVERAGE_STOCKS": payload.leverage_stocks,
        "TELEGRAM_ENABLED": payload.telegram_enabled,
        "TELEGRAM_BOT_TOKEN": payload.telegram_bot_token,
        "TELEGRAM_CHAT_ID": payload.telegram_chat_id,
        "WHATSAPP_ENABLED": payload.whatsapp_enabled,
        "WHATSAPP_PHONE": payload.whatsapp_phone,
        "WHATSAPP_APIKEY": payload.whatsapp_apikey,
        "WEBHOOK_SECRET": payload.webhook_secret,
    }
    updates = {k: v for k, v in mapping.items() if v is not None}
    if updates:
        update_env_file(updates)
        config.reload_from_env()
        bingx.update_api_credentials(
            api_key=config.BINGX_API_KEY,
            secret_key=config.BINGX_SECRET_KEY,
            is_demo=config.IS_DEMO
        )
        notifier.reload_credentials()
        add_system_log("INFO", "⚙️ Configuración del bot actualizada y recargada en caliente con éxito.")

    return {
        "status": "success",
        "message": "Configuración guardada y aplicada en caliente.",
        "config": get_full_config()
    }


@app.post("/api/manual-order")
def execute_manual_order(payload: ManualOrderPayload):
    state = get_bot_state()
    if not state["is_active"]:
        raise HTTPException(status_code=400, detail="El bot está apagado o en pausa.")

    try:
        res = bingx.open_bracket_position(
            raw_symbol=payload.symbol,
            side=payload.side,
            custom_leverage=payload.leverage
        )
        return res
    except Exception as e:
        add_system_log("ERROR", f"Fallo al ejecutar orden manual {payload.symbol}: {e}")
        raise HTTPException(status_code=400, detail=str(e))


@app.post("/webhook/signal")
async def receive_tradingview_signal(payload: SignalPayload):
    if config.WEBHOOK_SECRET and payload.secret and payload.secret != config.WEBHOOK_SECRET:
        add_system_log("WARNING", f"Webhook rechazado: Token inválido para {payload.symbol}")
        raise HTTPException(status_code=401, detail="Token secreto inválido.")

    state = get_bot_state()
    if not state["is_active"]:
        reason = state.get("circuit_breaker_reason") or "Bot en estado OFF/PAUSA"
        msg = f"Señal {payload.signal} para {payload.symbol} IGNORADA: {reason}"
        add_system_log("WARNING", msg)
        return {"status": "ignored", "reason": reason}

    add_system_log("INFO", f"Señal Webhook Recibida: {payload.signal} en {payload.symbol}")
    try:
        result = bingx.open_bracket_position(
            raw_symbol=payload.symbol,
            side=payload.signal,
            custom_leverage=payload.leverage,
            custom_sl_pct=payload.sl_pct,
            risk_ratio=config.DEFAULT_RISK_RATIO
        )
        return {
            "status": "executed",
            "details": result
        }
    except Exception as exc:
        add_system_log("ERROR", f"Error ejecutando señal {payload.signal} {payload.symbol}: {exc}")
        raise HTTPException(status_code=400, detail=str(exc))


if __name__ == "__main__":
    uvicorn.run(
        "main:app",
        host=config.HOST,
        port=config.PORT,
        reload=False
    )
