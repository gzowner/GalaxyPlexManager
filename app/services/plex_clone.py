from __future__ import annotations

import shutil
import subprocess
from pathlib import Path
from xml.etree import ElementTree


RESET_PREFERENCE_KEYS = {
    "AnonymousMachineIdentifier",
    "CertificateUUID",
    "CertificateVersion",
    "FriendlyName",
    "MachineIdentifier",
    "PlexOnlineHome",
    "PlexOnlineMail",
    "PlexOnlineToken",
    "PlexOnlineUsername",
    "ProcessedMachineIdentifier",
    "PubSubServer",
}

EXCLUDED_RELATIVE_PATHS = (
    "Library/Application Support/Plex Media Server/Cache",
    "Library/Application Support/Plex Media Server/Logs",
    "Library/Application Support/Plex Media Server/Crash Reports",
    "Library/Application Support/Plex Media Server/Diagnostics",
)


def clone_template(source: str, destination: str) -> Path:
    src = Path(source).resolve()
    dst = Path(destination).resolve()
    if not src.is_dir():
        raise RuntimeError(f"Master template does not exist: {src}")
    if dst.exists():
        raise RuntimeError(f"Deployment config already exists: {dst}")
    dst.parent.mkdir(parents=True, exist_ok=True)

    cp = shutil.which("cp")
    if cp:
        result = subprocess.run(
            [cp, "-a", "--reflink=auto", f"{src}/.", str(dst)],
            capture_output=True,
            text=True,
            check=False,
        )
        if result.returncode != 0:
            shutil.rmtree(dst, ignore_errors=True)
            shutil.copytree(src, dst, symlinks=True)
    else:
        shutil.copytree(src, dst, symlinks=True)

    _remove_transient_data(dst)
    _reset_preferences(dst)
    return dst


def _remove_transient_data(config_root: Path) -> None:
    for relative in EXCLUDED_RELATIVE_PATHS:
        shutil.rmtree(config_root / relative, ignore_errors=True)
    transcode = config_root / "Library/Application Support/Plex Media Server/Cache/Transcode"
    shutil.rmtree(transcode, ignore_errors=True)


def _reset_preferences(config_root: Path) -> None:
    preferences = config_root / "Library/Application Support/Plex Media Server/Preferences.xml"
    if not preferences.exists():
        return
    tree = ElementTree.parse(preferences)
    root = tree.getroot()
    for key in RESET_PREFERENCE_KEYS:
        root.attrib.pop(key, None)
    tree.write(preferences, encoding="utf-8", xml_declaration=True)


def plex_is_claimed(config_root: str) -> bool:
    preferences = Path(config_root) / "Library/Application Support/Plex Media Server/Preferences.xml"
    if not preferences.exists():
        return False
    try:
        root = ElementTree.parse(preferences).getroot()
    except ElementTree.ParseError:
        return False
    return bool(root.attrib.get("PlexOnlineToken"))


def safe_remove_config(path: str, required_parent: str) -> None:
    target = Path(path).resolve()
    parent = Path(required_parent).resolve()
    if target == parent or parent not in target.parents:
        raise RuntimeError("Refusing to delete a path outside the node deployment directory")
    shutil.rmtree(target, ignore_errors=False)
