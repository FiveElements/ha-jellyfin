# Development

```bash
docker run --rm -v "$PWD:/src" -v ha-jellyfin-pip:/root/.cache/pip -w /src \
  python:3.14-bookworm bash -lc '
    pip install -q -r requirements-test.txt
    ruff check custom_components tests fuzz scripts
    ruff format --check custom_components tests fuzz scripts
    pytest tests -q --cov=custom_components.jellyfin_control --cov-fail-under=95
    mypy custom_components/jellyfin_control
  '
```

Home Assistant does not run natively on Windows (`fcntl`): use Docker or CI.

The tests run the real REST client against an in-memory Jellyfin
(`tests/conftest.py`, `FakeJellyfin`) injected in place of the HTTP session.

`fuzz/fuzz_api.py` checks with Atheris that a path segment cannot escape and
that the `Authorization` header keeps its shape whatever the device name or
token.

Releases: `git tag vX.Y.Z && git push origin vX.Y.Z`; the workflow stamps the
version into the manifest of `jellyfin_control.zip`, which HACS installs.
