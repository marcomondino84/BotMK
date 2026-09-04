import sys
sys.stdout.reconfigure(encoding='utf-8')
import ccxt
from config import config

print("API Key:", config.BINGX_API_KEY[:10] + "...")

# 1. Probar en modo sandbox (is_demo=True)
print("\n--- TEST 1: Con sandbox mode = True ---")
ex_sandbox = ccxt.bingx({
    "apiKey": config.BINGX_API_KEY,
    "secret": config.BINGX_SECRET_KEY,
    "enableRateLimit": True,
    "options": {"defaultType": "swap"}
})
ex_sandbox.set_sandbox_mode(True)

try:
    bal = ex_sandbox.fetch_balance()
    print("Balance Sandbox VST:", bal.get("total", {}).get("VST"))
except Exception as e:
    print("Error balance sandbox:", e)

try:
    order = ex_sandbox.create_order(
        symbol="BTC/USDT:USDT",
        type="market",
        side="buy",
        amount=0.001
    )
    print("Orden Sandbox éxito:", order)
except Exception as e:
    print("Error orden sandbox:", e)


# 2. Probar sin sandbox mode (Live API / endpoints de producción)
print("\n--- TEST 2: Con sandbox mode = False (Live) ---")
ex_live = ccxt.bingx({
    "apiKey": config.BINGX_API_KEY,
    "secret": config.BINGX_SECRET_KEY,
    "enableRateLimit": True,
    "options": {"defaultType": "swap"}
})
ex_live.set_sandbox_mode(False)

try:
    bal_live = ex_live.fetch_balance()
    print("Balance Live:", {k: v for k, v in bal_live.get("total", {}).items() if v > 0})
except Exception as e:
    print("Error balance live:", e)

# NO abrimos orden real todavía sin confirmar
