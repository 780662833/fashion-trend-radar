from __future__ import annotations

from getpass import getpass
from pathlib import Path

KEYS = ("DATAFORSEO_LOGIN", "DATAFORSEO_PASSWORD")


def main() -> int:
    env_path = Path(".env")
    existing = env_path.read_text(encoding="utf-8-sig").splitlines() if env_path.exists() else []
    values = {
        "DATAFORSEO_LOGIN": getpass("DataForSEO API login (input hidden): ").strip(),
        "DATAFORSEO_PASSWORD": getpass("DataForSEO API password (input hidden): ").strip(),
    }
    missing = [key for key, value in values.items() if not value]
    if missing:
        print(f"Not saved: missing {', '.join(missing)}.")
        return 2

    output: list[str] = []
    replaced: set[str] = set()
    for line in existing:
        key = line.split("=", 1)[0].strip() if "=" in line else ""
        if key in KEYS:
            if key not in replaced:
                output.append(f"{key}={values[key]}")
                replaced.add(key)
            continue
        output.append(line)
    for key in KEYS:
        if key not in replaced:
            output.append(f"{key}={values[key]}")
    env_path.write_text("\n".join(output).rstrip() + "\n", encoding="utf-8")
    print(f"Saved {', '.join(KEYS)} to {env_path.resolve()}. Values were not displayed.")
    print("Confirm .env is ignored by Git before committing any changes.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
