from __future__ import annotations

from pathlib import Path

from .db import connect

TABLES = ["products", "social_signals", "metric_snapshots", "search_snapshots", "audience_insights", "scores"]


def refresh_warehouse(db_path: str | Path, output_dir: str | Path) -> dict[str, str]:
    """Export selected SQLite tables to Parquet and materialize them in DuckDB."""
    try:
        import duckdb
        import polars as pl
    except ImportError as exc:
        raise RuntimeError(
            "Warehouse export requires optional dependencies. Install with: pip install -e '.[warehouse]'"
        ) from exc
    out = Path(output_dir)
    out.mkdir(parents=True, exist_ok=True)
    parquet_paths: dict[str, Path] = {}
    with connect(db_path) as conn:
        for table in TABLES:
            rows = [dict(row) for row in conn.execute(f"SELECT * FROM {table}").fetchall()]
            frame = pl.DataFrame(rows) if rows else pl.DataFrame()
            path = out / f"{table}.parquet"
            frame.write_parquet(path)
            parquet_paths[table] = path

    warehouse_path = out / "fashion_radar.duckdb"
    duck = duckdb.connect(str(warehouse_path))
    try:
        for table, path in parquet_paths.items():
            escaped = str(path.resolve()).replace("'", "''")
            duck.execute(f"CREATE OR REPLACE TABLE {table} AS SELECT * FROM read_parquet('{escaped}')")
    finally:
        duck.close()
    result = {table: str(path) for table, path in parquet_paths.items()}
    result["duckdb"] = str(warehouse_path)
    return result
