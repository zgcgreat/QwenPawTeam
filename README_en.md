# QwenPaw Multi-User Plugin

> A derivative work of [QwenPaw](https://github.com/agentscope-ai/QwenPaw) (Apache License 2.0). Turn a single-user instance into a multi-user platform with **one environment variable**.

---

**License**: [Apache License 2.0](https://www.apache.org/licenses/LICENSE-2.0)  
**Base Project**: [QwenPaw](https://github.com/agentscope-ai/QwenPaw) | Apache License 2.0  
**Copyright**: Copyright 2024 QwenPawTeam Authors

---

## Features at a Glance

| Feature | Description |
|---------|-------------|
| 🔐 **Built-in Auth** | HMAC-SHA256 login, with custom TokenParser for SSO/OAuth integration |
| 📁 **Full Data Isolation** | Workspace, config, API keys, env vars, token stats, logs, and backups are all isolated per user |
| 🧩 **Flexible User Fields** | One field (username) = simple multi-user; multiple fields (orgId/deptId/userId) = enterprise hierarchy |
| 🌍 **4-Language UI** | Frontend login form and user info support Chinese / English / 日本語 / Русский |
| 🔄 **Auto-Registration** | Pre-seed admin account via env vars — ready out of the box, perfect for Docker / K8s |
| 🎯 **Zero Upstream Invasion** | Only 2 upstream file changes (generic plugin hooks), no pain on upgrade |
| ✅ **Fully Backward Compatible** | Disabled = original single-user QwenPaw, one config toggle away |

---

## Quick Start

### 1. Set environment variables

```bash
QWENPAW_MULTI_USER_ENABLED=true
QWENPAW_AUTH_ENABLED=true
QWENPAW_AUTH_USERNAME=admin
QWENPAW_AUTH_PASSWORD=changeme
```

### 2. Start the server

#### Backend Startup

```bash
# Create virtual environment (first time only)
python -m venv venv

# Activate virtual environment
# Windows:
venv\Scripts\activate
# Linux/Mac:
source venv/bin/activate

# Install dependencies (first time only)
pip install -e .

# Start backend service
python run_server.py
```

#### Frontend Startup

```bash
# Enter frontend directory
cd console

# Install dependencies (first time only)
pnpm install

# Start frontend development server
pnpm run dev
```

The backend runs on `http://localhost:8000` by default, and the frontend runs on `http://localhost:5173`.

### 3. Open your browser

Visit `http://localhost:8000/login` and log in with the username and password you configured above.

---

## Typical Scenarios

### Scenario A: Simple Multi-User (Development / Small Team)

All users share one configuration, isolated only by username.

```bash
QWENPAW_MULTI_USER_ENABLED=true
QWENPAW_AUTH_ENABLED=true
QWENPAW_AUTH_USERNAME=admin
QWENPAW_AUTH_PASSWORD=secret123
```

Each user gets an independent workspace: `WORKING_DIR/users/{username}/`

> **No registration needed**: New users can log in directly with a new username + password and the account is created automatically.

### Scenario B: Enterprise Hierarchy Isolation (Multiple Fields)

```bash
QWENPAW_MULTI_USER_ENABLED=true
QWENPAW_AUTH_ENABLED=true
QWENPAW_USER_FIELDS=orgId,deptId,userId

# Chinese labels
QWENPAW_USER_FIELD_LABELS_ZH={"orgId":"机构编号","deptId":"部门编号","userId":"用户编号"}
# English labels
QWENPAW_USER_FIELD_LABELS_EN={"orgId":"Organization ID","deptId":"Department ID","userId":"User ID"}
# Japanese labels
QWENPAW_USER_FIELD_LABELS_JA={"orgId":"組織ID","deptId":"部門ID","userId":"ユーザーID"}
# Russian labels
QWENPAW_USER_FIELD_LABELS_RU={"orgId":"ИД организации","deptId":"ИД отдела","userId":"ИД пользователя"}

# Admin account
QWENPAW_AUTH_ORGID=ACME
QWENPAW_AUTH_DEPTID=ENG
QWENPAW_AUTH_USERID=alice
QWENPAW_AUTH_PASSWORD=secret123
```

Directory structure: `WORKING_DIR/users/ACME/ENG/alice/`

The login form renders dynamically based on `QWENPAW_USER_FIELDS` and switches language automatically by browser preference.

### Scenario C: SSO / Gateway Integration

Connect to existing systems like Keycloak, Auth0, or Nginx OAuth proxy.

```bash
QWENPAW_MULTI_USER_ENABLED=true
QWENPAW_AUTH_ENABLED=false
QWENPAW_USER_FIELDS=orgId,userId
QWENPAW_TOKEN_PARSER_MODULE=my_sso.parser
```

Implement a custom TokenParser:

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

## Configuration Reference

| Environment Variable | Description | Default |
|---------------------|-------------|---------|
| `QWENPAW_MULTI_USER_ENABLED` | Enable multi-user plugin | `true` |
| `QWENPAW_AUTH_ENABLED` | Enable built-in HMAC auth | `true` |
| `QWENPAW_USER_FIELDS` | User field names, comma-separated | `username` |
| `QWENPAW_USER_FIELD_LABELS_ZH` | Chinese labels | `{"username":"用户名"}` |
| `QWENPAW_USER_FIELD_LABELS_EN` | English labels | `{"username":"Username"}` |
| `QWENPAW_USER_FIELD_LABELS_JA` | Japanese labels | `{"username":"ユーザー名"}` |
| `QWENPAW_USER_FIELD_LABELS_RU` | Russian labels | `{"username":"Имя пользователя"}` |
| `QWENPAW_TOKEN_PARSER_MODULE` | Dotted path to custom TokenParser module | Built-in parser |

---

## Data Isolation

| Data Category | Isolation |
|--------------|-----------|
| Workspace (Agents, chats, Memory, Skills) | Independent directory per user |
| Config file (config.json) | One copy per user |
| API Key / Provider credentials | Independent overrides per user |
| Environment variables (envs.json) | Independent per user |
| Token usage stats | Per (user × Agent) |
| Backend logs | Independent log file per user |
| Backup / Restore | Independent backup directory per user |

---

## Supported Languages

The frontend login UI supports **4 languages** and switches automatically based on browser language preference:

| Language | Code | Status |
|----------|------|--------|
| 🇨🇳 Chinese | `zh` | ✅ Full support |
| 🇺🇸 English | `en` | ✅ Full support |
| 🇯🇵 Japanese | `ja` | ✅ Full support |
| 🇷🇺 Russian | `ru` | ✅ Full support |

---

## API Endpoints

| Endpoint | Method | Description |
|----------|--------|-------------|
| `/api/auth/login` | POST | Login with dynamic fields; auto-registers new users |
| `/api/auth/status` | GET | Returns auth status, user fields, and UI labels |
| `/api/auth/verify` | GET | Verify if a Bearer token is valid |
| `/api/auth/resolve-user` | GET | Parse user identity from an upstream token |
| `/api/auth/init-workspace` | POST | Initialize user workspace (integration mode only) |
| `/api/auth/users` | GET | List all registered users |
| `/api/auth/update-profile` | POST | Change password |
| `/api/auth/users/{id}` | DELETE | Delete a user account |

---

## FAQ

**Q: Will disabling the plugin lose any data?**  
A: No. When disabled, the system reverts to single-user mode. The `users/` directory is ignored and all original data stays intact.

**Q: Can I add more user fields after initial setup?**  
A: Yes. Just update `QWENPAW_USER_FIELDS` and the corresponding label env vars, then restart.

**Q: Do I need an external database?**  
A: No. User data is stored in `SECRET_DIR/auth.json` (a JSON file).

**Q: Can different users use different LLM API keys?**  
A: Yes. Each user can configure their own API keys to override the global settings.

---

## Plugin File Structure

```
src/qwenpaw_plugins/multi_user/
├── __init__.py              # Plugin entry (activate/deactivate)
├── constants.py              # Env var names, default labels
├── user_context.py           # Async user ID propagation (ContextVar)
├── token_parser.py           # Pluggable TokenParser
├── auth_extension.py          # HMAC auth, AuthMiddleware
├── router_extension.py        # 8 auth API endpoints
├── manager_extension.py       # UserAware MultiAgentManager wrapper
├── provider_extension.py      # UserAware ProviderManager credential overlay
├── config_extension.py        # Per-user config (monkey-patch)
├── envs_extension.py          # Per-user env vars (monkey-patch)
├── agents_extension.py        # Per-user workspace directory
├── migration_extension.py     # Lazy workspace initialization
├── token_usage_extension.py   # Per-user token stats
├── console_extension.py       # Per-user backend logs
├── backup_extension.py        # Per-user backup/restore
└── middleware.py             # Middleware factory
```
