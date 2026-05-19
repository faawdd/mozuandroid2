# 墨筑 MoZu Android

> 默认展示：简体中文 | English: [README.en.md](README.en.md)

<p align="center">
	<img src="https://img.shields.io/badge/Brand-MoZu-111111?style=for-the-badge" alt="Brand: MoZu">
	<img src="https://img.shields.io/badge/Mobile-Android-3DDC84?style=for-the-badge&logo=android&logoColor=white" alt="Android">
	<img src="https://img.shields.io/badge/UI-Flet-0D1117?style=for-the-badge&logo=flutter&logoColor=46D1FD" alt="Flet">
	<img src="https://img.shields.io/badge/CMS-Hugo-AF2B1E?style=for-the-badge&logo=hugo&logoColor=white" alt="Hugo">
	<img src="https://img.shields.io/badge/CI-GitHub_Actions-2088FF?style=for-the-badge&logo=githubactions&logoColor=white" alt="GitHub Actions">
</p>

<p align="center">
	<img src="assets/icons/mozu.svg" alt="MoZu Icon" width="120">
</p>

基于 Flet 的移动端博客管理应用，面向 Hugo 内容维护场景，支持在手机端完成文章读取、编辑与发布，并通过 GitHub API 直接同步仓库内容。

## 亮点

- 三页式底部导航：文章列表、在线编辑器、同步设置。
- 在线直连 GitHub Contents API，无需本地 Git 仓库即可更新文章。
- 兼容 `content/posts` 与历史 `content/post` 目录，发布时自动归一化到 `content/posts`。
- 移动端交互优化：加载态、错误提示、编辑流程切换更直接。
- 应用图标与桌面版 `pyqt_blog_tool` 使用同一视觉源图（`mozu.svg`）。

## 环境变量配置

启动前可通过环境变量注入仓库信息：

- `GITHUB_TOKEN`
- `REPO_OWNER`
- `REPO_NAME`
- `GITHUB_BRANCH`（可选，默认 `main`）

也可在应用内“同步设置”页填写并保存，数据会写入 `app_settings.json`。

## 本地运行

安装依赖：

```bash
pip install -r requirements.txt
```

启动应用：

```bash
python main.py
```

## Android 图标说明

本项目将桌面版图标源文件同步到：

- `assets/icons/mozu.svg`

为适配 Flet 打包，提供图标生成脚本：

```bash
pip install pillow
python tools/generate_app_icons.py
```

脚本会生成：

- `assets/icon.png`
- `assets/icon_android.png`

GitHub Actions 在构建 APK 前会自动执行该脚本，因此云端打包可直接使用统一图标。

## GitHub Actions 打包

工作流文件：`.github/workflows/build-apk.yml`

- 仅在 `v*` 版本标签推送时触发。
- 使用 `flet build apk --split-per-abi` 按架构分包。
- 上传 `build/apk/*-release.apk` 作为构建产物。

## 核心文件

- `main.py`：Flet UI 路由、底部导航和页面交互。
- `github_service.py`：GitHub API 封装、文章读取与发布逻辑。
- `settings_store.py`：配置读取、保存与环境变量回填。
- `tools/generate_app_icons.py`：根据统一图形规则生成打包图标。