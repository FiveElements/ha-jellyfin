"""Tell apart browser clients that all report the same device name.

Jellyfin Web names every Chrome tab "Chrome". Its device id is the browser's
User-Agent and a timestamp, base64-encoded with "=" replaced by "1"
(jellyfin-web `getDeviceId`), so the operating system and browser version can
be read back from it. No Home Assistant import here.
"""

from __future__ import annotations

import base64
import binascii
from dataclasses import dataclass
import re

# Order matters: Edge and Opera also announce Chrome, Chrome announces Safari.
_BROWSERS = (
    ("Edg/", "Edge"),
    ("OPR/", "Opera"),
    ("Firefox/", "Firefox"),
    ("Chrome/", "Chrome"),
    ("Version/", "Safari"),
)
# Order matters: Android announces Linux, iPadOS can announce Mac OS X.
_SYSTEMS = (
    ("Windows", "Windows"),
    ("Android", "Android"),
    ("iPhone", "iOS"),
    ("iPad", "iPadOS"),
    ("CrOS", "ChromeOS"),
    ("Mac OS X", "macOS"),
    ("Linux", "Linux"),
)


@dataclass(frozen=True, slots=True)
class BrowserInfo:
    """What a web client's User-Agent says about where it runs."""

    system: str | None
    browser: str | None
    version: str | None

    @property
    def label(self) -> str | None:
        """Return "Chrome 156", or None when the browser is unknown."""
        if self.browser is None:
            return None
        return f"{self.browser} {self.version}" if self.version else self.browser


def user_agent_from_device_id(device_id: str) -> str | None:
    """Return the User-Agent encoded in a Jellyfin Web device id, if any."""
    stripped = device_id.rstrip("1")
    # Each "=" of padding became a "1"; put back as many as the length needs
    # (a real trailing "1" of the data is put back too).
    for padding in range(len(device_id) - len(stripped) + 1):
        candidate = stripped + "1" * padding
        candidate += "=" * (-len(candidate) % 4)
        try:
            text = base64.b64decode(candidate, validate=True).decode("utf-8")
        except binascii.Error, UnicodeDecodeError, ValueError:
            continue
        user_agent, separator, _ = text.rpartition("|")
        if separator and user_agent.startswith("Mozilla/"):
            return user_agent
    return None


def browser_info(user_agent: str) -> BrowserInfo:
    """Read the operating system and browser from a User-Agent."""
    system = next((name for key, name in _SYSTEMS if key in user_agent), None)
    browser = version = None
    for key, name in _BROWSERS:
        if match := re.search(re.escape(key) + r"(\d+)", user_agent):
            browser, version = name, match.group(1)
            break
    return BrowserInfo(system, browser, version)


def client_browser(device_id: str | None) -> BrowserInfo | None:
    """Return the browser behind a Jellyfin Web device id, if it is one."""
    if not device_id:
        return None
    user_agent = user_agent_from_device_id(device_id)
    return browser_info(user_agent) if user_agent else None
