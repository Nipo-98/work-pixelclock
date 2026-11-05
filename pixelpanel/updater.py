"""Self-update from the GitHub releases of the project.

check() asks the GitHub API for the latest release, download() fetches PixelPanel.exe and verifies
its SHA-256 digest, swap() replaces the running exe: Windows does not allow overwriting a running
program, but it does allow renaming it, so the old file becomes PixelPanel.exe.old and the new one
takes its name; the new version deletes the .old file on start (cleanup()).
"""

from __future__ import annotations

import hashlib
import json
import logging
import os
import re
import subprocess
import sys
import urllib.request
from dataclasses import dataclass
from pathlib import Path

from . import __version__

log = logging.getLogger(__name__)

REPO = "Nipo-98/work-pixelclock"
LATEST_URL = f"https://api.github.com/repos/{REPO}/releases/latest"
ASSET = "PixelPanel.exe"
USER_AGENT = f"PixelPanel/{__version__}"
CHECK_EVERY_H = 6


class UpdateError(Exception):
    pass


def parse_version(tag: str) -> tuple[int, ...]:
    nums = re.findall(r"\d+", tag or "")
    return tuple(int(n) for n in nums) if nums else (0,)


@dataclass
class Release:
    tag: str
    version: tuple[int, ...]
    url: str
    size: int
    sha256: str | None
    page: str

    @classmethod
    def from_api(cls, data: dict) -> Release | None:
        asset = next((a for a in data.get("assets", []) if a.get("name") == ASSET), None)
        if asset is None:
            return None
        digest = asset.get("digest") or ""
        return cls(tag=data.get("tag_name", ""), version=parse_version(data.get("tag_name", "")),
                   url=asset["browser_download_url"], size=int(asset.get("size") or 0),
                   sha256=digest.removeprefix("sha256:") if digest.startswith("sha256:") else None,
                   page=data.get("html_url", f"https://github.com/{REPO}/releases/latest"))


def check(current: str = __version__) -> Release | None:
    """The latest release if it is newer than `current`, else None."""
    req = urllib.request.Request(LATEST_URL, headers={"Accept": "application/vnd.github+json", "User-Agent": USER_AGENT})
    with urllib.request.urlopen(req, timeout=15) as resp:
        release = Release.from_api(json.loads(resp.read()))
    if release is None or release.version <= parse_version(current):
        return None
    return release


def download(release: Release, folder: Path, progress=lambda done, total: None) -> Path:
    """Download the exe next to the running one and verify size and SHA-256; returns the file."""
    folder.mkdir(parents=True, exist_ok=True)
    target = folder / f"{ASSET}.download"
    sha = hashlib.sha256()
    done = 0
    req = urllib.request.Request(release.url, headers={"User-Agent": USER_AGENT})
    try:
        with urllib.request.urlopen(req, timeout=60) as resp, open(target, "wb") as out:
            while chunk := resp.read(256 * 1024):
                out.write(chunk)
                sha.update(chunk)
                done += len(chunk)
                progress(done, release.size)
        if release.size and done != release.size:
            raise UpdateError(f"download incomplete ({done} of {release.size} bytes)")
        if release.sha256 and sha.hexdigest() != release.sha256.lower():
            raise UpdateError("checksum does not match – the download was not installed")
    except BaseException:
        target.unlink(missing_ok=True)
        raise
    return target


def swap(new_file: Path, exe: Path) -> None:
    """Put `new_file` in place of the (running) `exe`; the old one is kept as .old until cleanup()."""
    old = exe.with_name(exe.name + ".old")
    old.unlink(missing_ok=True)
    try:
        os.replace(exe, old)  # renaming a running exe is allowed on Windows
    except OSError as err:
        raise UpdateError(f"cannot replace {exe.name}: {err}") from err
    try:
        os.replace(new_file, exe)
    except OSError as err:
        os.replace(old, exe)  # roll back, the app must stay startable
        raise UpdateError(f"cannot install the update: {err}") from err


def cleanup(exe: Path | None = None) -> None:
    """Remove the previous version's .old file and leftovers of a failed download."""
    exe = exe or running_exe()
    if exe is None:
        return
    for leftover in (exe.with_name(exe.name + ".old"), exe.with_name(exe.name + ".download")):
        try:
            leftover.unlink(missing_ok=True)
        except OSError:
            pass  # the old process is still exiting; next start removes it


def running_exe() -> Path | None:
    """The exe this app runs from, or None when it runs from the source code."""
    return Path(sys.executable) if getattr(sys, "frozen", False) else None


def restart_env() -> dict[str, str]:
    """Environment for a fresh, independent instance of the exe.

    A one-file PyInstaller exe passes _PYI_* variables to its children; a child that inherits them
    takes itself for part of the running app and uses its unpack folder in %TEMP% – which the old
    app deletes on exit (seen as "cannot import name '_imaging' from 'PIL'"). The bootloader's
    PYINSTALLER_RESET_ENVIRONMENT makes the new exe start from scratch."""
    env = {k: v for k, v in os.environ.items() if not k.startswith(("_PYI_", "_MEI"))}
    env["PYINSTALLER_RESET_ENVIRONMENT"] = "1"
    return env


def restart(exe: Path, args: list[str]) -> None:
    """Start the (new) exe as an independent process."""
    flags = getattr(subprocess, "DETACHED_PROCESS", 0) | getattr(subprocess, "CREATE_NEW_PROCESS_GROUP", 0)
    subprocess.Popen([str(exe), *args], close_fds=True, creationflags=flags, env=restart_env(),
                     cwd=str(exe.parent))
