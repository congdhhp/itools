"""Configuration, profiles and secret handling (no secrets on disk)."""

from __future__ import annotations

import os
import tomllib
from dataclasses import dataclass
from pathlib import Path
from urllib.parse import urlparse


class ConfigurationError(ValueError):
    """Invalid or missing CLI configuration."""


DEFAULT_CONFIG = Path.home() / ".config" / "itools" / "config.toml"
EXAMPLE_CONFIG = """# Store credentials in environment variables, NEVER in this file.
[profiles.internal.jira]
url = "https://jira.example.internal"
auth = "pat"
token_env = "ITOOLS_JIRA_TOKEN"
# ca_bundle = "/path/to/internal-ca.pem"
"""


def config_path(path: Path | None = None) -> Path:
    return path or Path(os.environ.get("ITOOLS_CONFIG", str(DEFAULT_CONFIG))).expanduser()


def load_config(path: Path | None = None) -> dict:
    target = config_path(path)
    if not target.exists():
        return {}
    try:
        with target.open("rb") as handle:
            data = tomllib.load(handle)
    except (OSError, tomllib.TOMLDecodeError) as exc:
        raise ConfigurationError(f"Cannot load {target}: {exc}") from exc
    if not isinstance(data, dict):
        raise ConfigurationError("Config root must be a TOML table")
    return data


@dataclass(frozen=True)
class JiraSettings:
    url: str
    auth: str
    token: str | None = None
    username: str | None = None
    password: str | None = None
    ca_bundle: str | None = None

    def __repr__(self) -> str:
        return f"JiraSettings(url={self.url!r}, auth={self.auth!r}, credentials='***')"


def jira_settings(profile: str | None = None, path: Path | None = None) -> JiraSettings:
    config = load_config(path)
    selected = profile or os.environ.get("ITOOLS_PROFILE", "internal")
    profiles = config.get("profiles", {})
    data = profiles.get(selected, {}).get("jira", {}) if isinstance(profiles, dict) else {}
    if not isinstance(data, dict):
        raise ConfigurationError(f"Invalid Jira configuration for profile {selected!r}")

    url = (os.environ.get("ITOOLS_JIRA_URL") or data.get("url") or "").strip().rstrip("/")
    parsed = urlparse(url)
    if parsed.scheme not in ("http", "https") or not parsed.hostname:
        raise ConfigurationError(
            f"Missing/invalid Jira URL (profile {selected!r}); "
            "set ITOOLS_JIRA_URL or profiles.<name>.jira.url"
        )
    auth = str(os.environ.get("ITOOLS_JIRA_AUTH") or data.get("auth") or "pat").lower()
    ca_bundle = os.environ.get("ITOOLS_JIRA_CA_BUNDLE") or data.get("ca_bundle")
    if ca_bundle and not Path(ca_bundle).expanduser().is_file():
        raise ConfigurationError(f"CA bundle does not exist: {ca_bundle}")

    if auth == "pat":
        env_name = str(data.get("token_env") or "ITOOLS_JIRA_TOKEN")
        token = os.environ.get(env_name)
        if not token:
            raise ConfigurationError(f"Missing Jira PAT: set environment variable {env_name}")
        return JiraSettings(url, auth, token=token, ca_bundle=ca_bundle)
    if auth == "basic":
        user_env = str(data.get("username_env") or "ITOOLS_JIRA_USERNAME")
        pass_env = str(data.get("password_env") or "ITOOLS_JIRA_PASSWORD")
        username, password = os.environ.get(user_env), os.environ.get(pass_env)
        if not username or not password:
            raise ConfigurationError(f"Basic auth requires {user_env} and {pass_env}")
        return JiraSettings(url, auth, username=username, password=password, ca_bundle=ca_bundle)
    raise ConfigurationError("Jira auth must be either 'pat' or 'basic'")


def initialize_config(path: Path | None = None, *, force: bool = False) -> Path:
    target = config_path(path)
    if target.exists() and not force:
        raise ConfigurationError(f"Configuration already exists: {target} (use --force)")
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(EXAMPLE_CONFIG, encoding="utf-8")
    try:
        target.chmod(0o600)
    except OSError:
        pass
    return target
