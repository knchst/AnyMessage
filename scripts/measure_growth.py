#!/usr/bin/env python3
"""Save one GitHub growth snapshot without collecting message or client data."""

from __future__ import annotations

import argparse
from datetime import datetime, timezone
import json
from pathlib import Path
import subprocess


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--repo", default="knchst/AnyMessage")
    parser.add_argument("--output", type=Path, default=Path(".audit/growth.jsonl"))
    args = parser.parse_args()
    endpoints = {
        "repository": f"repos/{args.repo}",
        "views": f"repos/{args.repo}/traffic/views",
        "clones": f"repos/{args.repo}/traffic/clones",
        "referrers": f"repos/{args.repo}/traffic/popular/referrers",
    }
    responses = {
        name: json.loads(subprocess.run(["gh", "api", path], check=True, capture_output=True, text=True, timeout=30).stdout)
        for name, path in endpoints.items()
    }
    repository = responses["repository"]
    snapshot = {
        "captured_at": datetime.now(timezone.utc).isoformat(),
        "repository": repository["full_name"],
        "stars": repository["stargazers_count"],
        "forks": repository["forks_count"],
        "views_14d": responses["views"],
        "clones_14d": responses["clones"],
        "referrers_14d": responses["referrers"],
    }
    args.output.parent.mkdir(parents=True, exist_ok=True)
    with args.output.open("a") as output:
        output.write(json.dumps(snapshot, ensure_ascii=False) + "\n")
    print(f"{snapshot['repository']}: {snapshot['stars']} stars, {snapshot['forks']} forks. Saved to {args.output}.")
    print("Traffic covers a rolling 14-day window. Views and clones do not establish active users or conversions.")


if __name__ == "__main__":
    main()
