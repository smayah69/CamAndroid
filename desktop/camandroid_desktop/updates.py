"""Check GitHub Releases for a newer CamAndroid and download the installer."""

from __future__ import annotations

import hashlib
import json
import logging
import os
import re
import sys
import tempfile
import urllib.request
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path

from . import __version__

log = logging.getLogger(__name__)

REPO = "smayah69/CamAndroid"
REPO_URL = f"https://github.com/{REPO}"
RELEASES_URL = f"{REPO_URL}/releases"
ISSUES_URL = f"{REPO_URL}/issues"
LATEST_API = f"https://api.github.com/repos/{REPO}/releases/latest"


class UpdateError(Exception):
    pass


@dataclass
class Asset:
    name: str
    url: str
    size: int
    sha256: str | None  # GitHub's "digest" for the uploaded file, when it provides one


@dataclass
class Release:
    version: str
    url: str
    notes: str
    assets: list[Asset]

    def asset(self, pattern: str) -> Asset | None:
        return next((a for a in self.assets if re.fullmatch(pattern, a.name)), None)

    @property
    def installer(self) -> Asset | None:
        return self.asset(r"CamAndroid-Setup-.*\.exe")


def parse_version(text: str) -> tuple[int, ...]:
    """'v0.3.41' -> (0, 3, 41). Pre-release suffixes ('-dev') are ignored."""
    core = text.strip().lstrip("vV").split("-", 1)[0].split("+", 1)[0]
    parts = []
    for piece in core.split("."):
        if not piece.isdigit():
            break
        parts.append(int(piece))
    return tuple(parts)


def is_newer(latest: str, current: str = __version__) -> bool:
    return parse_version(latest) > parse_version(current)


def _request(url: str) -> urllib.request.Request:
    return urllib.request.Request(url, headers={  # noqa: S310 - callers only pass https URLs
        "Accept": "application/vnd.github+json",
        "User-Agent": f"CamAndroid/{__version__}",
    })


def fetch_latest(timeout: float = 10.0) -> Release:
    try:
        with urllib.request.urlopen(_request(LATEST_API), timeout=timeout) as resp:  # noqa: S310 - fixed https URL
            data = json.load(resp)
    except OSError as e:
        raise UpdateError(f"could not reach GitHub: {e}") from e
    except ValueError as e:
        raise UpdateError("GitHub sent an unexpected reply") from e
    assets = []
    for a in data.get("assets", []):
        digest = str(a.get("digest") or "")
        assets.append(Asset(
            name=str(a.get("name", "")),
            url=str(a.get("browser_download_url", "")),
            size=int(a.get("size") or 0),
            sha256=digest.removeprefix("sha256:") if digest.startswith("sha256:") else None,
        ))
    tag = str(data.get("tag_name", ""))
    release = Release(tag.lstrip("vV"), str(data.get("html_url") or RELEASES_URL), str(data.get("body") or ""), assets)
    log.info("Latest release on GitHub: %s (this is %s)", release.version, __version__)
    return release


def download(asset: Asset, progress: Callable[[int, int], None] | None = None, dest_dir: Path | None = None) -> Path:
    """Downloads an asset, verifying its SHA-256 when GitHub publishes one."""
    if not asset.url.startswith("https://github.com/"):
        raise UpdateError(f"refusing to download from {asset.url}")
    dest_dir = dest_dir or Path(tempfile.mkdtemp(prefix="camandroid-update-"))
    dest = dest_dir / Path(asset.name).name
    sha = hashlib.sha256()
    done = 0
    try:
        with urllib.request.urlopen(_request(asset.url), timeout=30) as resp, open(dest, "wb") as out:  # noqa: S310
            total = int(resp.headers.get("Content-Length") or asset.size or 0)
            while chunk := resp.read(256 * 1024):
                out.write(chunk)
                sha.update(chunk)
                done += len(chunk)
                if progress:
                    progress(done, total)
    except OSError as e:
        raise UpdateError(f"download failed: {e}") from e
    if asset.sha256 and sha.hexdigest() != asset.sha256.lower():
        dest.unlink(missing_ok=True)
        raise UpdateError("the download is corrupted (SHA-256 does not match GitHub's); try again")
    log.info("Downloaded %s (%d bytes, sha256 %s)", dest, done, sha.hexdigest())
    return dest


def installed_with_setup() -> bool:
    """True when running the installed app (the installer can update it in place)."""
    if not getattr(sys, "frozen", False):
        return False
    return any(Path(sys.executable).resolve().parent.glob("unins*.exe"))


def clean_child_environment() -> None:
    """Removes PyInstaller's variables before starting the installer.

    The one-file bootloader passes _PYI_* to its children. The installer would hand them to the new
    CamAndroid it starts ("Start CamAndroid" box), which would then look for its Python DLL in this
    process's temp folder, deleted when we exit: "Failed to load Python DLL".
    """
    for key in [k for k in os.environ if k.upper().startswith(("_PYI_", "_MEIPASS"))]:
        del os.environ[key]  # on Windows this updates the real process environment block
    os.environ["PYINSTALLER_RESET_ENVIRONMENT"] = "1"  # in case anything is left: start afresh
