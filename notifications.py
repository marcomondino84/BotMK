"""
Módulo de Notificaciones Multicanal (WhatsApp y Telegram).
Envía alertas en tiempo real sobre operaciones ejecutadas, cierres con PnL y Circuit Breaker.
"""

import urllib.parse
import requests
import asyncio
from typing import Optional
from config import config
from database import add_system_log


class NotificationDispatcher:
    """Despachador de notificaciones multicanal para Telegram y WhatsApp."""

    def __init__(self):
        # Configuración WhatsApp
        self.wa_phone = config.WHATSAPP_PHONE or "5493462698956"
        self.wa_apikey = config.WHATSAPP_APIKEY
        self.wa_enabled = config.WHATSAPP_ENABLED

        # Configuración Telegram (Recomendado - 100% estable)
        self.tg_token = config.TELEGRAM_BOT_TOKEN
        self.tg_chat_id = config.TELEGRAM_CHAT_ID
        self.tg_enabled = config.TELEGRAM_ENABLED

    def _format_phone(self, phone_str: str) -> str:
        """Asegura formato internacional para Argentina (+549...)."""
        clean = phone_str.strip().replace(" ", "").replace("-", "").replace("(", "").replace(")", "")
        if clean.startswith("+"):
            return clean
        if clean.startswith("549"):
            return f"+{clean}"
        if clean.startswith("54"):
            return f"+549{clean[2:]}"
        return f"+549{clean}"

    def send_telegram(self, message: str) -> bool:
        """Envía alerta instantánea por Telegram."""
        if not self.tg_enabled or not self.tg_token or not self.tg_chat_id:
            return False

        try:
            url = f"https://api.telegram.org/bot{self.tg_token}/sendMessage"
            payload = {
                "chat_id": self.tg_chat_id,
                "text": message,
                "parse_mode": "Markdown"
            }
            res = requests.post(url, json=payload, timeout=8)
            if res.status_code == 200:
                add_system_log("INFO", "[TELEGRAM]: Mensaje enviado con éxito.")
                return True
            else:
                add_system_log("WARNING", f"[TELEGRAM Error]: {res.text}")
                return False
        except Exception as e:
            add_system_log("WARNING", f"Error conexión Telegram: {e}")
            return False

    def send_whatsapp(self, message: str) -> bool:
        """Envía alerta por WhatsApp vía CallMeBot."""
        formatted_phone = self._format_phone(self.wa_phone)
        if not self.wa_enabled or not self.wa_apikey:
            return False

        try:
            clean_text = message.replace("*", "")  # formato simple para URL
            encoded_text = urllib.parse.quote(clean_text)
            url = f"https://api.callmebot.com/whatsapp.php?phone={formatted_phone}&text={encoded_text}&apikey={self.wa_apikey}"
            resp = requests.get(url, timeout=8)
            if resp.status_code == 200:
                add_system_log("INFO", f"[WHATSAPP -> {formatted_phone}]: Mensaje enviado.")
                return True
            else:
                add_system_log("WARNING", f"[WHATSAPP Aviso]: {resp.text}")
                return False
        except Exception as e:
            add_system_log("WARNING", f"Error conexión WhatsApp: {e}")
            return False

    def reload_credentials(self):
        """Recarga la configuración de notificaciones desde el objeto config."""
        self.wa_phone = config.WHATSAPP_PHONE or "5493462698956"
        self.wa_apikey = config.WHATSAPP_APIKEY
        self.wa_enabled = config.WHATSAPP_ENABLED
        self.tg_token = config.TELEGRAM_BOT_TOKEN
        self.tg_chat_id = config.TELEGRAM_CHAT_ID
        self.tg_enabled = config.TELEGRAM_ENABLED

    def broadcast_alert(self, message: str):
        """Despacha la alerta a todos los canales configurados y al log del sistema."""
        # Registrar en logs visibles de la consola web
        add_system_log("INFO", f"🔔 ALERTA: {message.split(chr(10))[0]}")

        # Enviar Telegram si está habilitado y configurado
        if self.tg_enabled and self.tg_token and self.tg_chat_id:
            self.send_telegram(message)

        # Enviar WhatsApp si está configurado
        if self.wa_enabled and self.wa_apikey:
            self.send_whatsapp(message)

    def notify_trade_opened(
        self,
        symbol: str,
        side: str,
        amount: float,
        price: float,
        leverage: int,
        sl: float,
        tp: float,
        is_demo: bool = True
    ):
        """Notifica apertura de posición."""
        account_type = "DEMO (VST)" if is_demo else "REAL (USDT)"
        text = (
            f"🚀 *NUEVA POSICIÓN ABIERTA* [{account_type}]\n"
            f"━━━━━━━━━━━━━━━━━━\n"
            f"🔹 *Activo:* `{symbol}`\n"
            f"🔹 *Operación:* *{side.upper()}* ({leverage}x)\n"
            f"🔹 *Cantidad:* `{amount}`\n"
            f"🔹 *Precio Entrada:* `${price:,.2f}`\n"
            f"🛑 *Stop Loss:* `${sl:,.2f}`\n"
            f"🎯 *Take Profit:* `${tp:,.2f}`\n"
            f"⚖️ *Margen:* 10% del Balance Libre\n"
            f"━━━━━━━━━━━━━━━━━━"
        )
        self.broadcast_alert(text)

    def notify_trade_closed(
        self,
        symbol: str,
        side: str,
        exit_price: float,
        pnl_usd: float,
        is_demo: bool = True
    ):
        """Notifica cierre de posición con PnL."""
        account_type = "DEMO (VST)" if is_demo else "REAL (USDT)"
        icon = "💰" if pnl_usd >= 0 else "🛑"
        sign = "+" if pnl_usd >= 0 else ""
        text = (
            f"{icon} *POSICIÓN CERRADA* [{account_type}]\n"
            f"━━━━━━━━━━━━━━━━━━\n"
            f"🔹 *Activo:* `{symbol}` ({side})\n"
            f"🔹 *Precio Salida:* `${exit_price:,.2f}`\n"
            f"💵 *PnL Neto:* *{sign}${pnl_usd:,.2f}*\n"
            f"━━━━━━━━━━━━━━━━━━"
        )
        self.broadcast_alert(text)

    def notify_circuit_breaker(self, reason: str):
        """Notifica activación de Circuit Breaker."""
        text = (
            f"⚠️ *CIRCUIT BREAKER ACTIVADO* ⚠️\n"
            f"━━━━━━━━━━━━━━━━━━\n"
            f"El bot se ha *PAUSADO AUTOMÁTICAMENTE* para proteger el capital.\n"
            f"Motivo: {reason}\n"
            f"Accede al panel para revisar y restablecer.\n"
            f"━━━━━━━━━━━━━━━━━━"
        )
        self.broadcast_alert(text)


notifier = NotificationDispatcher()
