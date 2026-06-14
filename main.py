from __future__ import annotations

import asyncio
import base64
import os
import platform
import subprocess
from datetime import datetime
from pathlib import Path
from typing import Optional

import flet as ft
import requests

from github_service import ArticleDraft, ArticleEntry, GitHubContentClient, POSTS_ROOT, slugify, split_categories
from settings_store import AppSettings


ROUTE_ARTICLES = "/articles"
ROUTE_EDITOR = "/editor"
ROUTE_SETTINGS = "/settings"
ROUTE_HOME = "/home"
ROUTE_BY_INDEX = [ROUTE_HOME, ROUTE_ARTICLES, ROUTE_EDITOR, ROUTE_SETTINGS]


def _read_version_file() -> Optional[str]:
    version_file = Path(__file__).with_name("build_version.txt")
    if not version_file.exists():
        return None
    try:
        value = version_file.read_text(encoding="utf-8").strip()
    except OSError:
        return None
    if not value:
        return None
    return value.lstrip("v")


def _read_git_tag_version() -> Optional[str]:
    try:
        result = subprocess.run(
            ["git", "describe", "--tags", "--abbrev=0"],
            capture_output=True,
            text=True,
            check=True,
            timeout=1.5,
        )
    except (subprocess.SubprocessError, OSError):
        return None

    tag = (result.stdout or "").strip()
    if not tag:
        return None
    return tag.lstrip("v")


def resolve_app_version() -> str:
    env_version = (os.getenv("MOZU_APP_VERSION") or "").strip()
    if env_version:
        return env_version.lstrip("v")

    file_version = _read_version_file()
    if file_version:
        return file_version

    tag_version = _read_git_tag_version()
    if tag_version:
        return tag_version

    return "dev"


APP_VERSION = resolve_app_version()


class MozuMobileApp:
    """Route-driven mobile shell with bottom navigation and shared article state."""

    def __init__(self, page: ft.Page) -> None:
        self.page = page
        self.settings = AppSettings.load()
        self.client = GitHubContentClient(self.settings)
        self.entries: list[ArticleEntry] = []
        self.current_article: Optional[ArticleDraft] = None
        self.loading = False
        self._current_loading_overlay: Optional[ft.Container] = None
        self._editor_body_container: Optional[ft.Container] = None
        self._editor_scroll_view: Optional[ft.ListView] = None
        self._editor_card_container: Optional[ft.Container] = None
        self._home_icon_container: Optional[ft.Container] = None
        self._home_breathing_active = False

        # Article editor controls are kept on the controller so route switches can
        # repopulate them without recreating the underlying state model.
        self.article_list = ft.ListView(expand=True, spacing=8, padding=8, auto_scroll=False)
        self.title_field = ft.TextField(
            key="editor-title-field",
            label="标题",
            dense=True,
            border_radius=14,
            content_padding=12,
            on_focus=self._on_editor_input_focus,
        )
        self.category_field = ft.TextField(
            key="editor-category-field",
            label="分类",
            hint_text="多个分类用逗号分隔",
            dense=True,
            border_radius=14,
            content_padding=12,
            on_focus=self._on_editor_input_focus,
        )
        self.editor_field = ft.TextField(
            key="editor-body-field",
            multiline=True,
            expand=True,
            min_lines=18,
            max_lines=None,
            border_radius=18,
            text_size=14,
            on_change=self._on_editor_change,
            on_selection_change=self._on_editor_selection_change,
            on_focus=self._on_editor_input_focus,
            content_padding=16,
            hint_text="在这里编辑 Markdown 正文，发布时会自动拼接 YAML Front Matter。",
        )
        self.line_number_text = ft.Text(
            "1\n",
            size=13,
            color=ft.Colors.BLUE_GREY_400,
            text_align=ft.TextAlign.RIGHT,
            font_family="Consolas",
            selectable=False,
        )
        self.editor_toolbar = ft.ResponsiveRow(
            spacing=4,
            run_spacing=4,
            controls=[
                self._toolbar_svg_button(
                    src="assets/icons/toolbar/bold.svg",
                    tooltip="加粗",
                    on_click=lambda _e: self._insert_wrapped("**", "**"),
                ),
                self._toolbar_svg_button(
                    src="assets/icons/toolbar/italic.svg",
                    tooltip="斜体",
                    on_click=lambda _e: self._insert_wrapped("*", "*"),
                ),
                self._toolbar_svg_button(
                    src="assets/icons/toolbar/code_inline.svg",
                    tooltip="行内代码",
                    on_click=lambda _e: self._insert_wrapped("`", "`"),
                ),
                self._toolbar_svg_button(
                    src="assets/icons/toolbar/code_block.svg",
                    tooltip="Python 代码块",
                    on_click=lambda _e: self._insert_code_block("python"),
                ),
                self._toolbar_svg_button(
                    src="assets/icons/toolbar/table.svg",
                    tooltip="GFM 表格",
                    on_click=lambda _e: self._insert_table_template(),
                ),
                self._toolbar_svg_button(
                    src="assets/icons/toolbar/quote.svg",
                    tooltip="引用",
                    on_click=lambda _e: self._insert_prefix_line("> "),
                ),
                self._toolbar_svg_button(
                    src="assets/icons/toolbar/h1.svg",
                    tooltip="一级标题",
                    on_click=lambda _e: self._insert_prefix_line("# "),
                ),
                self._toolbar_svg_button(
                    src="assets/icons/toolbar/h2.svg",
                    tooltip="二级标题",
                    on_click=lambda _e: self._insert_prefix_line("## "),
                ),
                self._toolbar_svg_button(
                    src="assets/icons/toolbar/h3.svg",
                    tooltip="三级标题",
                    on_click=lambda _e: self._insert_prefix_line("### "),
                ),
                self._toolbar_svg_button(
                    src="assets/icons/toolbar/unordered_list.svg",
                    tooltip="无序列表",
                    on_click=lambda _e: self._insert_prefix_line("- "),
                ),
                self._toolbar_svg_button(
                    src="assets/icons/toolbar/task_list.svg",
                    tooltip="任务列表",
                    on_click=lambda _e: self._insert_prefix_line("- [ ] "),
                ),
            ],
        )

        # Settings page controls.
        self.token_field = ft.TextField(label="GitHub Token", password=True, can_reveal_password=True, dense=True)
        self.owner_field = ft.TextField(label="Repo Owner", dense=True)
        self.repo_field = ft.TextField(label="Repo Name", dense=True)
        self.branch_field = ft.TextField(label="Branch", dense=True)
        self.settings_hint = ft.Text("", size=12, color=ft.Colors.BLUE_GREY_500)

        # New article dialog controls.
        self.new_title_field = ft.TextField(label="新文章标题", autofocus=True, dense=True)
        self.new_category_field = ft.TextField(label="分类", hint_text="多个分类用逗号分隔", dense=True)
        self.page.dialog = ft.AlertDialog(
            modal=True,
            title=ft.Text("新建文章"),
            content=ft.Column(tight=True, spacing=12, controls=[self.new_title_field, self.new_category_field]),
            actions=[
                ft.TextButton("取消", on_click=self._close_new_article_dialog),
                ft.FilledButton(content="创建", on_click=self._create_new_article),
            ],
            actions_alignment=ft.MainAxisAlignment.END,
        )

        self._sync_settings_fields()
        self._configure_page()

    def _configure_page(self) -> None:
        self.page.title = "墨筑 MoZu"
        self.page.bgcolor = ft.Colors.BLUE_GREY_50
        self.page.theme_mode = ft.ThemeMode.LIGHT
        self.page.theme = ft.Theme(color_scheme_seed=ft.Colors.BLUE_700)
        self.page.padding = 0
        self.page.scroll = ft.ScrollMode.HIDDEN
        self._configure_soft_input_resize_mode()
        self.page.on_resized = self._on_page_resized
        self.page.on_route_change = self._on_route_change
        self.page.on_view_pop = self._on_view_pop
        self.page.go(ROUTE_HOME)

    def _configure_soft_input_resize_mode(self) -> None:
        """Prefer resize mode so Android IME does not cover input controls."""
        soft_input_mode = getattr(ft, "WindowSoftInputMode", None)
        if soft_input_mode is None:
            return

        resize_mode = getattr(soft_input_mode, "RESIZE", None)
        if resize_mode is None:
            return

        # Newer Flet exposes mode on page directly; older versions expose it on page.window.
        if hasattr(self.page, "window_soft_input_mode"):
            self.page.window_soft_input_mode = resize_mode

        window = getattr(self.page, "window", None)
        if window is not None and hasattr(window, "soft_input_mode"):
            window.soft_input_mode = resize_mode

    def _sync_settings_fields(self) -> None:
        self.token_field.value = self.settings.github_token
        self.owner_field.value = self.settings.repo_owner
        self.repo_field.value = self.settings.repo_name
        self.branch_field.value = self.settings.github_branch or "main"

    def _apply_settings_from_fields(self) -> None:
        self.settings = AppSettings(
            github_token=(self.token_field.value or "").strip(),
            repo_owner=(self.owner_field.value or "").strip(),
            repo_name=(self.repo_field.value or "").strip(),
            github_branch=(self.branch_field.value or "main").strip() or "main",
        )
        self.settings.apply_to_environment()
        self.settings.save()
        self.client.apply_settings(self.settings)

    def _set_loading(self, value: bool) -> None:
        self.loading = value
        if self._current_loading_overlay is not None:
            self._current_loading_overlay.visible = value
        self.page.update()

    def _show_snackbar(self, message: str, is_error: bool = False) -> None:
        self.page.snack_bar = ft.SnackBar(
            content=ft.Text(message),
            bgcolor=ft.Colors.RED_600 if is_error else ft.Colors.BLUE_700,
            behavior=ft.SnackBarBehavior.FLOATING,
        )
        self.page.snack_bar.open = True
        self.page.update()

    def _build_loading_overlay(self) -> ft.Container:
        overlay = ft.Container(
            visible=self.loading,
            expand=True,
            alignment=ft.Alignment.CENTER,
            bgcolor=ft.Colors.with_opacity(0.18, ft.Colors.BLUE_GREY_900),
            content=ft.Container(
                padding=24,
                border_radius=24,
                bgcolor=ft.Colors.with_opacity(0.95, ft.Colors.WHITE),
                shadow=ft.BoxShadow(
                    blur_radius=24,
                    spread_radius=1,
                    color=ft.Colors.with_opacity(0.12, ft.Colors.BLACK),
                    offset=ft.Offset(0, 6),
                ),
                content=ft.Column(
                    horizontal_alignment=ft.CrossAxisAlignment.CENTER,
                    tight=True,
                    spacing=12,
                    controls=[
                        ft.ProgressRing(),
                        ft.Text("正在处理 GitHub 请求", size=13, color=ft.Colors.BLUE_GREY_600),
                    ],
                ),
            ),
        )
        self._current_loading_overlay = overlay
        return overlay

    def _wrap_async(self, coroutine_func, *args):
        async def handler(_event):
            await coroutine_func(*args)

        return handler

    def _navigation_bar(self, selected_index: int) -> ft.NavigationBar:
        return ft.NavigationBar(
            selected_index=selected_index,
            destinations=[
                ft.NavigationBarDestination(
                    icon=ft.icons.Icons.HOME_OUTLINED,
                    selected_icon=ft.icons.Icons.HOME,
                    label="首页",
                ),
                ft.NavigationBarDestination(
                    icon=ft.icons.Icons.ARTICLE_ROUNDED,
                    selected_icon=ft.icons.Icons.LIST_ALT,
                    label="文章列表",
                ),
                ft.NavigationBarDestination(
                    icon=ft.icons.Icons.EDIT_NOTE_ROUNDED,
                    selected_icon=ft.icons.Icons.CREATE,
                    label="编辑器",
                ),
                ft.NavigationBarDestination(
                    icon=ft.icons.Icons.SETTINGS_INPUT_COMPONENT_ROUNDED,
                    selected_icon=ft.icons.Icons.SETTINGS,
                    label="同步设置",
                ),
            ],
            bgcolor=ft.Colors.WHITE,
            elevation=2,
            on_change=self._on_nav_change,
        )

    def _selected_index_for_route(self, route: str) -> int:
        if route == ROUTE_HOME:
            return 0
        if route == ROUTE_EDITOR:
            return 2
        if route == ROUTE_SETTINGS:
            return 3
        return 1

    def _build_view_shell(self, title: str, body: ft.Control, route: str) -> ft.View:
        # Each route renders a fully independent mobile page with a fixed bottom nav.
        selected_index = self._selected_index_for_route(route)
        return ft.View(
            route=route,
            bgcolor=ft.Colors.BLUE_GREY_50,
            padding=0,
            appbar=ft.AppBar(
                title=ft.Text(title),
                center_title=False,
                bgcolor=ft.Colors.WHITE,
                elevation=0,
                actions=self._appbar_actions_for_route(route),
            ),
            navigation_bar=self._navigation_bar(selected_index),
            controls=[ft.Stack([body, self._build_loading_overlay()], expand=True)],
        )

    def _appbar_actions_for_route(self, route: str) -> list[ft.Control]:
        if route == ROUTE_ARTICLES:
            return [
                ft.IconButton(
                    icon=ft.icons.Icons.REFRESH,
                    tooltip="同步/刷新",
                    on_click=self._wrap_async(self.refresh_articles),
                ),
                ft.IconButton(
                    icon=ft.icons.Icons.ADD,
                    tooltip="新建文章",
                    on_click=self._open_new_article_dialog,
                ),
            ]
        if route == ROUTE_HOME:
            return [
                ft.IconButton(
                    icon=ft.icons.Icons.REFRESH,
                    tooltip="刷新文章列表",
                    on_click=self._wrap_async(self.refresh_articles),
                ),
            ]
        if route == ROUTE_EDITOR:
            return [
                ft.IconButton(
                    icon=ft.icons.Icons.CLOUD_UPLOAD,
                    tooltip="一键同步发布",
                    on_click=self._wrap_async(self.publish_current_article),
                ),
            ]
        return [
            ft.IconButton(
                icon=ft.icons.Icons.SAVE,
                tooltip="保存设置",
                on_click=self._wrap_async(self.save_settings),
            )
        ]

    def _on_nav_change(self, event: ft.ControlEvent) -> None:
        self.page.go(ROUTE_BY_INDEX[event.control.selected_index])

    def _on_view_pop(self, _event: ft.ViewPopEvent) -> None:
        # Mobile back gestures should land on the article list, which is the app's home tab.
        self.page.go(ROUTE_HOME)

    def _home_timeflow_copy(self) -> dict[str, str]:
        now = datetime.now()
        minutes = now.hour * 60 + now.minute

        if 300 <= minutes < 510:  # 05:00 - 08:30
            return {
                "slot": "清晨启墨",
                "time_range": "05:00 - 08:30",
                "welcome": "晨光落墨，万象开篇",
                "quote": "海日生残夜，江春入旧年。",
                "author": "王湾",
                "mood": "破晓时分最适合开启新篇，夜尽日生的转换感与创作起笔的仪式感高度契合。",
            }
        if 510 <= minutes < 720:  # 08:30 - 12:00
            return {
                "slot": "上午淬文",
                "time_range": "08:30 - 12:00",
                "welcome": "趁光淬字，铸就锋芒",
                "quote": "盛年不重来，一日难再晨。",
                "author": "陶渊明",
                "mood": "上午是思维最清明的时段，强调时间不可复得，适合专注打磨每一行字。",
            }
        if 720 <= minutes < 840:  # 12:00 - 14:00
            return {
                "slot": "午间留白",
                "time_range": "12:00 - 14:00",
                "welcome": "留白半刻，字自回甘",
                "quote": "采菊东篱下，悠然见南山。",
                "author": "陶渊明",
                "mood": "中午宜短暂抽离与沉淀，让思绪回温，文字会更有层次。",
            }
        if 840 <= minutes < 1110:  # 14:00 - 18:30
            return {
                "slot": "午后铸句",
                "time_range": "14:00 - 18:30",
                "welcome": "静水深流，慢火铸文",
                "quote": "纸上得来终觉浅，绝知此事要躬行。",
                "author": "陆游",
                "mood": "午后适合深加工与结构推敲，把灵感落实为可发布的完整表达。",
            }
        if 1110 <= minutes < 1380:  # 18:30 - 23:00
            return {
                "slot": "夜色成章",
                "time_range": "18:30 - 23:00",
                "welcome": "灯下成章，灵感正浓",
                "quote": "文章千古事，得失寸心知。",
                "author": "杜甫",
                "mood": "夜晚情绪与思辨并行，适合进入高产状态，同时保持文本敬畏。",
            }

        # 23:00 - 05:00
        return {
            "slot": "子夜求索",
            "time_range": "23:00 - 05:00",
            "welcome": "孤灯砺思，星河为证",
            "quote": "路漫漫其修远兮，吾将上下而求索。",
            "author": "屈原",
            "mood": "深夜是守夜写作者与自我对话的时段，最适合硬核思考与持续求索。",
        }

    async def _run_home_icon_breathing(self) -> None:
        if self._home_breathing_active:
            return
        self._home_breathing_active = True
        scale_up = True
        try:
            while self.page.route == ROUTE_HOME and self._home_icon_container is not None:
                self._home_icon_container.scale = 1.04 if scale_up else 1.0
                self.page.update()
                scale_up = not scale_up
                await asyncio.sleep(1.25)
        finally:
            self._home_breathing_active = False

    def _build_home_icon_container(self) -> ft.Container:
        self._home_icon_container = ft.Container(
            width=54,
            height=54,
            border_radius=14,
            bgcolor=ft.Colors.with_opacity(0.2, ft.Colors.WHITE),
            padding=8,
            scale=1.0,
            animate_scale=ft.Animation(1100, "easeInOut"),
            content=ft.Image(src="assets/icons/mozu.svg", fit="contain"),
        )
        return self._home_icon_container

    def _sync_home_animated_state(self) -> None:
        if self.page.route == ROUTE_HOME and self._home_icon_container is not None:
            self.page.run_task(self._run_home_icon_breathing)
            return
        self._home_breathing_active = False

    def _build_home_body(self) -> ft.Control:
        flet_version = getattr(ft, "__version__", "unknown")
        python_version = platform.python_version()
        repo_name = f"{self.settings.repo_owner}/{self.settings.repo_name}".strip("/") or "未配置"
        timeflow = self._home_timeflow_copy()
        primary = ft.Colors.BLUE_700
        primary_soft = ft.Colors.BLUE_600

        def info_row(label: str, value: str) -> ft.Row:
            return ft.Row(
                alignment=ft.MainAxisAlignment.SPACE_BETWEEN,
                vertical_alignment=ft.CrossAxisAlignment.CENTER,
                controls=[
                    ft.Text(label, size=13, weight=ft.FontWeight.W_600, color=ft.Colors.BLUE_GREY_700),
                    ft.Text(value, size=13, color=ft.Colors.BLUE_GREY_900, max_lines=1, overflow=ft.TextOverflow.ELLIPSIS),
                ],
            )

        return ft.Container(
            expand=True,
            padding=16,
            content=ft.ListView(
                expand=True,
                spacing=12,
                auto_scroll=False,
                controls=[
                    ft.Container(
                        padding=20,
                        border_radius=24,
                        gradient=ft.LinearGradient(
                            begin=ft.Alignment(-1, -1),
                            end=ft.Alignment(1, 1),
                            colors=[primary, primary_soft],
                        ),
                        shadow=ft.BoxShadow(
                            blur_radius=22,
                            spread_radius=0,
                            color=ft.Colors.with_opacity(0.25, primary),
                            offset=ft.Offset(0, 8),
                        ),
                        content=ft.Column(
                            tight=True,
                            spacing=12,
                            controls=[
                                ft.Row(
                                    alignment=ft.MainAxisAlignment.START,
                                    vertical_alignment=ft.CrossAxisAlignment.CENTER,
                                    spacing=12,
                                    controls=[
                                        self._build_home_icon_container(),
                                        ft.Column(
                                            tight=True,
                                            spacing=2,
                                            controls=[
                                                ft.Text("墨筑 MoZu", size=24, weight=ft.FontWeight.BOLD, color=ft.Colors.WHITE),
                                                ft.Text("移动端 Hugo 博客管理工具", size=13, color=ft.Colors.with_opacity(0.9, ft.Colors.WHITE)),
                                            ],
                                        ),
                                    ],
                                ),
                                ft.Text(timeflow["welcome"], size=15, weight=ft.FontWeight.W_600, color=ft.Colors.with_opacity(0.98, ft.Colors.WHITE)),
                                ft.Text(
                                    f"{timeflow['slot']} · {timeflow['time_range']}",
                                    size=12,
                                    color=ft.Colors.with_opacity(0.92, ft.Colors.WHITE),
                                ),
                                ft.Row(
                                    spacing=8,
                                    controls=[
                                        ft.Container(
                                            border_radius=999,
                                            padding=ft.Padding(left=10, top=4, right=10, bottom=4),
                                            bgcolor=ft.Colors.with_opacity(0.18, ft.Colors.WHITE),
                                            content=ft.Text(f"v{APP_VERSION}", size=12, weight=ft.FontWeight.W_600, color=ft.Colors.WHITE),
                                        ),
                                        ft.Container(
                                            border_radius=999,
                                            padding=ft.Padding(left=10, top=4, right=10, bottom=4),
                                            bgcolor=ft.Colors.with_opacity(0.18, ft.Colors.WHITE),
                                            content=ft.Text("Hugo + GitHub API", size=12, color=ft.Colors.WHITE),
                                        ),
                                    ],
                                ),
                            ],
                        ),
                    ),
                    ft.Container(
                        padding=16,
                        border_radius=18,
                        bgcolor=ft.Colors.WHITE,
                        shadow=ft.BoxShadow(
                            blur_radius=14,
                            spread_radius=0,
                            color=ft.Colors.with_opacity(0.08, ft.Colors.BLUE_GREY_900),
                            offset=ft.Offset(0, 4),
                        ),
                        content=ft.Column(
                            tight=True,
                            spacing=10,
                            controls=[
                                ft.Text("时间流摘句", size=16, weight=ft.FontWeight.BOLD, color=ft.Colors.BLUE_GREY_900),
                                ft.Text(
                                    f"{timeflow['quote']} —— {timeflow['author']}",
                                    size=14,
                                    weight=ft.FontWeight.W_600,
                                    color=primary,
                                ),
                                ft.Text(timeflow["mood"], size=12, color=ft.Colors.BLUE_GREY_600),
                                ft.Divider(height=1),
                                ft.Text("应用信息", size=16, weight=ft.FontWeight.BOLD, color=ft.Colors.BLUE_GREY_900),
                                ft.Divider(height=1),
                                info_row("应用版本", APP_VERSION),
                                info_row("UI 框架", f"Flet {flet_version}"),
                                info_row("Python", python_version),
                                info_row("目标仓库", repo_name),
                                info_row("默认分支", self.settings.github_branch or "main"),
                            ],
                        ),
                    ),
                    ft.Container(
                        padding=16,
                        border_radius=18,
                        bgcolor=ft.Colors.WHITE,
                        shadow=ft.BoxShadow(
                            blur_radius=14,
                            spread_radius=0,
                            color=ft.Colors.with_opacity(0.08, ft.Colors.BLUE_GREY_900),
                            offset=ft.Offset(0, 4),
                        ),
                        content=ft.Column(
                            tight=True,
                            spacing=10,
                            controls=[
                                ft.Text("快速入口", size=16, weight=ft.FontWeight.BOLD, color=ft.Colors.BLUE_GREY_900),
                                ft.ResponsiveRow(
                                    spacing=10,
                                    run_spacing=10,
                                    controls=[
                                        ft.Container(
                                            col={"xs": 12, "sm": 6},
                                            content=ft.FilledButton(
                                                content=ft.Text("进入文章列表"),
                                                icon=ft.icons.Icons.LIST_ALT,
                                                style=ft.ButtonStyle(
                                                    bgcolor=primary,
                                                    color=ft.Colors.WHITE,
                                                    shape=ft.RoundedRectangleBorder(radius=12),
                                                ),
                                                on_click=lambda _e: self.page.go(ROUTE_ARTICLES),
                                            ),
                                        ),
                                        ft.Container(
                                            col={"xs": 12, "sm": 6},
                                            content=ft.OutlinedButton(
                                                content=ft.Text("打开编辑器"),
                                                icon=ft.icons.Icons.EDIT_NOTE,
                                                style=ft.ButtonStyle(
                                                    side=ft.BorderSide(1.2, primary),
                                                    color=primary,
                                                    shape=ft.RoundedRectangleBorder(radius=12),
                                                ),
                                                on_click=lambda _e: self.page.go(ROUTE_EDITOR),
                                            ),
                                        ),
                                    ],
                                ),
                                ft.Text("支持直接在手机端编辑 Markdown 并发布到 GitHub 仓库。", size=13, color=ft.Colors.BLUE_GREY_600),
                            ],
                        ),
                    ),
                ],
            ),
        )

    def _build_article_card(self) -> ft.Container:
        return ft.Container(
            padding=16,
            border_radius=18,
            bgcolor=ft.Colors.WHITE,
            shadow=ft.BoxShadow(
                blur_radius=16,
                spread_radius=0,
                color=ft.Colors.with_opacity(0.08, ft.Colors.BLUE_GREY_900),
                offset=ft.Offset(0, 4),
            ),
            content=ft.Column(
                tight=True,
                spacing=10,
                controls=[
                    ft.Text("点击文章后，系统会自动下载、解码并跳转到编辑器。", size=12, color=ft.Colors.BLUE_GREY_600),
                    ft.Row(
                        tight=True,
                        spacing=8,
                        controls=[
                            ft.FilledTonalButton(
                                "同步/刷新",
                                icon=ft.icons.Icons.REFRESH,
                                on_click=self._wrap_async(self.refresh_articles),
                            ),
                            ft.OutlinedButton(
                                "新建文章",
                                icon=ft.icons.Icons.ADD,
                                on_click=self._open_new_article_dialog,
                            ),
                        ],
                    ),
                    ft.Divider(height=1),
                    self.article_list,
                ],
            ),
        )

    def _build_articles_body(self) -> ft.Control:
        return ft.Container(
            expand=True,
            padding=14,
            content=ft.Column(
                expand=True,
                spacing=14,
                controls=[
                    ft.Container(
                        padding=18,
                        border_radius=20,
                        bgcolor=ft.Colors.WHITE,
                        content=ft.Column(
                            tight=True,
                            spacing=6,
                            controls=[
                                ft.Text("文章列表", size=18, weight=ft.FontWeight.BOLD, color=ft.Colors.BLUE_GREY_900),
                                ft.Text(
                                    "从 GitHub REST API 读取 content/posts 目录并展示 Markdown 文件。",
                                    size=12,
                                    color=ft.Colors.BLUE_GREY_500,
                                ),
                                ft.Text(f"当前仓库：{self.settings.repo_owner}/{self.settings.repo_name}", size=12),
                            ],
                        ),
                    ),
                    self._build_article_card(),
                ],
            ),
        )

    def _build_editor_body(self) -> ft.Control:
        compact = self._is_compact_layout()
        section_padding = 12 if compact else 18
        body_padding = 8 if compact else 14
        card_padding = 12 if compact else 16
        line_gutter_width = 36 if compact else 44
        keyboard_inset_bottom = self._keyboard_inset_bottom()
        editor_card_height = self._editor_card_height(keyboard_inset_bottom)

        self._editor_card_container = ft.Container(
            height=editor_card_height,
            padding=card_padding,
            border_radius=20,
            bgcolor=ft.Colors.WHITE,
            content=ft.Column(
                expand=True,
                spacing=12,
                controls=[
                    ft.ResponsiveRow(
                        spacing=12,
                        run_spacing=12,
                        controls=[
                            ft.Container(col={"sm": 12, "md": 6}, content=self.title_field),
                            ft.Container(col={"sm": 12, "md": 6}, content=self.category_field),
                        ],
                    ),
                    self.editor_toolbar,
                    ft.Container(
                        expand=True,
                        border_radius=16,
                        border=self._editor_outline_border(),
                        content=ft.Row(
                            expand=True,
                            spacing=0,
                            controls=[
                                ft.Container(
                                    width=line_gutter_width,
                                    expand=False,
                                    padding=ft.Padding(top=14, right=4),
                                    bgcolor=ft.Colors.BLUE_GREY_50,
                                    alignment=ft.Alignment.TOP_RIGHT,
                                    content=self.line_number_text,
                                ),
                                ft.VerticalDivider(width=1, color=ft.Colors.BLUE_GREY_100),
                                ft.Container(expand=True, content=self.editor_field),
                            ],
                        ),
                    ),
                ],
            ),
        )

        self._editor_scroll_view = ft.ListView(
            expand=True,
            spacing=10 if compact else 14,
            padding=0,
            auto_scroll=False,
            controls=[
                ft.Container(
                    key="editor-header-card",
                    padding=section_padding,
                    border_radius=20,
                    bgcolor=ft.Colors.WHITE,
                    content=ft.Column(
                        tight=True,
                        spacing=6,
                        controls=[
                            ft.Text("在线编辑器", size=18, weight=ft.FontWeight.BOLD, color=ft.Colors.BLUE_GREY_900),
                            ft.Text(
                                "标题、分类和正文会在发布时自动拼接为 YAML Front Matter + Markdown。",
                                size=12,
                                color=ft.Colors.BLUE_GREY_500,
                            ),
                            ft.Text(
                                f"当前文件：{self.current_article.path if self.current_article else '未选择'}",
                                size=12,
                                color=ft.Colors.BLUE_GREY_600,
                            ),
                        ],
                    ),
                ),
                self._editor_card_container,
            ],
        )

        self._editor_body_container = ft.Container(
            expand=True,
            padding=ft.Padding(left=body_padding, top=body_padding, right=body_padding, bottom=body_padding + keyboard_inset_bottom),
            content=self._editor_scroll_view,
        )
        return self._editor_body_container

    def _on_page_resized(self, _event: ft.ControlEvent) -> None:
        self._update_editor_keyboard_padding(update_page=True)

    def _on_editor_input_focus(self, event: ft.ControlEvent) -> None:
        control_key = getattr(event.control, "key", None)
        self.page.run_task(self._deferred_scroll_to_control, control_key)

    async def _deferred_scroll_to_control(self, control_key: Optional[str]) -> None:
        # Wait a bit for IME animation, then ensure focused field is visible.
        await asyncio.sleep(0.2)
        self._update_editor_keyboard_padding(update_page=False)
        if self._editor_scroll_view is None:
            self.page.update()
            return

        if control_key in ("editor-title-field", "editor-category-field"):
            self.page.scroll_to(key=control_key, duration=260)
            self._editor_scroll_view.scroll_to(offset=0, duration=200)
        elif control_key == "editor-body-field":
            self._scroll_editor_to_cursor_line(duration=220)
        self.page.update()

    def _editor_base_padding(self) -> int:
        return 8 if self._is_compact_layout() else 14

    def _update_editor_keyboard_padding(self, *, update_page: bool) -> None:
        if self.page.route != ROUTE_EDITOR or self._editor_body_container is None:
            return

        base_padding = self._editor_base_padding()
        keyboard_inset_bottom = self._keyboard_inset_bottom()
        self._editor_body_container.padding = ft.Padding(
            left=base_padding,
            top=base_padding,
            right=base_padding,
            bottom=base_padding + keyboard_inset_bottom,
        )
        if self._editor_card_container is not None:
            self._editor_card_container.height = self._editor_card_height(keyboard_inset_bottom)
        if update_page:
            self.page.update()

    def _editor_card_height(self, keyboard_inset_bottom: int) -> int:
        page_height = int(self.page.height or 760)
        reserve_height = 250 + max(0, int(keyboard_inset_bottom * 0.35))
        return max(320, page_height - reserve_height)

    def _cursor_line_index(self) -> int:
        value = self.editor_field.value or ""
        start, _end = self._get_selection_range()
        return max(0, value.count("\n", 0, start))

    def _scroll_editor_to_cursor_line(self, duration: int = 0) -> None:
        if self._editor_scroll_view is None or self.page.route != ROUTE_EDITOR:
            return

        line_index = self._cursor_line_index()
        line_height = 22
        base_offset = 220
        target_offset = max(0, base_offset + (line_index * line_height) - 120)
        self._editor_scroll_view.scroll_to(offset=target_offset, duration=duration)

    def _keyboard_inset_bottom(self) -> int:
        media = getattr(self.page, "media", None)
        if media is None:
            return 0

        view_insets = getattr(media, "view_insets", None)
        if view_insets is None:
            return 0

        raw_bottom = getattr(view_insets, "bottom", 0)
        try:
            return max(0, int(raw_bottom or 0))
        except (TypeError, ValueError):
            return 0

    def _toolbar_icon_button(self, *, icon, tooltip: str, on_click) -> ft.Container:
        return ft.Container(
            col={"xs": 2, "sm": 2, "md": 1},
            alignment=ft.Alignment.CENTER,
            content=ft.IconButton(icon=icon, tooltip=tooltip, on_click=on_click),
        )

    def _toolbar_svg_button(self, *, src: str, tooltip: str, on_click) -> ft.Container:
        button = ft.Container(
            col={"xs": 2, "sm": 2, "md": 1},
            alignment=ft.Alignment.CENTER,
            tooltip=tooltip,
            on_click=on_click,
            on_hover=self._on_toolbar_svg_hover,
            border_radius=10,
            padding=8,
            bgcolor=ft.Colors.TRANSPARENT,
            animate=ft.Animation(120, "easeInOut"),
            content=ft.Image(
                src=src,
                width=22,
                height=22,
                fit="contain",
            ),
        )
        return button

    def _on_toolbar_svg_hover(self, event: ft.ControlEvent) -> None:
        is_hover = str(getattr(event, "data", "")).lower() == "true"
        event.control.bgcolor = ft.Colors.with_opacity(0.1, ft.Colors.BLUE_700) if is_hover else ft.Colors.TRANSPARENT
        event.control.update()

    def _is_compact_layout(self) -> bool:
        width = self.page.width or 0
        return width == 0 or width < 520

    def _editor_outline_border(self) -> ft.Border:
        side = ft.BorderSide(1, ft.Colors.BLUE_GREY_100)
        return ft.Border(top=side, right=side, bottom=side, left=side)

    def _on_editor_change(self, _event: ft.ControlEvent) -> None:
        self._refresh_editor_metrics(update_page=True)

    def _on_editor_selection_change(self, _event: ft.ControlEvent) -> None:
        self._scroll_editor_to_cursor_line(duration=120)

    def _refresh_editor_metrics(self, update_page: bool) -> None:
        value = self.editor_field.value or ""
        line_count = max(1, value.count("\n") + 1)
        self.line_number_text.value = "\n".join(str(i) for i in range(1, line_count + 1)) + "\n"
        if update_page:
            self.page.update()

    def _get_selection_range(self) -> tuple[int, int]:
        value = self.editor_field.value or ""
        default_cursor = len(value)
        selection = self.editor_field.selection
        if selection is None or not selection.is_valid:
            return default_cursor, default_cursor

        raw_start = getattr(selection, "start", getattr(selection, "base_offset", default_cursor))
        raw_end = getattr(selection, "end", getattr(selection, "extent_offset", default_cursor))
        start = max(0, min(raw_start, len(value)))
        end = max(0, min(raw_end, len(value)))
        if end < start:
            start, end = end, start
        return start, end

    def _set_editor_value_and_cursor(self, new_value: str, cursor: Optional[int] = None) -> None:
        self.editor_field.value = new_value
        if cursor is None:
            cursor = len(new_value)
        cursor = max(0, min(cursor, len(new_value)))
        self.editor_field.selection = ft.TextSelection(base_offset=cursor, extent_offset=cursor)
        self._refresh_editor_metrics(update_page=True)
        self.editor_field.focus()

    def _insert_wrapped(self, prefix: str, suffix: str) -> None:
        value = self.editor_field.value or ""
        start, end = self._get_selection_range()
        selected = value[start:end]

        if selected:
            new_value = f"{value[:start]}{prefix}{selected}{suffix}{value[end:]}"
            cursor = end + len(prefix) + len(suffix)
        else:
            insertion = f"{prefix}{suffix}"
            new_value = f"{value[:start]}{insertion}{value[end:]}"
            cursor = start + len(prefix)

        self._set_editor_value_and_cursor(new_value, cursor)

    def _insert_text_at_cursor(self, insertion: str) -> None:
        value = self.editor_field.value or ""
        start, end = self._get_selection_range()
        new_value = f"{value[:start]}{insertion}{value[end:]}"
        cursor = start + len(insertion)
        self._set_editor_value_and_cursor(new_value, cursor)

    def _insert_code_block(self, language: str) -> None:
        value = self.editor_field.value or ""
        start, end = self._get_selection_range()
        selected = value[start:end]
        head_break = "\n" if start > 0 and value[start - 1] != "\n" else ""
        tail_break = "\n" if end < len(value) and value[end:end + 1] != "\n" else ""
        body = selected if selected else ""
        insertion = f"{head_break}```{language}\n{body}\n```{tail_break}"
        new_value = f"{value[:start]}{insertion}{value[end:]}"
        cursor = start + len(insertion)
        if not selected:
            cursor = start + len(head_break) + len(f"```{language}\n")
        self._set_editor_value_and_cursor(new_value, cursor)

    def _insert_table_template(self) -> None:
        table_template = "| 列1 | 列2 | 列3 |\n| --- | --- | --- |\n|  |  |  |\n|  |  |  |\n"
        self._insert_text_at_cursor(table_template)

    def _insert_prefix_line(self, prefix: str) -> None:
        value = self.editor_field.value or ""
        start, end = self._get_selection_range()
        line_start = value.rfind("\n", 0, start)
        line_start = 0 if line_start < 0 else line_start + 1
        new_value = f"{value[:line_start]}{prefix}{value[line_start:]}"
        cursor = end + len(prefix)
        self._set_editor_value_and_cursor(new_value, cursor)

    def _build_settings_body(self) -> ft.Control:
        return ft.Container(
            expand=True,
            padding=14,
            content=ft.Column(
                expand=True,
                spacing=14,
                controls=[
                    ft.Container(
                        padding=18,
                        border_radius=20,
                        bgcolor=ft.Colors.WHITE,
                        content=ft.Column(
                            tight=True,
                            spacing=6,
                            controls=[
                                ft.Text("同步设置", size=18, weight=ft.FontWeight.BOLD, color=ft.Colors.BLUE_GREY_900),
                                ft.Text(
                                    "在这里配置 GitHub Token、仓库信息和默认分支。保存后会立即刷新 GitHub 客户端。",
                                    size=12,
                                    color=ft.Colors.BLUE_GREY_500,
                                ),
                            ],
                        ),
                    ),
                    ft.Container(
                        padding=16,
                        border_radius=20,
                        bgcolor=ft.Colors.WHITE,
                        content=ft.Column(
                            tight=True,
                            spacing=12,
                            controls=[
                                self.token_field,
                                self.owner_field,
                                self.repo_field,
                                self.branch_field,
                                ft.Row(
                                    alignment=ft.MainAxisAlignment.END,
                                    controls=[
                                        ft.FilledButton(content="保存设置", icon=ft.icons.Icons.SAVE, on_click=self._wrap_async(self.save_settings)),
                                    ],
                                ),
                                self.settings_hint,
                            ],
                        ),
                    ),
                ],
            ),
        )

    def _build_view(self) -> ft.View:
        route = self.page.route or ROUTE_HOME
        if route == ROUTE_HOME:
            return self._build_view_shell("首页", self._build_home_body(), ROUTE_HOME)
        if route == ROUTE_EDITOR:
            return self._build_view_shell("在线编辑器", self._build_editor_body(), ROUTE_EDITOR)
        if route == ROUTE_SETTINGS:
            return self._build_view_shell("同步设置", self._build_settings_body(), ROUTE_SETTINGS)
        return self._build_view_shell("文章列表", self._build_articles_body(), ROUTE_ARTICLES)

    def _on_route_change(self, _event: ft.RouteChangeEvent) -> None:
        self.page.views.clear()
        self.page.views.append(self._build_view())
        self._sync_home_animated_state()
        self.page.update()

    async def refresh_articles(self) -> None:
        self._set_loading(True)
        try:
            self.entries = await self.client.list_articles()
            if self._selected_index_for_route(self.page.route or ROUTE_ARTICLES) == 1:
                self._refresh_article_list()
            self._show_snackbar(f"已同步 {len(self.entries)} 篇文章")
        except requests.HTTPError as exc:
            response = exc.response
            if response is not None and response.status_code == 403:
                self._show_snackbar("GitHub 返回 403：Token 权限不足、仓库权限受限或触发了 API 限流。", True)
            else:
                self._show_snackbar(f"同步失败：{response.status_code if response else '未知'}", True)
        except requests.RequestException as exc:
            self._show_snackbar(f"网络异常：{exc}", True)
        except Exception as exc:  # noqa: BLE001
            self._show_snackbar(f"同步失败：{exc}", True)
        finally:
            self._set_loading(False)

    def _refresh_article_list(self) -> None:
        self.article_list.controls.clear()
        if not self.entries:
            self.article_list.controls.append(
                ft.Container(
                    padding=16,
                    border_radius=16,
                    bgcolor=ft.Colors.BLUE_GREY_50,
                    content=ft.Text("当前仓库里没有找到 Markdown 文章。", color=ft.Colors.BLUE_GREY_500),
                )
            )
        else:
            for entry in self.entries:
                is_selected = self.current_article is not None and entry.path == self.current_article.path
                self.article_list.controls.append(
                    ft.Container(
                        border_radius=16,
                        bgcolor=ft.Colors.BLUE_50 if is_selected else ft.Colors.BLUE_GREY_50,
                        padding=6,
                        content=ft.ListTile(
                            dense=True,
                            selected=is_selected,
                            leading=ft.Icon(ft.icons.Icons.DESCRIPTION, color=ft.Colors.BLUE_700),
                            title=ft.Text(entry.name, max_lines=1, overflow=ft.TextOverflow.ELLIPSIS),
                            subtitle=ft.Text(entry.path, max_lines=1, overflow=ft.TextOverflow.ELLIPSIS),
                            on_click=self._wrap_async(self.open_article, entry.path),
                        ),
                    )
                )
        self.page.update()

    async def open_article(self, path: str) -> None:
        self._set_loading(True)
        try:
            draft = await self.client.get_article(path)
            self.current_article = draft
            self.title_field.value = draft.title
            self.category_field.value = ", ".join(draft.categories)
            self.editor_field.value = draft.body
            self._refresh_editor_metrics(update_page=False)
            self.page.go(ROUTE_EDITOR)
            self._show_snackbar("文章已载入到编辑器")
        except requests.HTTPError as exc:
            response = exc.response
            if response is not None and response.status_code == 403:
                self._show_snackbar("GitHub 返回 403：没有权限读取该文章或触发了限流。", True)
            elif response is not None and response.status_code == 404:
                self._show_snackbar("没有找到这篇文章。", True)
            else:
                self._show_snackbar(f"读取失败：{response.status_code if response else '未知'}", True)
        except (ValueError, UnicodeDecodeError, base64.binascii.Error) as exc:
            self._show_snackbar(f"解码失败：{exc}", True)
        except requests.RequestException as exc:
            self._show_snackbar(f"网络异常：{exc}", True)
        except Exception as exc:  # noqa: BLE001
            self._show_snackbar(f"读取失败：{exc}", True)
        finally:
            self._set_loading(False)

    def _open_new_article_dialog(self, _event=None) -> None:
        self.new_title_field.value = ""
        self.new_category_field.value = ""
        self.page.dialog.open = True
        self.page.update()

    def _close_new_article_dialog(self, _event=None) -> None:
        self.page.dialog.open = False
        self.page.update()

    async def _create_new_article(self, _event=None) -> None:
        title = (self.new_title_field.value or "").strip()
        category_text = (self.new_category_field.value or "").strip()
        if not title:
            self._show_snackbar("请先输入标题。", True)
            return

        categories = split_categories(category_text)
        base_name = slugify(title) or f"post-{datetime.now().strftime('%Y%m%d-%H%M%S')}"
        existing_paths = {entry.path for entry in self.entries}
        filename = f"{base_name}.md"
        if f"{POSTS_ROOT}/{filename}" in existing_paths:
            filename = f"{base_name}-{datetime.now().strftime('%Y%m%d-%H%M%S')}.md"

        self.current_article = ArticleDraft(
            path=self.client.canonicalize_post_path(f"{POSTS_ROOT}/{filename}"),
            title=title,
            categories=categories,
            body="",
            sha="",
            date=datetime.now().astimezone().isoformat(timespec="seconds"),
        )
        self.title_field.value = title
        self.category_field.value = ", ".join(categories)
        self.editor_field.value = ""
        self._refresh_editor_metrics(update_page=False)
        self.page.dialog.open = False
        self.page.go(ROUTE_EDITOR)
        self._show_snackbar("新文章草稿已创建，可以开始编辑正文。")

    async def publish_current_article(self) -> None:
        title = (self.title_field.value or "").strip()
        if not title:
            self._show_snackbar("标题不能为空，无法发布。", True)
            return

        categories = split_categories(self.category_field.value or "")
        body = self.editor_field.value or ""
        if self.current_article is None:
            slug = slugify(title) or f"post-{datetime.now().strftime('%Y%m%d-%H%M%S')}"
            self.current_article = ArticleDraft(
                path=self.client.canonicalize_post_path(f"{POSTS_ROOT}/{slug}.md"),
                title=title,
                categories=categories,
                body=body,
                sha="",
                date=datetime.now().astimezone().isoformat(timespec="seconds"),
            )

        self._set_loading(True)
        try:
            source_path = self.current_article.path
            canonical_path = self.client.canonicalize_post_path(source_path)

            saved = await self.client.save_article(
                path=canonical_path,
                title=title,
                categories=categories,
                body=body,
                sha=self.current_article.sha,
                date_value=self.current_article.date,
                source_path=source_path if source_path != canonical_path else None,
                source_sha=self.current_article.sha if source_path != canonical_path else None,
            )
            self.current_article = saved
            self._show_snackbar("发布成功")
            await self.refresh_articles()
            self.page.go(ROUTE_EDITOR)
        except requests.HTTPError as exc:
            response = exc.response
            if response is not None and response.status_code == 403:
                self._show_snackbar("GitHub 返回 403：Token 权限不足、仓库保护规则拦截或触发限流。", True)
            elif response is not None and response.status_code == 409:
                self._show_snackbar("发布失败：远端文件发生冲突，请先刷新后再试。", True)
            else:
                self._show_snackbar(f"发布失败：{response.status_code if response else '未知'}", True)
        except requests.RequestException as exc:
            self._show_snackbar(f"网络异常：{exc}", True)
        except Exception as exc:  # noqa: BLE001
            self._show_snackbar(f"发布失败：{exc}", True)
        finally:
            self._set_loading(False)

    async def save_settings(self) -> None:
        self._set_loading(True)
        try:
            self._apply_settings_from_fields()
            self.settings_hint.value = "设置已保存，GitHub 客户端已同步刷新。"
            self._show_snackbar("设置已保存")
        except Exception as exc:  # noqa: BLE001
            self._show_snackbar(f"保存设置失败：{exc}", True)
        finally:
            self._set_loading(False)


async def main(page: ft.Page) -> None:
    app = MozuMobileApp(page)
    await app.refresh_articles()


if __name__ == "__main__":
    ft.app(target=main)
