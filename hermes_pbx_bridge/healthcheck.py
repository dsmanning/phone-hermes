import argparse
from dataclasses import asdict, dataclass
import json
import os
from pathlib import Path
import re
import sys
import tempfile
from urllib.request import Request, urlopen

from .speech_cli import _client_factory
from .wyoming import Event


@dataclass(frozen=True, slots=True)
class CheckResult:
    name: str
    healthy: bool
    detail: str


@dataclass(frozen=True, slots=True)
class Transition:
    kind: str
    failed: tuple[str, ...]
    recovered: tuple[str, ...]


def classify_transition(
    previous: dict[str, bool], current: list[CheckResult]
) -> Transition:
    failed = tuple(
        result.name
        for result in current
        if not result.healthy and previous.get(result.name, True)
    )
    recovered = tuple(
        result.name
        for result in current
        if result.healthy and previous.get(result.name) is False
    )
    if failed:
        kind = "failure"
    elif recovered:
        kind = "recovery"
    else:
        kind = "unchanged"
    return Transition(kind, failed, recovered)


def load_state(path: Path) -> dict[str, bool]:
    try:
        raw = json.loads(path.read_text())
    except (FileNotFoundError, json.JSONDecodeError, OSError):
        return {}
    if not isinstance(raw, dict):
        return {}
    return {str(name): bool(healthy) for name, healthy in raw.items()}


def save_state(path: Path, results: list[CheckResult]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    descriptor, temporary_name = tempfile.mkstemp(
        prefix=f".{path.name}.", suffix=".partial", dir=path.parent
    )
    try:
        with os.fdopen(descriptor, "w") as stream:
            json.dump({result.name: result.healthy for result in results}, stream)
            stream.write("\n")
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temporary_name, path)
    except BaseException:
        try:
            os.unlink(temporary_name)
        except FileNotFoundError:
            pass
        raise


def _safe_check(name, operation) -> CheckResult:
    try:
        detail = operation()
    except Exception as error:
        return CheckResult(name, False, f"{type(error).__name__}")
    return CheckResult(name, True, detail)


def _http_json(url: str, validator) -> str:
    request = Request(url, headers={"Accept": "application/json"})
    with urlopen(request, timeout=10) as response:
        if response.status != 200:
            raise RuntimeError("unexpected HTTP status")
        body = json.load(response)
    if not validator(body):
        raise ValueError("unexpected response schema")
    return "application response validated"


def validate_frigate_version(body: str) -> bool:
    return re.fullmatch(r"\d+\.\d+\.\d+(?:[-+][A-Za-z0-9.-]+)?", body.strip()) is not None


def _http_text(url: str, validator) -> str:
    request = Request(url, headers={"Accept": "text/plain"})
    with urlopen(request, timeout=10) as response:
        if response.status != 200:
            raise RuntimeError("unexpected HTTP status")
        body = response.read().decode("utf-8")
    if not validator(body):
        raise ValueError("unexpected response body")
    return "application response validated"


def _wyoming_info(host: str, port: int, proxy_url: str | None) -> str:
    factory = _client_factory(host, port, 10, proxy_url)
    with factory() as client:
        client.send(Event("describe"))
        response = client.receive()
    if response.type != "info":
        raise ValueError("unexpected Wyoming response")
    return "Wyoming info returned"


def _nas_write(path: Path) -> str:
    path.mkdir(parents=True, exist_ok=True)
    descriptor, probe = tempfile.mkstemp(prefix=".healthcheck-", dir=path)
    try:
        with os.fdopen(descriptor, "w") as stream:
            stream.write("ok\n")
            stream.flush()
            os.fsync(stream.fileno())
    finally:
        try:
            os.unlink(probe)
        except FileNotFoundError:
            pass
    return "write probe passed"


def run_checks(environ=None) -> list[CheckResult]:
    environment = os.environ if environ is None else environ
    proxy_url = (
        environment.get("WYOMING_PROXY")
        or environment.get("HTTP_PROXY")
        or environment.get("http_proxy")
    )
    return [
        _safe_check(
            "frigate",
            lambda: _http_text(
                "http://frigate.agent.lan/api/version",
                validate_frigate_version,
            ),
        ),
        _safe_check(
            "home-assistant",
            lambda: _http_json(
                "http://homeassistant.agent.lan/api/",
                lambda body: isinstance(body, dict)
                and body.get("message") == "API running.",
            ),
        ),
        _safe_check(
            "piper",
            lambda: _wyoming_info("piper.agent.lan", 10200, proxy_url),
        ),
        _safe_check(
            "whisper",
            lambda: _wyoming_info("whisper.agent.lan", 10300, proxy_url),
        ),
        _safe_check(
            "nas-output",
            lambda: _nas_write(Path(environment.get("HEALTHCHECK_OUTPUT", "/nas-output"))),
        ),
    ]


def main(argv=None, environ=None, stdout=None) -> int:
    environment = os.environ if environ is None else environ
    output = sys.stdout if stdout is None else stdout
    parser = argparse.ArgumentParser(
        description="Check established least-privilege Hermes service paths."
    )
    parser.add_argument(
        "--state",
        type=Path,
        default=Path(
            environment.get(
                "HERMES_SERVICE_HEALTH_STATE",
                "/nas-output/.hermes-service-health.json",
            )
        ),
    )
    args = parser.parse_args(argv)

    previous = load_state(args.state)
    results = run_checks(environment)
    transition = classify_transition(previous, results)
    save_state(args.state, results)
    payload = {
        "transition": transition.kind,
        "failed": list(transition.failed),
        "recovered": list(transition.recovered),
        "healthy": all(result.healthy for result in results),
        "checks": [asdict(result) for result in results],
    }
    print(json.dumps(payload, separators=(",", ":")), file=output)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
