"""Fetch trades via Railway SSH and save to local file."""

import subprocess
import json
import os

# Run Python script on Railway via SSH to export trades
print("Fetching trades from Railway database...")

cmd = [
    "railway", "run",
    "python3", "-c",
    """
import sqlite3
import json
import os

DB_PATH = os.getenv('DB_PATH', '/data/trades.db')
conn = sqlite3.connect(DB_PATH)
cursor = conn.cursor()

cursor.execute('''
    SELECT city, side, edge, forecast_prob, price, size, hit, pnl
    FROM trades
    WHERE resolved = 1 AND hit IS NOT NULL
    ORDER BY created_at ASC
''')

trades = cursor.fetchall()
conn.close()

# Output as JSON
print(json.dumps(trades))
"""
]

result = subprocess.run(cmd, capture_output=True, text=True, timeout=60)

if result.returncode != 0:
    print("Error fetching trades:", result.stderr)
    exit(1)

# Parse JSON output
trades_data = json.loads(result.stdout.strip())

print(f"Fetched {len(trades_data)} trades from Railway")

# Save to local file
output_file = "trades_data.json"
with open(output_file, 'w') as f:
    json.dump(trades_data, f, indent=2)

print(f"Saved to {output_file}")
