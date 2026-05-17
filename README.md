# 墨筑 MoZu Android

基于 Flet 的移动端博客管理界面，使用底部导航栏在“文章列表 / 在线编辑器 / 同步设置”之间切换，并直接通过 GitHub REST API 读取、编辑和发布 Hugo 文章，不再依赖本地 Git 仓库。

## 运行前配置

在环境变量中设置：

- `GITHUB_TOKEN`
- `REPO_OWNER`
- `REPO_NAME`
- 可选：`GITHUB_BRANCH`，默认 `main`

这些值也可以在应用内的“同步设置”页面中保存，保存后会写入同目录下的 `app_settings.json`。

## 安装依赖

```bash
pip install -r requirements.txt
```

## 启动

```bash
python main.py
```

## 功能说明

- 底部导航栏固定在屏幕下方，适合手机单手切换页面。
- 文章列表页从 `content/posts` 读取 `.md` 文件列表。
- 点击文章后通过 GitHub Contents API 拉取并自动解码 Base64 内容，然后自动切到编辑器页。
- 编辑器页只保存 Markdown 正文，发布时自动拼接 YAML Front Matter。
- 新建文章会先弹出对话框输入标题和分类。
- 发布时使用 `PUT /contents/...` 完成在线同步；更新已有文章会自动带上 `sha`。
- 网络请求期间显示加载进度，并用 SnackBar 提示错误。

## 文件结构

- `main.py`：Flet 路由、底部导航、三页视图和交互逻辑。
- `github_service.py`：GitHub REST API 访问、Front Matter 解析与发布。
- `settings_store.py`：应用设置读取、保存与环境变量同步。