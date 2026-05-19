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
LEGACY_POSTS_ROOT = "content/post"


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

    normalized_categories = [item.strip() for item in categories if item.strip()]
    categories_yaml = json.dumps(normalized_categories, ensure_ascii=False)
    lines = [
        "---",
        f'title: {json.dumps(title, ensure_ascii=False)}',
        "description: ",
        f"date: {date_value}",
        "image: ",
        "math: ",
        "license: ",
        "comments: true",
        "tags: []",
        f"categories: {categories_yaml}",
        "draft: false",
        "build:",
        '    list: always    # Change to "never" to hide the page from the list',
    ]
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
            _, _, category_value = line.partition(":")
            category_value = category_value.strip()

            if category_value:
                if category_value.startswith("[") and category_value.endswith("]"):
                    try:
                        parsed = json.loads(category_value)
                        if isinstance(parsed, list):
                            categories = [str(item).strip() for item in parsed if str(item).strip()]
                    except Exception:
                        raw_items = category_value[1:-1]
                        categories = [
                            item.strip().strip('"').strip("'")
                            for item in raw_items.split(",")
                            if item.strip().strip('"').strip("'")
                        ]
                else:
                    scalar = category_value.strip().strip('"').strip("'")
                    if scalar:
                        categories = [scalar]
                index += 1
            else:
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
        # Prefer content/posts, but keep compatibility with legacy content/post.
        entries_by_name: dict[str, ArticleEntry] = {}

        for root in (POSTS_ROOT, LEGACY_POSTS_ROOT):
            try:
                response = await self.request_async("GET", f"contents/{root}")
            except requests.HTTPError as exc:
                response = exc.response
                if response is not None and response.status_code == 404:
                    continue
                raise

            payload = response.json()
            if isinstance(payload, dict):
                payload = [payload]

            for item in payload:
                if not isinstance(item, dict):
                    continue
                if item.get("type") != "file" or not str(item.get("name", "")).endswith(".md"):
                    continue

                name = str(item.get("name", ""))
                path = str(item.get("path", ""))
                sha = str(item.get("sha", ""))
                if not name or not path:
                    continue

                # posts has higher priority than legacy post for same filename.
                if name in entries_by_name and root == LEGACY_POSTS_ROOT:
                    continue
                entries_by_name[name] = ArticleEntry(name=name, path=path, sha=sha)

        return sorted(entries_by_name.values(), key=lambda entry: entry.name.lower())

    @staticmethod
    def canonicalize_post_path(path: str) -> str:
        normalized = (path or "").strip().replace("\\", "/")
        if normalized.startswith(f"{LEGACY_POSTS_ROOT}/"):
            return normalized.replace(f"{LEGACY_POSTS_ROOT}/", f"{POSTS_ROOT}/", 1)
        return normalized

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
        source_path: Optional[str] = None,
        source_sha: Optional[str] = None,
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

        # If this save is a legacy-path migration, delete the old legacy file.
        if source_path and source_sha and source_path != path:
            delete_body = {
                "message": f"Move {Path(source_path).name} to posts",
                "sha": source_sha,
                "branch": self.settings.github_branch or "main",
            }
            await self.request_async(
                "DELETE",
                f"contents/{quote(source_path, safe='/')}",
                json=delete_body,
            )

        return ArticleDraft(
            path=str(content_info.get("path", path)),
            title=title,
            categories=categories,
            body=body,
            sha=str(content_info.get("sha", sha or "")),
            date=date_value,
        )
