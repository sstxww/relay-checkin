#!/usr/bin/env python3
"""Extensible daily check-in client for API relay sites."""

from __future__ import annotations

import argparse
import json
import os
import re
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable, Mapping, Sequence
from urllib.error import HTTPError, URLError
from urllib.parse import urljoin, urlparse
from urllib.request import Request, urlopen


VERSION = "0.1.0"
ENV_NAME_PATTERN = re.compile(r"^[A-Z_][A-Z0-9_]*$")


class CheckinError(RuntimeError):
    """Raised when configuration or a check-in request is invalid."""


@dataclass(frozen=True)
class SiteConfig:
    site_id: str
    name: str
    homepage: str
    base_url: str
    checkin_path: str
    access_token_env: str
    user_id_env: str
    already_checked_in_messages: tuple[str, ...]

    @classmethod
    def from_mapping(cls, value: Mapping[str, Any]) -> "SiteConfig":
        required = (
            "id",
            "name",
            "homepage",
            "base_url",
            "checkin_path",
            "access_token_env",
            "user_id_env",
        )
        missing = [key for key in required if not value.get(key)]
        if missing:
            raise CheckinError(f"site config is missing: {', '.join(missing)}")

        base_url = str(value["base_url"]).rstrip("/")
        homepage = str(value["homepage"])
        checkin_path = str(value["checkin_path"])
        access_token_env = str(value["access_token_env"])
        user_id_env = str(value["user_id_env"])

        for label, url in (("homepage", homepage), ("base_url", base_url)):
            parsed = urlparse(url)
            if parsed.scheme != "https" or not parsed.netloc:
                raise CheckinError(f"{label} must be an absolute HTTPS URL")
            if parsed.username or parsed.password:
                raise CheckinError(f"{label} must not contain credentials")

        if not checkin_path.startswith("/"):
            raise CheckinError("checkin_path must start with '/'")

        for env_name in (access_token_env, user_id_env):
            if not ENV_NAME_PATTERN.fullmatch(env_name):
                raise CheckinError(f"invalid environment variable name: {env_name}")

        messages = value.get(
            "already_checked_in_messages",
            ["already checked in"],
        )
        if not isinstance(messages, list) or not all(
            isinstance(message, str) and message for message in messages
        ):
            raise CheckinError("already_checked_in_messages must be a string list")

        return cls(
            site_id=str(value["id"]),
            name=str(value["name"]),
            homepage=homepage,
            base_url=base_url,
            checkin_path=checkin_path,
            access_token_env=access_token_env,
            user_id_env=user_id_env,
            already_checked_in_messages=tuple(messages),
        )


@dataclass(frozen=True)
class CheckinResult:
    site: SiteConfig
    message: str
    quota_awarded: int | float | None = None
    already_checked_in: bool = False


def load_sites(path: Path) -> list[SiteConfig]:
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except FileNotFoundError as exc:
        raise CheckinError(f"config file not found: {path}") from exc
    except json.JSONDecodeError as exc:
        raise CheckinError(f"invalid JSON in {path}: {exc}") from exc

    raw_sites = payload.get("sites") if isinstance(payload, dict) else None
    if not isinstance(raw_sites, list) or not raw_sites:
        raise CheckinError("config must contain a non-empty 'sites' list")

    sites = [SiteConfig.from_mapping(item) for item in raw_sites]
    ids = [site.site_id for site in sites]
    if len(ids) != len(set(ids)):
        raise CheckinError("site IDs must be unique")
    return sites


def _required_env(site: SiteConfig, environ: Mapping[str, str]) -> tuple[str, str]:
    token = environ.get(site.access_token_env, "").strip()
    user_id = environ.get(site.user_id_env, "").strip()
    missing = []
    if not token:
        missing.append(site.access_token_env)
    if not user_id:
        missing.append(site.user_id_env)
    if missing:
        raise CheckinError(f"{site.name}: missing environment variables: {', '.join(missing)}")
    return token, user_id


def check_in(
    site: SiteConfig,
    environ: Mapping[str, str],
    timeout: float = 30.0,
    opener: Callable[..., Any] = urlopen,
) -> CheckinResult:
    token, user_id = _required_env(site, environ)
    endpoint = urljoin(f"{site.base_url}/", site.checkin_path.lstrip("/"))
    request = Request(
        endpoint,
        data=b"{}",
        method="POST",
        headers={
            "Accept": "application/json",
            "Content-Type": "application/json",
            "Authorization": token,
            "New-Api-User": user_id,
            "User-Agent": f"relay-checkin/{VERSION}",
        },
    )

    try:
        with opener(request, timeout=timeout) as response:
            status = getattr(response, "status", 200)
            body = response.read().decode("utf-8")
    except HTTPError as exc:
        body = exc.read().decode("utf-8", errors="replace")
        raise CheckinError(f"{site.name}: HTTP {exc.code}: {_safe_message(body)}") from exc
    except URLError as exc:
        raise CheckinError(f"{site.name}: network error: {exc.reason}") from exc

    if not 200 <= status < 300:
        raise CheckinError(f"{site.name}: HTTP {status}: {_safe_message(body)}")

    try:
        payload = json.loads(body)
    except json.JSONDecodeError as exc:
        raise CheckinError(f"{site.name}: response is not valid JSON") from exc

    message = str(payload.get("message") or "")
    if payload.get("success") is True:
        data = payload.get("data") if isinstance(payload.get("data"), dict) else {}
        return CheckinResult(
            site=site,
            message=message or "check-in succeeded",
            quota_awarded=data.get("quota_awarded"),
        )

    message_lower = message.lower()
    if any(marker.lower() in message_lower for marker in site.already_checked_in_messages):
        return CheckinResult(
            site=site,
            message=message,
            already_checked_in=True,
        )

    raise CheckinError(f"{site.name}: {message or 'check-in rejected'}")


def _safe_message(body: str) -> str:
    try:
        payload = json.loads(body)
    except json.JSONDecodeError:
        return "non-JSON response"
    if isinstance(payload, dict) and payload.get("message"):
        return str(payload["message"])
    return "request failed"


def select_sites(sites: Sequence[SiteConfig], selected_ids: Sequence[str]) -> list[SiteConfig]:
    if not selected_ids:
        return list(sites)
    requested = set(selected_ids)
    selected = [site for site in sites if site.site_id in requested]
    missing = sorted(requested - {site.site_id for site in selected})
    if missing:
        raise CheckinError(f"unknown site IDs: {', '.join(missing)}")
    return selected


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--config",
        type=Path,
        default=Path(__file__).with_name("sites.json"),
        help="path to the site configuration JSON",
    )
    parser.add_argument(
        "--site",
        action="append",
        default=[],
        help="run only this site ID; may be repeated",
    )
    parser.add_argument("--timeout", type=float, default=30.0)
    parser.add_argument("--list", action="store_true", help="list configured sites")
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="validate config and required environment variables without sending requests",
    )
    parser.add_argument("--version", action="version", version=VERSION)
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    try:
        sites = select_sites(load_sites(args.config), args.site)
        if args.list:
            for site in sites:
                print(f"{site.site_id}\t{site.name}\t{site.homepage}")
            return 0

        failures = 0
        for site in sites:
            try:
                if args.dry_run:
                    _required_env(site, os.environ)
                    print(f"[OK] {site.name}: configuration is valid")
                    continue

                result = check_in(site, os.environ, timeout=args.timeout)
                if result.already_checked_in:
                    print(f"[OK] {site.name}: {result.message}")
                elif result.quota_awarded is not None:
                    print(f"[OK] {site.name}: {result.message}; quota_awarded={result.quota_awarded}")
                else:
                    print(f"[OK] {site.name}: {result.message}")
            except CheckinError as exc:
                failures += 1
                print(f"[ERROR] {exc}", file=sys.stderr)
        return 1 if failures else 0
    except CheckinError as exc:
        print(f"[ERROR] {exc}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
