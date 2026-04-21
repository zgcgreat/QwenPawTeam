# QwenPaw Multi-User Plugin

> Turn a single-user QwenPaw instance into a multi-user platform with **one environment variable** — zero changes to upstream code.

---

## Choose Your Language / 选择语言 / 言語を選択 / Выберите язык

| Language | Documentation |
|----------|---------------|
| 🇨🇳 [中文](README_zh.md) | 多用户插件完整文档 |
| 🇺🇸 [English](README_en.md) | Complete multi-user plugin documentation |
| 🇯🇵 [日本語](README_ja.md) | マルチユーザープラグインの完全なドキュメント |
| 🇷🇺 [Русский](README_ru.md) | Полная документация плагина мультиользователя |

---

## Quick Overview

| Feature | Description |
|---------|-------------|
| 🔐 **Auth** | Built-in HMAC-SHA256, or plug in your own SSO via `TokenParser` |
| 📁 **Isolation** | Workspace, config, API keys, env vars, logs, backups — all per user |
| 🧩 **Flexible Fields** | One field (username) or multiple (orgId/deptId/userId) |
| 🌍 **4 Languages** | Chinese · English · Japanese · Russian |
| 🔄 **Auto-Registration** | Pre-seed admin via env vars |
| ✅ **Zero Invasion** | Only 2 upstream changes, fully backward compatible |

---

## Quick Start

```bash
QWENPAW_MULTI_USER_ENABLED=true
QWENPAW_AUTH_ENABLED=true
QWENPAW_AUTH_USERNAME=admin
QWENPAW_AUTH_PASSWORD=changeme
```

```bash
python run_server.py
# → Open http://localhost:8000/login
```

---

## Files

| File | Language |
|------|----------|
| `README_zh.md` | 🇨🇳 中文 |
| `README_en.md` | 🇺🇸 English |
| `README_ja.md` | 🇯🇵 日本語 |
| `README_ru.md` | 🇷🇺 Русский |
