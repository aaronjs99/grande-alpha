"""Read the declared packaging tools without native-shell quote rewriting."""

from __future__ import annotations

import json
import sys
import tomllib
from pathlib import Path


def main() -> None:
    project = tomllib.loads(Path(sys.argv[1]).read_text(encoding="utf-8"))
    requirements = [value for value in project["project"]["optional-dependencies"]["dev"]
                    if value.split(">", 1)[0] in {"pyinstaller", "pip-audit"}]
    if len(requirements) != 2:
        raise ValueError("Declare both pyinstaller and pip-audit in the development dependencies")
    print(json.dumps(requirements))


if __name__ == "__main__":
    main()
