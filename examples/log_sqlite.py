"""Log every reading to an SQLite database, one row per transmission.

    python examples/log_sqlite.py readings.db

Sensors are told apart by channel (``reading.channel``), not by ID, because the ID
changes every time the battery is replaced.
"""
import sqlite3
import sys

import ad5664

db = sqlite3.connect(sys.argv[1] if len(sys.argv) > 1 else 'ad5664.db')
db.execute('''CREATE TABLE IF NOT EXISTS readings (
    ts REAL, channel INTEGER, sensor_id TEXT, temperature REAL, humidity INTEGER,
    wbgt REAL, vh REAL, raw TEXT)''')

for rx in ad5664.listen():
    r = rx.reading
    db.execute('INSERT INTO readings VALUES (?,?,?,?,?,?,?,?)',
               (rx.time, r.channel, '%04X' % r.sensor_id, r.temperature, r.humidity,
                r.wbgt, r.vh, r.raw.hex()))
    db.commit()
    print('CH%d %.1f C %d %%' % (r.channel, r.temperature, r.humidity), flush=True)
