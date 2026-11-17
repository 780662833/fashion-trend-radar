from __future__ import annotations

from getpass import getpass
from pathlib import Path


def main() -> int:
    env_path = Path(".env")
    lines = env_path.read_text(encoding="utf-8-sig").splitlines() if env_path.exists() else []
    secret = getpass("Paste the restricted YouTube Data API key (input hidden): ").strip()
    if not secret:
        print("No key entered; .env was not changed.")
        return 2
    if any(char in secret for char in "\r\n"):
        print("Invalid key format; .env was not changed.")
        return 2

    updated = []
    replaced = False
    for line in lines:
        if line.strip().startswith("YOUTUBE_API_KEY="):
            if not replaced:
                updated.append(f"YOUTUBE_API_KEY={secret}")
                replaced = True
        else:
            updated.append(line)
    if not replaced:
        updated.append(f"YOUTUBE_API_KEY={secret}")

    env_path.write_text("\n".join(updated).rstrip() + "\n", encoding="utf-8")
    print("YouTube API key saved to .env. The key was not displayed.")
    print("Confirm that .env is ignored by Git before committing other changes.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
