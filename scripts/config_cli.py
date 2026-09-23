"""Command-line views for explicit configuration and legacy-data operations."""

from __future__ import annotations

import argparse
import json
from pathlib import Path

from grande_alpha.cli_table import format_table
from grande_alpha.config import (
    config_document,
    config_path,
    data_dir,
    load_config,
    migrate_legacy_data,
    upgrade_config,
)


def command_config_show(args: argparse.Namespace) -> int:
    """Print validated settings without creating, upgrading, or changing them."""
    path = Path(args.path) if args.path else None
    config = load_config(path)
    resolved_path = path or config_path()
    document = config_document(config)
    payload = {"path": str(resolved_path), "settings": document}
    if args.json:
        print(json.dumps(payload, indent=2, sort_keys=True))
        return 0
    rows = [["schema_version", document["schema_version"]]]
    rows.extend(
        [f"{section}.{name}", value]
        for section, values in document.items() if isinstance(values, dict)
        for name, value in values.items()
    )
    print(format_table(["Setting", "Value"], rows, args.width))
    print(f"Path: {payload['path']}")
    return 0


def command_config_upgrade(args: argparse.Namespace) -> int:
    """Back up and upgrade an explicitly selected configuration file."""
    path = Path(args.path) if args.path else None
    backup = upgrade_config(path)
    target = path or config_path()
    payload = {"path": str(target), "backup": str(backup) if backup else None, "upgraded": backup is not None}
    if args.json:
        print(json.dumps(payload, indent=2, sort_keys=True))
    elif backup:
        print(f"Upgraded {target}; backup retained at {backup}")
    else:
        print(f"No saved configuration exists at {target}; defaults were not written.")
    return 0


def command_config_import_legacy(args: argparse.Namespace) -> int:
    """Copy selected legacy data without automatic discovery or source deletion."""
    source = Path(args.source)
    destination = Path(args.destination) if args.destination else data_dir()
    copied = migrate_legacy_data(source, destination)
    payload = {
        "source": str(source),
        "destination": str(destination),
        "copied": [str(path) for path in copied],
        "source_preserved": True,
    }
    if args.json:
        print(json.dumps(payload, indent=2, sort_keys=True))
    elif copied:
        print(f"Copied {len(copied)} legacy files to {destination}; source files remain unchanged.")
    else:
        print("No eligible legacy files were copied; existing destination files were left unchanged.")
    return 0
