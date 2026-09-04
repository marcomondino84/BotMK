import sqlite3

conn = sqlite3.connect('data/trading_bot.db')
cursor = conn.cursor()
cols = [row[1] for row in cursor.execute('PRAGMA table_info(bot_state)').fetchall()]
if 'scanner_active' not in cols:
    cursor.execute('ALTER TABLE bot_state ADD COLUMN scanner_active BOOLEAN DEFAULT 0')
    print('Columna scanner_active añadida.')
if 'scanner_mode' not in cols:
    cursor.execute("ALTER TABLE bot_state ADD COLUMN scanner_mode VARCHAR(32) DEFAULT 'watchlist'")
    print('Columna scanner_mode añadida.')
conn.commit()
conn.close()
print('Migración completada con éxito.')
