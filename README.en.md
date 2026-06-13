# MoZu Android

> Default display: Simplified Chinese | 中文版: [README.md](README.md)

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

A Flet-based mobile blog management app for Hugo workflows. It lets you read, edit, and publish posts directly on mobile devices through the GitHub API.

## Highlights

- Three-tab bottom navigation: post list, online editor, and sync settings.
- Direct GitHub Contents API integration, no local Git repository required.
- Compatible with both `content/posts` and legacy `content/post`, and normalizes publishing to `content/posts`.
- Mobile-friendly interactions with loading states, error feedback, and streamlined editor flow.
- App icon is aligned with the desktop `pyqt_blog_tool` and uses the same visual source (`mozu.svg`).

## Environment Variables

Set these variables before launch:

- `GITHUB_TOKEN`
- `REPO_OWNER`
- `REPO_NAME`
- `GITHUB_BRANCH` (optional, default is `main`)

You can also fill these values in the in-app Sync Settings page. They are persisted to `app_settings.json`.

## Local Run

Install dependencies:

```bash
pip install -r requirements.txt
```

Start the app:

```bash
python main.py
```

## Android Icon Notes

This project includes the desktop icon source at:

- `assets/icons/mozu.svg`

For Flet packaging compatibility, use the icon generation script:

```bash
pip install pillow
python tools/generate_app_icons.py
```

The script generates:

- `assets/icon.png`
- `assets/icon_android.png`

GitHub Actions runs this script before building APKs, so cloud builds always use the unified icon style.

## GitHub Actions Packaging

Workflow file: `.github/workflows/build-apk.yml`

- Triggered only by version tags matching `v*`.
- Uses `flet build apk --split-per-abi` for ABI-split APK outputs.
- Uses a fixed Android keystore for signing, so new APKs can be installed as upgrades.
- Uploads per-ABI APKs as both workflow artifacts and Release assets.
- Build metadata is unified in Flet parameters:
  - App project name: `mozuapp`
  - App product name: `MoZu App`
  - Bundle ID: `com.mozu.app`
  - Build version: derived from tag (for example, `v1.2.3` -> `1.2.3`)
  - Build number: `GITHUB_RUN_NUMBER`

To keep APK signatures stable, configure these repository secrets in
`Settings -> Secrets and variables -> Actions`:

- `ANDROID_KEYSTORE_BASE64`: Base64-encoded fixed upload keystore (`.jks`).
- `ANDROID_KEY_ALIAS`: signing key alias in that keystore.
- `ANDROID_KEYSTORE_PASSWORD`: keystore password.
- `ANDROID_KEY_PASSWORD`: key password for the alias.

As long as these values stay unchanged and the application ID (`com.mozu.app`) stays the same, each new APK can be installed directly over the previous one.

## Core Files

- `main.py`: Flet UI routes, bottom navigation, and interaction logic.
- `github_service.py`: GitHub API access, post loading, and publishing logic.
- `settings_store.py`: configuration load/save and environment mapping.
- `tools/generate_app_icons.py`: icon generator based on the shared MoZu shape rules.
