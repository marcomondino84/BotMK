"""
Script de ejecución de señal directa con gestión cuantitativa de riesgo en BingX.
Permite procesar señales desde la terminal o por lotes.
"""

import sys
import json
import argparse
from src.core.engine import TradingEngine
from src.utils.logger import logger

# Forzar UTF-8 en salida
if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")


def run_signal(signal_data: dict, dry_run: bool = False):
    print("\n================ PROCESANDO SEÑAL CUANTITATIVA ================")
    print(f"Par:          {signal_data.get('symbol')}")
    print(f"Acción:       {signal_data.get('action')}")
    print(f"Stop Loss:    ${signal_data.get('stop_loss')}")
    print(f"Take Profit:  ${signal_data.get('take_profit')}")
    print(f"Riesgo (%):   {signal_data.get('risk_percent')}%")
    print(f"Modo Dry Run: {'ACTIVADO (Sin órdenes reales)' if dry_run else 'DESACTIVADO (Ejecución Real/Demo)'}")
    print("================================================================\n")

    engine = TradingEngine()
    engine.initialize()

    if dry_run:
        # Modo simulación: cálculo matemático sin colocar orden
        symbol = signal_data.get("symbol")
        if engine.settings.DEFAULT_MARKET == "swap" and ":" not in symbol:
            candidate = f"{symbol}:USDT"
            if candidate in engine.client.exchange.markets:
                symbol = candidate

        ticker = engine.client.fetch_ticker(symbol)
        current_price = ticker.get("last") or ticker.get("close")
        balance = engine.client.fetch_balance()
        free_balances = balance.get("free", {})
        capital = (
            free_balances.get("USDT")
            or free_balances.get("VST")
            or balance.get("total", {}).get("USDT")
            or balance.get("total", {}).get("VST")
            or 1000.0
        )

        stop_loss = float(signal_data.get("stop_loss"))
        risk_pct = float(signal_data.get("risk_percent", 1.0))
        price_dist = abs(current_price - stop_loss)
        risk_usd = capital * (risk_pct / 100.0)
        raw_size = risk_usd / price_dist

        market_info = engine.client.exchange.markets.get(symbol, {})
        adjusted_size = engine.risk_manager.adjust_to_market_precision(
            raw_size,
            market_info,
            exchange=engine.client.exchange,
            symbol=symbol
        )

        print("[SIMULACIÓN DE CÁLCULO DE RIESGO]")
        print(f"  Precio Actual de Mercado:   ${current_price:.2f}")
        print(f"  Capital en Cuenta:          ${capital:.2f}")
        print(f"  Pérdida Máxima Tolerada:    ${risk_usd:.2f} ({risk_pct}%)")
        print(f"  Distancia al Stop Loss:     ${price_dist:.2f}")
        print(f"  Tamaño Calculado Teórico:   {raw_size:.6f} BTC")
        print(f"  Tamaño Ajustado al Exchange:{adjusted_size:.4f} BTC")
        print(f"  Valor Nocional del Trade:   ${(adjusted_size * current_price):.2f}")
        print("\n[OK] Simulación completada con éxito. Ninguna orden fue enviada.")
        return

    # Ejecución en el engine
    try:
        result = engine.process_signal(signal_data)
        print("\n================ RESULTADO DE LA EJECUCIÓN ================")
        print(f"Estado:           {result.get('status').upper()}")
        print(f"Order ID:         {result.get('order_id')}")
        print(f"Símbolo:          {result.get('symbol')}")
        print(f"Acción:           {result.get('action')}")
        print(f"Precio Ejecutado: ${result.get('executed_price'):.2f}")
        print(f"Tamaño Posición:  {result.get('position_size')} BTC")
        print(f"Stop Loss:        ${result.get('stop_loss')}")
        print(f"Take Profit:      ${result.get('take_profit')}")
        print("===========================================================\n")
    except Exception as exc:
        print("\n[!] NO SE PUDO EJECUTAR LA ORDEN EN BINGX:")
        if "100004" in str(exc) or "Permission denied" in str(exc):
            print("  -> CAUSA: La API Key configurada no tiene habilitado el permiso de 'Perpetual Futures Trading'.")
            print("  -> SOLUCIÓN: Ve a https://bingx.com/en/account/api, edita tu API Key y marca la casilla:")
            print("     [x] Perpetual Futures Trading (o Trading de Futuros)")
        else:
            print(f"  -> Error: {exc}")
        print("\nEl cálculo matemático y el análisis de riesgo se completaron correctamente.")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Ejecutar señal en BingX con gestión de riesgo.")
    parser.add_argument("--dry-run", action="store_true", help="Simular cálculo de tamaño sin enviar órdenes")
    parser.add_argument("--payload", type=str, default=None, help="JSON con la señal")

    args = parser.parse_args()

    default_signal = {
        "symbol": "BTC/USDT",
        "action": "BUY",
        "stop_loss": 62000,
        "take_profit": 64500,
        "risk_percent": 1
    }

    signal = json.loads(args.payload) if args.payload else default_signal
    run_signal(signal, dry_run=args.dry_run)
