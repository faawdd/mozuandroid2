from __future__ import annotations

import asyncio
import base64
import json
import re
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Any, Optional
from urllib.parse import quote

import requests

from settings_store import AppSettings


API_BASE = "https://api.github.com"
POSTS_ROOT = "content/posts"


@dataclass
class ArticleEntry:
    name: str
    path: str
    sha: str = ""


@dataclass
class ArticleDraft:
    path: str
    title: str
    categories: list[str]
    body: str
    sha: str = ""
    date: str = ""


def slugify(value: str) -> str:
    """Create a conservative Markdown filename slug."""

    cleaned = re.sub(r"[^\w-]+", "-", value.strip(), flags=re.UNICODE)
    cleaned = re.sub(r"[-\s]+", "-", cleaned).strip("-_.")
    return cleaned.lower()


def split_categories(raw_value: str) -> list[str]:
    return [item.strip() for item in re.split(r"[,，/]+", raw_value) if item.strip()]


def build_front_matter(title: str, categories: list[str], date_value: str) -> str:
    """Serialize the minimal YAML Front Matter expected by Hugo."""

    lines = ["---", f'title: {json.dumps(title, ensure_ascii=False)}', f"date: {date_value}", "draft: false"]
    if categories:
        lines.append("categories:")
        for category in categories:
            lines.append(f'  - {json.dumps(category, ensure_ascii=False)}')
    lines.append("---")
    return "\n".join(lines)


def parse_front_matter(raw_text: str) -> tuple[dict[str, Any], str]:
    """Extract basic front matter fields and return the remaining Markdown body."""

    if not raw_text.startswith("---\n"):
        return {}, raw_text

    closing_index = raw_text.find("\n---\n", 4)
    if closing_index < 0:
        return {}, raw_text

    front_matter = raw_text[4:closing_index]
    body = raw_text[closing_index + 5 :]

    metadata: dict[str, Any] = {}
    lines = front_matter.splitlines()
    index = 0
    while index < len(lines):
        line = lines[index].strip()
        if not line or line.startswith("#"):
            index += 1
            continue

        if line.startswith("categories:"):
            categories: list[str] = []
            index += 1
            while index < len(lines):
                category_line = lines[index].strip()
                if not category_line.startswith("-"):
                    break
                categories.append(category_line.lstrip("-").strip().strip('"').strip("'"))
                index += 1
            metadata["categories"] = categories
            continue

        if ":" in line:
            key, value = line.split(":", 1)
            metadata[key.strip()] = value.strip().strip('"').strip("'")
        index += 1

    return metadata, body


class GitHubContentClient:
    """Small GitHub Contents API wrapper used by the mobile editor."""

    def __init__(self, settings: AppSettings) -> None:
        self.session = requests.Session()
        self.session.headers.update(
            {
                "Accept": "application/vnd.github+json",
                "X-GitHub-Api-Version": "2022-11-28",
            }
        )
        self.apply_settings(settings)

    def apply_settings(self, settings: AppSettings) -> None:
        self.settings = settings
        self.session.headers.pop("Authorization", None)
        if settings.github_token:
            self.session.headers["Authorization"] = f"Bearer {settings.github_token}"

    def _repo_url(self, endpoint: str) -> str:
        if not self.settings.repo_owner or not self.settings.repo_name:
            raise RuntimeError("请先在同步设置中配置 GitHub 仓库信息。")
        endpoint = endpoint.lstrip("/")
        return f"{API_BASE}/repos/{self.settings.repo_owner}/{self.settings.repo_name}/{endpoint}"

    def _request(self, method: str, endpoint: str, **kwargs: Any) -> requests.Response:
        url = self._repo_url(endpoint)
        response = self.session.request(method, url, timeout=30, **kwargs)
        if response.status_code >= 400:
            raise requests.HTTPError(response.text, response=response)
        return response

    async def request_async(self, method: str, endpoint: str, **kwargs: Any) -> requests.Response:
        return await asyncio.to_thread(self._request, method, endpoint, **kwargs)

    async def list_articles(self) -> list[ArticleEntry]:
        response = await self.request_async("GET", f"contents/{POSTS_ROOT}")
        payload = response.json()
        if isinstance(payload, dict):
            payload = [payload]

        return [
            ArticleEntry(name=item["name"], path=item["path"], sha=item.get("sha", ""))
            for item in payload
            if isinstance(item, dict) and item.get("type") == "file" and item.get("name", "").endswith(".md")
        ]

    async def get_article(self, path: str) -> ArticleDraft:
        response = await self.request_async("GET", f"contents/{quote(path, safe='/')}")
        payload = response.json()

        encoded_content = (payload.get("content") or "").replace("\n", "")
        raw_bytes = base64.b64decode(encoded_content.encode("utf-8"))
        raw_text = raw_bytes.decode("utf-8")

        metadata, body = parse_front_matter(raw_text)
        title = str(metadata.get("title", Path(path).stem))
        categories = metadata.get("categories", [])
        if not isinstance(categories, list):
            categories = []

        return ArticleDraft(
            path=payload.get("path", path),
            title=title,
            categories=[str(item) for item in categories],
            body=body.lstrip("\n"),
            sha=payload.get("sha", ""),
            date=str(metadata.get("date", datetime.now().astimezone().isoformat(timespec="seconds"))),
        )

    async def save_article(
        self,
        *,
        path: str,
        title: str,
        categories: list[str],
        body: str,
        sha: Optional[str] = None,
        date_value: Optional[str] = None,
    ) -> ArticleDraft:
        """Create or update a Markdown article through the GitHub Contents API."""

        date_value = date_value or datetime.now().astimezone().isoformat(timespec="seconds")
        front_matter = build_front_matter(title, categories, date_value)
        content_text = f"{front_matter}\n\n{body.rstrip()}\n"
        encoded_text = base64.b64encode(content_text.encode("utf-8")).decode("ascii")

        request_body: dict[str, Any] = {
            "message": f"Publish {title}",
            "content": encoded_text,
            "branch": self.settings.github_branch or "main",
        }
        if sha:
            request_body["sha"] = sha

        response = await self.request_async(
            "PUT",
            f"contents/{quote(path, safe='/')}",
            json=request_body,
        )
        payload = response.json()
        content_info = payload.get("content", {}) if isinstance(payload, dict) else {}

        return ArticleDraft(
            path=str(content_info.get("path", path)),
            title=title,
            categories=categories,
            body=body,
            sha=str(content_info.get("sha", sha or "")),
            date=date_value,
        )
