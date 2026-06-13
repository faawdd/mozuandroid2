from __future__ import annotations

import asyncio
import base64
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
ROUTE_BY_INDEX = [ROUTE_ARTICLES, ROUTE_EDITOR, ROUTE_SETTINGS]


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

        # Article editor controls are kept on the controller so route switches can
        # repopulate them without recreating the underlying state model.
        self.article_list = ft.ListView(expand=True, spacing=8, padding=8, auto_scroll=False)
        self.title_field = ft.TextField(label="标题", dense=True, border_radius=14, content_padding=12)
        self.category_field = ft.TextField(
            label="分类",
            hint_text="多个分类用逗号分隔",
            dense=True,
            border_radius=14,
            content_padding=12,
        )
        self.editor_field = ft.TextField(
            multiline=True,
            expand=True,
            min_lines=18,
            max_lines=None,
            border_radius=18,
            text_size=14,
            on_change=self._on_editor_change,
            on_selection_change=self._on_editor_selection_change,
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
                self._toolbar_icon_button(
                    icon=ft.icons.Icons.FORMAT_BOLD,
                    tooltip="加粗",
                    on_click=lambda _e: self._insert_wrapped("**", "**"),
                ),
                self._toolbar_icon_button(
                    icon=ft.icons.Icons.FORMAT_ITALIC,
                    tooltip="斜体",
                    on_click=lambda _e: self._insert_wrapped("*", "*"),
                ),
                self._toolbar_icon_button(
                    icon=ft.icons.Icons.CODE,
                    tooltip="行内代码",
                    on_click=lambda _e: self._insert_wrapped("`", "`"),
                ),
                self._toolbar_icon_button(
                    icon=ft.icons.Icons.DATA_OBJECT,
                    tooltip="Python 代码块",
                    on_click=lambda _e: self._insert_code_block("python"),
                ),
                self._toolbar_icon_button(
                    icon=ft.icons.Icons.TABLE_CHART,
                    tooltip="GFM 表格",
                    on_click=lambda _e: self._insert_table_template(),
                ),
                self._toolbar_icon_button(
                    icon=ft.icons.Icons.FORMAT_QUOTE,
                    tooltip="引用",
                    on_click=lambda _e: self._insert_prefix_line("> "),
                ),
                self._toolbar_icon_button(
                    icon=ft.icons.Icons.FILTER_1,
                    tooltip="一级标题",
                    on_click=lambda _e: self._insert_prefix_line("# "),
                ),
                self._toolbar_icon_button(
                    icon=ft.icons.Icons.FILTER_2,
                    tooltip="二级标题",
                    on_click=lambda _e: self._insert_prefix_line("## "),
                ),
                self._toolbar_icon_button(
                    icon=ft.icons.Icons.FILTER_3,
                    tooltip="三级标题",
                    on_click=lambda _e: self._insert_prefix_line("### "),
                ),
                self._toolbar_icon_button(
                    icon=ft.icons.Icons.FORMAT_LIST_BULLETED,
                    tooltip="无序列表",
                    on_click=lambda _e: self._insert_prefix_line("- "),
                ),
                self._toolbar_icon_button(
                    icon=ft.icons.Icons.CHECK_BOX,
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
        self.page.scroll = ft.ScrollMode.AUTO
        self._configure_soft_input_resize_mode()
        self.page.on_route_change = self._on_route_change
        self.page.on_view_pop = self._on_view_pop
        self.page.go(ROUTE_ARTICLES)

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
        if route == ROUTE_EDITOR:
            return 1
        if route == ROUTE_SETTINGS:
            return 2
        return 0

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
        self.page.go(ROUTE_ARTICLES)

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

        return ft.Container(
            expand=True,
            padding=ft.Padding(left=body_padding, top=body_padding, right=body_padding, bottom=body_padding + keyboard_inset_bottom),
            content=ft.Column(
                expand=True,
                spacing=10 if compact else 14,
                controls=[
                    ft.Container(
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
                    ft.Container(
                        expand=True,
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
                    ),
                ],
            ),
        )

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

    def _is_compact_layout(self) -> bool:
        width = self.page.width or 0
        return width == 0 or width < 520

    def _editor_outline_border(self) -> ft.Border:
        side = ft.BorderSide(1, ft.Colors.BLUE_GREY_100)
        return ft.Border(top=side, right=side, bottom=side, left=side)

    def _on_editor_change(self, _event: ft.ControlEvent) -> None:
        self._refresh_editor_metrics(update_page=True)

    def _on_editor_selection_change(self, _event: ft.ControlEvent) -> None:
        # Selection change is intentionally lightweight and does not trigger repaint-heavy controls.
        pass

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
        route = self.page.route or ROUTE_ARTICLES
        if route == ROUTE_EDITOR:
            return self._build_view_shell("在线编辑器", self._build_editor_body(), ROUTE_EDITOR)
        if route == ROUTE_SETTINGS:
            return self._build_view_shell("同步设置", self._build_settings_body(), ROUTE_SETTINGS)
        return self._build_view_shell("文章列表", self._build_articles_body(), ROUTE_ARTICLES)

    def _on_route_change(self, _event: ft.RouteChangeEvent) -> None:
        self.page.views.clear()
        self.page.views.append(self._build_view())
        self.page.update()

    async def refresh_articles(self) -> None:
        self._set_loading(True)
        try:
            self.entries = await self.client.list_articles()
            if self._selected_index_for_route(self.page.route or ROUTE_ARTICLES) == 0:
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
