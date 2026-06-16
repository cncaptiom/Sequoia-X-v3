import sqlite3
import pandas as pd
from pathlib import Path

db_path = Path("data/sequoia_v2.db")
conn = sqlite3.connect(db_path)

print("📋 数据库所有表：")
tables = pd.read_sql_query("SELECT name FROM sqlite_master WHERE type='table';", conn)
print(tables)

print("\n🔍 尝试查看最近数据...")
for table in tables['name']:
    try:
        df = pd.read_sql_query(f"SELECT * FROM {table} LIMIT 5", conn)
        print(f"\n表 {table} 前5行：")
        print(df.head())
    except:
        pass

conn.close()