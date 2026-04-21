# QwenPaw マルチユーザープラグイン

> **1つの環境変数**で単一ユーザー QwenPaw をマルチユーザープラットフォームに変換。上流コードの変更は不要。

---

## 機能一覧

| 機能 | 説明 |
|------|------|
| 🔐 **内蔵認証** | HMAC-SHA256 ログイン、カスタム TokenParser で SSO/OAuth との統合に対応 |
| 📁 **完全なデータ分離** | ワークスペース、設定、API Key、環境変数、Token 統計、ログ、バックアップがすべてユーザーごとに分離 |
| 🧩 **柔軟なユーザーフィールド** | 単一フィールド（用户名）= 简单マルチユーザー；複数フィールド（orgId/deptId/userId）= 企業階層分離 |
| 🌍 **4言語対応 UI** | フロントエンドログインフォームとユーザー情報が中文 / English / 日本語 / Русский に対応 |
| 🔄 **自动登録** | 環境変数で管理者アカウントを事前設定、Docker / K8s に最適 |
| 🎯 **上流コードへの侵入ゼロ** | 上流ファイルの変更はわずか 2 か所（汎用プラグインフック）、アップグレードが容易 |
| ✅ **完全な下位互換性** | プラグイン無効化 = 原生の単一ユーザー QwenPaw |

---

## クイックスタート

### 1. 環境変数を設定

```bash
QWENPAW_MULTI_USER_ENABLED=true
QWENPAW_AUTH_ENABLED=true
QWENPAW_AUTH_USERNAME=admin
QWENPAW_AUTH_PASSWORD=changeme
```

### 2. サーバーを起動

```bash
python run_server.py
```

### 3. ブラウザでログイン

`http://localhost:8000/login` にアクセスし、設定した用户名とパスワードでログイン。

---

## 典型的なシナリオ

### シナリオ A：简单マルチユーザー（開発 / 小規模チーム）

すべてのユーザーが同一の設定を共有し、ユーザー名で分離。

```bash
QWENPAW_MULTI_USER_ENABLED=true
QWENPAW_AUTH_ENABLED=true
QWENPAW_AUTH_USERNAME=admin
QWENPAW_AUTH_PASSWORD=secret123
```

各ユーザーは独立した作業ディレクトリを取得します: `WORKING_DIR/users/{username}/`

> **登録不要**：新しいユーザーは新しい 用户名 + パスワードでログインすると自動的にアカウントが作成されます。

### シナリオ B：企業階層分離（複数フィールド）

```bash
QWENPAW_MULTI_USER_ENABLED=true
QWENPAW_AUTH_ENABLED=true
QWENPAW_USER_FIELDS=orgId,deptId,userId

# 中国語ラベル
QWENPAW_USER_FIELD_LABELS_ZH={"orgId":"机构编号","deptId":"部门编号","userId":"用户编号"}
# 英語ラベル
QWENPAW_USER_FIELD_LABELS_EN={"orgId":"Organization ID","deptId":"Department ID","userId":"User ID"}
# 日本語ラベル
QWENPAW_USER_FIELD_LABELS_JA={"orgId":"組織ID","deptId":"部門ID","userId":"ユーザーID"}
# ロシア語ラベル
QWENPAW_USER_FIELD_LABELS_RU={"orgId":"ИД организации","deptId":"ИД отдела","userId":"ИД пользователя"}

# 管理者アカウント
QWENPAW_AUTH_ORGID=ACME
QWENPAW_AUTH_DEPTID=ENG
QWENPAW_AUTH_USERID=alice
QWENPAW_AUTH_PASSWORD=secret123
```

ディレクトリ構造: `WORKING_DIR/users/ACME/ENG/alice/`

ログインフォームは `QWENPAW_USER_FIELDS` に基づいて動的にレンダリングされ、ブラウザの言語設定に応じて自動切り替え。

### シナリオ C：SSO / ゲートウェイ統合

Keycloak、Auth0、Nginx OAuth プロキシなどの既存のシステムと連携。

```bash
QWENPAW_MULTI_USER_ENABLED=true
QWENPAW_AUTH_ENABLED=false
QWENPAW_USER_FIELDS=orgId,userId
QWENPAW_TOKEN_PARSER_MODULE=my_sso.parser
```

カスタム TokenParser を実装:

```python
from qwenpaw_plugins.multi_user.token_parser import TokenParser

class KeycloakTokenParser(TokenParser):
    def parse(self, token: str):
        import jwt
        payload = jwt.decode(token, options={"verify_signature": False})
        return {
            "orgId": payload.get("org_id", ""),
            "userId": payload.get("sub", ""),
        }

def create_token_parser() -> TokenParser:
    return KeycloakTokenParser()
```

---

## 設定リファレンス

| 環境変数 | 説明 | デフォルト値 |
|----------|------|--------|
| `QWENPAW_MULTI_USER_ENABLED` | マルチユーザープラグインを有効化 | `true` |
| `QWENPAW_AUTH_ENABLED` | 内蔵 HMAC 認証を有効化 | `true` |
| `QWENPAW_USER_FIELDS` | ユーザーフィールド名、カンマ区切り | `username` |
| `QWENPAW_USER_FIELD_LABELS_ZH` | 中国語ラベル | `{"username":"用户名"}` |
| `QWENPAW_USER_FIELD_LABELS_EN` | 英語ラベル | `{"username":"Username"}` |
| `QWENPAW_USER_FIELD_LABELS_JA` | 日本語ラベル | `{"username":"ユーザー名"}` |
| `QWENPAW_USER_FIELD_LABELS_RU` | ロシア語ラベル | `{"username":"Имя пользователя"}` |
| `QWENPAW_TOKEN_PARSER_MODULE` | カスタム TokenParser モジュールのドットパス | 内蔵パーサー |

---

## データ分離清单

| データカテゴリ | 分離方式 |
|----------|----------|
| ワークスペース（Agent、対話、Memory、Skills） | ユーザーごとに独立ディレクトリ |
| 設定ファイル（config.json） | ユーザーごとに1つ |
| API Key / Provider 認証情報 | ユーザーごとに独立上書き |
| 環境変数（envs.json） | ユーザーごとに独立 |
| Token 消費統計 | （ユーザー × Agent）単位で統計 |
| バックエンドログ | ユーザーごとに独立ログファイル |
| バックアップ / リストア | ユーザーごとに独立バックアップディレクトリ |

---

## 対応言語

フロントエンドログイン UI は以下の **4言語** に対応し、ブラウザの言語設定に応じて自動切り替え:

| 言語 | 言語コード | ステータス |
|------|----------|----------|
| 🇨🇳 中文 | `zh` | ✅ 完全対応 |
| 🇺🇸 English | `en` | ✅ 完全対応 |
| 🇯🇵 日本語 | `ja` | ✅ 完全対応 |
| 🇷🇺 Русский | `ru` | ✅ 完全対応 |

---

## API エンドポイント

| エンドポイント | メソッド | 説明 |
|------|------|------|
| `/api/auth/login` | POST | ログイン（動的フィールド対応、新ユーザー自動登録） |
| `/api/auth/status` | GET | 認証状態、ユーザーフィールド、UI ラベルを取得 |
| `/api/auth/verify` | GET | Bearer トークンの有効性を検証 |
| `/api/auth/resolve-user` | GET | 上流トークンからユーザーアイデンティティを解析 |
| `/api/auth/init-workspace` | POST | ユーザーワークスペースを初期化（統合モードのみ） |
| `/api/auth/users` | GET | 登録済み全ユーザーをリスト |
| `/api/auth/update-profile` | POST | パスワードを変更 |
| `/api/auth/users/{id}` | DELETE | ユーザーアカウントを削除 |

---

## よくある質問

**Q: プラグインを無効にするとデータは失われますか？**  
A: いいえ。無効にするとシングルユーザーモードに戻り、`users/` ディレクトリは無視されます。元のデータはすべてそのまま残ります。

**Q: 後からユーザーフィールドを追加できますか？**  
A: はい。`QWENPAW_USER_FIELDS` と対応するラベル環境変数を更新して再起動してください。

**Q: 外部データベースが必要ですか？**  
A: いいえ。ユーザーデータは `SECRET_DIR/auth.json`（JSON ファイル）に保存されます。

**Q: 異なるユーザーで異なる LLM API Key を使用できますか？**  
A: はい。各ユーザーはグローバル設定をオーバーライドする独自の API Key を構成できます。

---

## プラグインファイル構造

```
src/qwenpaw_plugins/multi_user/
├── __init__.py              # プラグインエントリー（activate/deactivate）
├── constants.py              # 環境変数名、デフォルトラベル
├── user_context.py           # 非同期ユーザー ID 伝播（ContextVar）
├── token_parser.py           # プラグイン可能な TokenParser
├── auth_extension.py         # HMAC 認証、AuthMiddleware
├── router_extension.py       # 8 つの認証 API エンドポイント
├── manager_extension.py      # UserAware MultiAgentManager ラッパー
├── provider_extension.py     # UserAware ProviderManager 認証情報オーバーレイ
├── config_extension.py       # ユーザーごと設定（monkey-patch）
├── envs_extension.py        # ユーザーごと環境変数（monkey-patch）
├── agents_extension.py       # ユーザーごとワークスペースディレクトリ
├── migration_extension.py    # 遅延ワークスペース初期化
├── token_usage_extension.py  # ユーザーごと Token 統計
├── console_extension.py     # ユーザーごとバックエンドログ
├── backup_extension.py       # ユーザーごとバックアップ / リストア
└── middleware.py            # ミドルウェアファクトリー
```
