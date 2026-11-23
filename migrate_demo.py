import sqlite3

conn = sqlite3.connect('data/trading_bot.db')
cursor = conn.cursor()

# 1. Agregar columna is_demo si no existe
cols = [r[1] for r in cursor.execute('PRAGMA table_info(trades)').fetchall()]
if 'is_demo' not in cols:
    cursor.execute('ALTER TABLE trades ADD COLUMN is_demo BOOLEAN DEFAULT 0')
    print('Columna is_demo agregada a tabla trades.')

# 2. Marcar trades de prueba demo (ID 32 de Oro con 134k)
cursor.execute('UPDATE trades SET is_demo = 1 WHERE id = 32 OR (amount * entry_price) > 500')
print('Trades marcados como demo:', cursor.rowcount)

# 3. Marcar los demás como 0
cursor.execute('UPDATE trades SET is_demo = 0 WHERE is_demo IS NULL')
conn.commit()

# 4. Comprobar estadísticas sólo de trades reales
cursor.execute("SELECT COUNT(*), SUM(pnl_usd), SUM(amount * entry_price) FROM trades WHERE is_demo = 0 AND status = 'CLOSED'")
row = cursor.fetchone()
print(f'TRADES REALES CERRADOS: Total={row[0]}, PnL=${row[1]:.2f}, Volumen Real=${row[2]:.2f}')

conn.close()
