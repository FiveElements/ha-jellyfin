"""Atheris harness for the REST client's injection guards.

Two properties keep a hostile item id or device name from reaching places it
must not: a path segment can never add a segment of its own (no traversal to
another endpoint), and the Authorization header always keeps its
``Key="value"`` shape (no forged Token field). api.py is loaded by path, so the
harness needs aiohttp and yarl but not Home Assistant.

    python fuzz/fuzz_api.py -max_total_time=60
"""

import importlib.util
from pathlib import Path
import re
import sys

import atheris

with atheris.instrument_imports():
    _PATH = (
        Path(__file__).resolve().parent.parent
        / "custom_components"
        / "jellyfin_control"
        / "api.py"
    )
    _SPEC = importlib.util.spec_from_file_location("jellyfin_api", _PATH)
    assert _SPEC is not None and _SPEC.loader is not None
    api = importlib.util.module_from_spec(_SPEC)
    sys.modules["jellyfin_api"] = api
    _SPEC.loader.exec_module(api)

BASE = "http://jellyfin.local:8096/jf"
HEADER = re.compile(
    r'MediaBrowser Client="[^",]*", Device="[^",]*", DeviceId="[^",]*", '
    r'Version="[^",]*"(, Token="[^",]*")?'
)


def _client(device: str, token: str | None) -> api.JellyfinClient:
    return api.JellyfinClient(
        None,  # type: ignore[arg-type]
        BASE,
        device_id=device,
        device_name=device,
        client_name="Home Assistant",
        client_version="1",
        access_token=token,
    )


def test_one_input(data: bytes) -> None:
    """Check both properties for one input."""
    fdp = atheris.FuzzedDataProvider(data)
    segments = [
        fdp.ConsumeUnicodeNoSurrogates(40) for _ in range(fdp.ConsumeIntInRange(1, 4))
    ]
    token = fdp.ConsumeUnicodeNoSurrogates(40) or None
    device = fdp.ConsumeUnicodeNoSurrogates(60)

    client = _client(device, token)
    url = client.build_url(*segments)
    assert url.host == "jellyfin.local"
    path = url.raw_path
    assert path.startswith("/jf/")
    parts = path[len("/jf/") :].split("/")
    assert len(parts) == len(segments), (segments, path)
    for part in parts:
        assert part not in (".", ".."), (segments, path)

    header = client.authorization()
    assert HEADER.fullmatch(header), header


def main() -> None:
    """Run the fuzzer."""
    atheris.Setup(sys.argv, test_one_input)
    atheris.Fuzz()


if __name__ == "__main__":
    main()
