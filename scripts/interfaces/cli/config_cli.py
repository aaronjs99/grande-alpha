"""Command-line views for explicit configuration and legacy-data operations."""

from __future__ import annotations

import argparse
import json
from pathlib import Path

from grande_alpha.configuration.config import (
    config_document,
    config_path,
    data_dir,
    load_config,
    migrate_legacy_data,
    upgrade_config,
)
from grande_alpha.execution.process_lock import ProcessLock
from grande_alpha.interfaces.cli.cli_table import format_table
from grande_alpha.interfaces.cli.parser_common import output_options
from grande_alpha.persistence.store import upgrade_execution_store


def add_config_commands(root_commands) -> None:
    config = root_commands.add_parser("config", help="Inspect or explicitly upgrade local configuration")
    commands = config.add_subparsers(dest="config_command", required=True)
    show = commands.add_parser("show", help="Read validated settings without writing files")
    show.add_argument("--path", type=Path, help="Configuration file to inspect")
    show.add_argument("--json", action="store_true")
    show.add_argument("--width", type=int)
    show.set_defaults(func=command_config_show)

    upgrade = commands.add_parser("upgrade", help="Back up and upgrade one existing configuration")
    upgrade.add_argument("--path", type=Path, help="Configuration file to upgrade")
    upgrade.add_argument("--json", action="store_true")
    upgrade.set_defaults(func=command_config_upgrade)

    import_legacy = commands.add_parser(
        "import-legacy", help="Explicitly copy recoverable legacy data without deleting its source"
    )
    import_legacy.add_argument("--source", type=Path, required=True, help="Legacy data directory")
    import_legacy.add_argument("--destination", type=Path, help="Target data directory")
    import_legacy.add_argument("--json", action="store_true")
    import_legacy.set_defaults(func=command_config_import_legacy)

    from grande_alpha.interfaces.cli.research_commands import command_glossary, command_plans

    glossary = commands.add_parser("glossary", help="Search the same definitions used by the GUI")
    glossary.add_argument("query", nargs="?")
    output_options(glossary)
    glossary.set_defaults(func=command_glossary)

    plans = commands.add_parser("plans", help="Show the free Community entitlement and truthful Pro roadmap")
    output_options(plans)
    plans.set_defaults(func=command_plans)


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


def command_execution_store_upgrade(args: argparse.Namespace) -> int:
    """Upgrade two existing journals only while the worker's app lock is held here."""
    audit_path = Path(args.audit).resolve(strict=True)
    legacy_path = Path(args.legacy_equity).resolve(strict=True)
    if not audit_path.is_file() or not legacy_path.is_file():
        raise ValueError("Both execution journals must be existing files")
    if audit_path == legacy_path:
        raise ValueError("Upgrade requires two distinct source journals")
    lock = ProcessLock(audit_path.parent / "app.lock")
    if not lock.acquire(timeout_seconds=0):
        raise RuntimeError("Stop the local worker before upgrading the execution store")
    try:
        result = upgrade_execution_store(audit_path, legacy_path, backup_dir=Path(args.backup_dir))
    finally:
        lock.release()
    if args.json:
        print(json.dumps(result, indent=2, sort_keys=True))
    else:
        print(f"Execution store upgraded. Audit backup: {result['audit_backup']}")
        print(f"Legacy equity backup: {result['equity_backup']}")
    return 0
