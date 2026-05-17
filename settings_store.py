from __future__ import annotations

import json
import os
from dataclasses import asdict, dataclass
from pathlib import Path


SETTINGS_FILE = Path(__file__).with_name("app_settings.json")


@dataclass
class AppSettings:
    """Persisted GitHub connection settings for the mobile app."""

    github_token: str = ""
    repo_owner: str = ""
    repo_name: str = ""
    github_branch: str = "main"

    @classmethod
    def load(cls, path: Path = SETTINGS_FILE) -> "AppSettings":
        """Load settings from disk and fall back to environment variables."""

        settings = cls(
            github_token=os.getenv("GITHUB_TOKEN", ""),
            repo_owner=os.getenv("REPO_OWNER", ""),
            repo_name=os.getenv("REPO_NAME", ""),
            github_branch=os.getenv("GITHUB_BRANCH", "main"),
        )

        if not path.exists():
            return settings

        try:
            payload = json.loads(path.read_text(encoding="utf-8"))
        except Exception:
            return settings

        if isinstance(payload, dict):
            settings.github_token = str(payload.get("github_token", settings.github_token))
            settings.repo_owner = str(payload.get("repo_owner", settings.repo_owner))
            settings.repo_name = str(payload.get("repo_name", settings.repo_name))
            settings.github_branch = str(payload.get("github_branch", settings.github_branch))

        return settings

    def save(self, path: Path = SETTINGS_FILE) -> None:
        """Persist the current settings to disk."""

        path.write_text(json.dumps(asdict(self), ensure_ascii=False, indent=2), encoding="utf-8")

    def apply_to_environment(self) -> None:
        """Mirror the current values into the process environment."""

        os.environ["GITHUB_TOKEN"] = self.github_token
        os.environ["REPO_OWNER"] = self.repo_owner
        os.environ["REPO_NAME"] = self.repo_name
        os.environ["GITHUB_BRANCH"] = self.github_branch or "main"

    def is_configured(self) -> bool:
        return bool(self.repo_owner and self.repo_name)
