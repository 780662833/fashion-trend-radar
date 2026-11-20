from __future__ import annotations

import argparse
import json
import sqlite3
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[1]


def resolve_db_path(value: str) -> Path:
    path = Path(value).expanduser()
    return path if path.is_absolute() else PROJECT_ROOT / path


def inspect_database(db_path: Path) -> dict:
    if not db_path.is_file():
        return {
            "status": "database_not_found",
            "database": str(db_path),
        }

    uri = db_path.resolve().as_uri() + "?mode=ro"
    with sqlite3.connect(uri, uri=True) as conn:
        tables = {
            row[0]
            for row in conn.execute(
                "SELECT name FROM sqlite_master WHERE type='table'"
            )
        }
        required = {"products", "social_signals", "search_snapshots"}
        missing = sorted(required - tables)
        if missing:
            return {
                "status": "unsupported_database",
                "database": str(db_path),
                "missing_required_tables": missing,
            }
        return {
            "status": "already_migrated" if "source_run_observations" in tables else "ready",
            "database": str(db_path.resolve()),
            "source_run_observations_exists": "source_run_observations" in tables,
            "historical_observation_rows": (
                int(conn.execute("SELECT COUNT(*) FROM source_observations").fetchone()[0])
                if "source_observations" in tables else 0
            ),
            "historical_run_rows": (
                int(conn.execute("SELECT COUNT(*) FROM source_runs").fetchone()[0])
                if "source_runs" in tables else 0
            ),
        }


def main() -> int:
    parser = argparse.ArgumentParser(
        description=(
            "Add the source_run_observations lineage table. Preview is read-only; "
            "use --apply to perform the additive migration."
        )
    )
    parser.add_argument("--db", default="data/registry.sqlite")
    parser.add_argument(
        "--apply",
        action="store_true",
        help="Apply the additive schema migration. No historical run links are guessed.",
    )
    args = parser.parse_args()

    db_path = resolve_db_path(args.db)
    result = inspect_database(db_path)
    if result["status"] in {"database_not_found", "unsupported_database"}:
        print(json.dumps(result, ensure_ascii=False, indent=2))
        return 2

    if not args.apply:
        result["status"] = (
            "already_migrated" if result["source_run_observations_exists"]
            else "preview"
        )
        result["next_step"] = (
            "No action required."
            if result["source_run_observations_exists"]
            else "Review this report, then rerun with --apply."
        )
        print(json.dumps(result, ensure_ascii=False, indent=2))
        return 0

    if not result["source_run_observations_exists"]:
        # The existence check above prevents connect() from creating a new,
        # empty database when a path is wrong.
        from fashion_radar.db import connect
        from fashion_radar.source_store import ensure_source_tables

        with connect(db_path) as conn:
            ensure_source_tables(conn)
            violations = conn.execute("PRAGMA foreign_key_check").fetchall()
            if violations:
                raise RuntimeError(
                    f"Foreign-key check found {len(violations)} violation(s)."
                )

    final = inspect_database(db_path)
    final["status"] = (
        "migrated" if final.get("source_run_observations_exists")
        else "migration_failed"
    )
    final["historical_backfill"] = "not_performed"
    final["historical_lineage_note"] = (
        "Existing source_observations were not assigned to historical runs. "
        "Time-window similarity is not sufficient proof of run ownership. "
        "Only observations saved or refreshed by future collector runs receive "
        "direct source_run_observations links."
    )
    print(json.dumps(final, ensure_ascii=False, indent=2))
    return 0 if final["status"] == "migrated" else 2


if __name__ == "__main__":
    raise SystemExit(main())
