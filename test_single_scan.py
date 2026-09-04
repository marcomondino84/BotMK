import sys
sys.stdout.reconfigure(encoding='utf-8')
import asyncio
from bingx_client import BingXFuturesClient
from auto_scanner import AutoScanner

async def run_single_scan():
    client = BingXFuturesClient()
    scanner = AutoScanner(client)
    scanner.scan_mode = "full_market"
    scanner.is_running = True
    print("Iniciando una ronda de escaneo manual...")
    
    open_positions = client.get_open_positions()
    active_symbols = [p["symbol"] for p in open_positions]
    print("Posiciones abiertas actuales:", active_symbols)
    
    market_pairs = client.get_market_symbols_by_volume(15)
    print(f"Obtenidos {len(market_pairs)} pares. Analizando...")
    
    for mp in market_pairs:
        ex_symbol = mp["symbol"]
        raw_sym = mp["ticker"]
        print(f"-> Analizando {raw_sym} ({ex_symbol})...")
        
        signal = await scanner._analyze_mtf(ex_symbol)
        print(f"   Señal: {signal}")
        if signal:
            print(f"   ¡¡EJECUTANDO {signal} en {raw_sym}!!")
            try:
                res = client.open_bracket_position(raw_symbol=raw_sym, side=signal)
                print("   Resultado orden:", res)
            except Exception as e:
                print("   Error al ejecutar orden:", e)
            break

asyncio.run(run_single_scan())
