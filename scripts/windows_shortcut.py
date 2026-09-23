from __future__ import annotations

import argparse
import sys
from pathlib import Path

WINDOWS_APP_USER_MODEL_ID = "GRANDEAlpha.Desktop"


def _property_store(shortcut_path: Path, *, writable: bool):
    if sys.platform != "win32":
        raise RuntimeError("Windows shortcut identity is only available on Windows")
    if shortcut_path.suffix.lower() != ".lnk":
        raise ValueError("Windows shortcut identity requires a .lnk file")
    if not shortcut_path.is_file():
        raise FileNotFoundError(shortcut_path)

    from win32com.propsys import propsys
    from win32com.shell import shellcon

    flags = shellcon.GPS_READWRITE if writable else shellcon.GPS_DEFAULT
    return propsys.SHGetPropertyStoreFromParsingName(
        str(shortcut_path),
        None,
        flags,
        propsys.IID_IPropertyStore,
    )


def shortcut_app_user_model_id(shortcut_path: str | Path) -> str | None:
    """Return the explicit taskbar identity stored on a Windows shortcut."""
    from win32com.propsys import propsys

    store = _property_store(Path(shortcut_path), writable=False)
    key = propsys.PSGetPropertyKeyFromName("System.AppUserModel.ID")
    value = store.GetValue(key).GetValue()
    return str(value) if value else None


def set_shortcut_app_user_model_id(
    shortcut_path: str | Path,
    app_id: str = WINDOWS_APP_USER_MODEL_ID,
) -> None:
    """Bind a .lnk to the same identity used by the running Qt process."""
    if not app_id.strip():
        raise ValueError("AppUserModelID cannot be empty")

    from win32com.propsys import propsys

    path = Path(shortcut_path)
    store = _property_store(path, writable=True)
    key = propsys.PSGetPropertyKeyFromName("System.AppUserModel.ID")
    store.SetValue(key, propsys.PROPVARIANTType(app_id))
    store.Commit()
    value = store.GetValue(key).GetValue()
    actual = str(value) if value else None
    if actual != app_id:
        raise RuntimeError(f"Shortcut identity verification failed: expected {app_id!r}, got {actual!r}")


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Configure GRANDE Alpha Windows shortcut identity")
    parser.add_argument("shortcuts", type=Path, nargs="+")
    parser.add_argument("--check", action="store_true", help="verify without changing shortcuts")
    args = parser.parse_args(argv)

    for shortcut in args.shortcuts:
        if not args.check:
            set_shortcut_app_user_model_id(shortcut)
        actual = shortcut_app_user_model_id(shortcut)
        if actual != WINDOWS_APP_USER_MODEL_ID:
            print(f"MISMATCH {shortcut}: {actual or 'unset'}", file=sys.stderr)
            return 1
        print(f"IDENTITY OK {shortcut}: {actual}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
