"""
Script de diagnóstico y prueba de conexión a la API de BingX.
Verifica conectividad y consulta el balance de la cuenta sin ejecutar órdenes.
"""

import os
import sys
import time
import hmac
import hashlib
from typing import Dict, Any, Optional

import os
import sys
import time
import hmac
import hashlib
from typing import Dict, Any, Optional

# Forzar UTF-8 en consola de Windows para evitar errores con charmap/cp1252
if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
if hasattr(sys.stderr, "reconfigure"):
    sys.stderr.reconfigure(encoding="utf-8", errors="replace")

# Intentar cargar variables desde .env
try:
    from dotenv import load_dotenv
    load_dotenv()
except ImportError:
    # Fallback si python-dotenv aún no está instalado: leer .env directamente
    if os.path.exists(".env"):
        with open(".env", "r", encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if line and not line.startswith("#") and "=" in line:
                    k, v = line.split("=", 1)
                    os.environ.setdefault(k.strip(), v.strip())


API_KEY = os.getenv("BINGX_API_KEY", "").strip()
SECRET_KEY = os.getenv("BINGX_SECRET_KEY", "").strip()
ENVIRONMENT = os.getenv("ENVIRONMENT", "demo").strip().lower()
DEFAULT_MARKET = os.getenv("DEFAULT_MARKET", "swap").strip().lower()


def test_via_ccxt() -> bool:
    """Intenta conectar y obtener saldos utilizando la biblioteca CCXT."""
    try:
        import ccxt
    except ImportError:
        print("[INFO] ccxt no está instalado aún en este entorno. Pasando a verificación nativa vía REST...")
        return False

    print("\n--- Conectando a BingX mediante CCXT ---")
    try:
        exchange_params = {
            "apiKey": API_KEY,
            "secret": SECRET_KEY,
            "enableRateLimit": True,
            "options": {
                "defaultType": DEFAULT_MARKET,  # 'swap' (Perpetuos) o 'spot'
            }
        }
        exchange = ccxt.bingx(exchange_params)

        if ENVIRONMENT == "demo":
            try:
                exchange.set_sandbox_mode(True)
                print("[*] Modo Sandbox/Testnet activado en CCXT.")
            except Exception as e:
                print(f"[!] Advertencia activando sandbox: {e}")

        print("[*] Cargando mercados disponibles...")
        markets = exchange.load_markets()
        print(f"[OK] Conexión establecida. Mercados detectados: {len(markets)}")

        print(f"[*] Consultando balance de la cuenta ({DEFAULT_MARKET.upper()})...")
        balance = exchange.fetch_balance()

        print("\n================ SALDO ACTUAL ================")
        # Filtrar solo monedas con saldo > 0 o USDT
        found_balance = False
        total_balances = balance.get("total", {})
        free_balances = balance.get("free", {})
        used_balances = balance.get("used", {})

        for currency, total in total_balances.items():
            if total is not None and (total > 0 or currency == "USDT"):
                free = free_balances.get(currency, 0.0)
                used = used_balances.get(currency, 0.0)
                print(f"  Moneda: {currency:<8} | Total: {total:<12.4f} | Libre: {free:<12.4f} | En Uso: {used:<12.4f}")
                found_balance = True

        if not found_balance:
            print("  (No se encontraron saldos mayores a 0 en esta cuenta/mercado)")

        print("==============================================")
        return True

    except ccxt.AuthenticationError as auth_err:
        print(f"\n[X] Error de Autenticación en BingX: {auth_err}")
        print("    -> Por favor verifica que BINGX_API_KEY y BINGX_SECRET_KEY en tu .env sean correctas.")
        return True
    except Exception as err:
        print(f"\n[X] Error al consultar mediante CCXT: {err}")
        return False


def test_via_requests():
    """Fallback directo mediante peticiones REST firmadas (HMAC-SHA256) con requests o urllib."""
    print("\n--- Conectando a BingX mediante API REST Oficial Directa ---")
    import urllib.request
    import urllib.parse
    import json

    # Host según entorno
    # BingX Open API estándar
    base_url = "https://open-api.bingx.com"
    # Endpoint para balance de Futuros Perpetuos V2 (Swap)
    endpoint = "/openApi/swap/v2/user/balance" if DEFAULT_MARKET == "swap" else "/openApi/spot/v1/account/balance"

    timestamp = int(time.time() * 1000)
    params = {
        "timestamp": timestamp,
        "recvWindow": 5000
    }

    query_string = urllib.parse.urlencode(sorted(params.items()))
    signature = hmac.new(
        SECRET_KEY.encode("utf-8"),
        query_string.encode("utf-8"),
        hashlib.sha256
    ).hexdigest()

    full_url = f"{base_url}{endpoint}?{query_string}&signature={signature}"
    headers = {
        "X-BX-APIKEY": API_KEY,
        "User-Agent": "BotMK-Trader/1.0"
    }

    req = urllib.request.Request(full_url, headers=headers, method="GET")

    try:
        with urllib.request.urlopen(req, timeout=10) as response:
            res_data = json.loads(response.read().decode("utf-8"))
            code = res_data.get("code")
            msg = res_data.get("msg", "")

            if code == 0:
                data = res_data.get("data", {})
                print(f"[OK] Conexión exitosa a la API de BingX ({DEFAULT_MARKET.upper()}).")
                print("\n================ SALDO ACTUAL ================")
                if DEFAULT_MARKET == "swap":
                    # data puede ser una lista de balances o un objeto con 'balance'
                    balances = data if isinstance(data, list) else [data.get("balance", data)]
                    for b in balances:
                        asset = b.get("asset", "USDT")
                        equity = float(b.get("equity", b.get("balance", 0.0)))
                        available = float(b.get("availableMargin", b.get("free", 0.0)))
                        unrealized_pnl = float(b.get("unrealizedProfit", 0.0))
                        print(f"  Activo: {asset:<8} | Equidad: {equity:<12.4f} | Disponible: {available:<12.4f} | PnL No Realizado: {unrealized_pnl:<12.4f}")
                else:
                    balances = data.get("balances", [])
                    for b in balances:
                        asset = b.get("asset", "")
                        free = float(b.get("free", 0.0))
                        locked = float(b.get("locked", 0.0))
                        if free > 0 or locked > 0 or asset == "USDT":
                            print(f"  Activo: {asset:<8} | Libre: {free:<12.4f} | Bloqueado: {locked:<12.4f}")
                print("==============================================")
            else:
                print(f"[X] BingX respondió con código de error {code}: {msg}")
    except urllib.error.HTTPError as http_err:
        error_body = http_err.read().decode("utf-8")
        print(f"[X] Error HTTP {http_err.code}: {error_body}")
    except Exception as ex:
        print(f"[X] Error en petición directa a BingX: {ex}")


def main():
    print("==================================================")
    print("      BingX Bot - Verificador de Conexión         ")
    print("==================================================")
    print(f"Entorno configurado: {ENVIRONMENT.upper()}")
    print(f"Mercado objetivo:    {DEFAULT_MARKET.upper()}")
    
    if not API_KEY or not SECRET_KEY or API_KEY == "tu_api_key_aqui":
        print("\n[!] AVISO: No has configurado tus credenciales en el archivo .env.")
        print("    Abre el archivo .env y asigna tus claves:")
        print("    BINGX_API_KEY=tu_api_key_real")
        print("    BINGX_SECRET_KEY=tu_secret_key_real")
        print("\nEl script se detiene para que puedas configurar tus credenciales.")
        sys.exit(0)

    print(f"API Key configurada: {API_KEY[:4]}...{API_KEY[-4:] if len(API_KEY) > 8 else ''}")
    print("[*] Probando conexión sin emitir órdenes...")

    # Intentar primero con CCXT, si no está instalado o falla, usar fallback directo
    ccxt_success = test_via_ccxt()
    if not ccxt_success:
        test_via_requests()


if __name__ == "__main__":
    main()
