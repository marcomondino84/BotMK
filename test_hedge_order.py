import sys
sys.stdout.reconfigure(encoding='utf-8')
import ccxt
from config import config

ex = ccxt.bingx({
    "apiKey": config.BINGX_API_KEY,
    "secret": config.BINGX_SECRET_KEY,
    "enableRateLimit": True,
    "options": {"defaultType": "swap"}
})
ex.set_sandbox_mode(True)

print("Probando orden en Hedge Mode con positionSide='LONG'...")
try:
    order = ex.create_order(
        symbol="BTC/USDT:USDT",
        type="market",
        side="buy",
        amount=0.001,
        params={"positionSide": "LONG"}
    )
    print("¡¡¡ORDEN EXITOSA EN SANDBOX/VST!!!")
    print(order)
except Exception as e:
    print("Error:", e)
