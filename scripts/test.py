from pathlib import Path
import csv
import sqlite3

files = [
    "configs/manual_research/google_trends_pilot_us.csv",
    "configs/manual_research/tiktok_pilot_us.csv",
]

for name in files:
    p = Path(name)
    print(f"\n=== {name} ===")
    print("exists:", p.exists())
    if not p.exists():
        continue

    print("absolute_path:", p.resolve())
    print("file_size:", p.stat().st_size)

    with p.open("r", encoding="utf-8-sig", newline="") as f:
        reader = csv.DictReader(f)
        rows = [
            row for row in reader
            if any(str(v or "").strip() for v in row.values())
        ]
        print("columns:", reader.fieldnames)
        print("actual_data_rows:", len(rows))
        for row in rows:
            print(row)

db_path = Path("data/registry.sqlite").resolve()
print("\n=== DATABASE ===")
print("absolute_path:", db_path)

con = sqlite3.connect(db_path)
print("total_observations:",
      con.execute("SELECT COUNT(*) FROM source_observations").fetchone()[0])

print("source_counts:",
      con.execute("""
          SELECT source_name, COUNT(*)
          FROM source_observations
          GROUP BY source_name
      """).fetchall())

con.close()

