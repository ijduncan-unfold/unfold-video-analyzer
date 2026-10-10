"""Write build dependency versions without pip URLs, credentials, or local paths."""
import importlib.metadata
import json
from pathlib import Path
import sys


def main():
    destination = Path(sys.argv[1])
    packages = sorted(
        [{"name":package.metadata["Name"], "version":package.version}
         for package in importlib.metadata.distributions()],
        key=lambda package: package["name"].lower(),
    )
    destination.parent.mkdir(parents=True, exist_ok=True)
    destination.write_text(json.dumps(packages, indent=2) + "\n", encoding="utf-8")


if __name__ == "__main__":
    main()
