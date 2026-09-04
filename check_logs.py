import sys
sys.stdout.reconfigure(encoding='utf-8')
from database import get_recent_logs

print("--- ÚLTIMOS LOGS DEL SISTEMA ---")
for l in get_recent_logs(limit=30):
    print(f"[{l['level']}] {l['time']}: {l['message']}")
