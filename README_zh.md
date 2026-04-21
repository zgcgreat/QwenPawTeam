# QwenPaw 多用户插件

> 基于 [QwenPaw](https://github.com/agentscope-ai/QwenPaw) (Apache License 2.0) 的二次开发，通过**一个环境变量**将单用户 QwenPaw 变为多用户平台。

---

**License**: [Apache License 2.0](https://www.apache.org/licenses/LICENSE-2.0)  
**原始项目**: [QwenPaw](https://github.com/agentscope-ai/QwenPaw) | Apache License 2.0  
**Copyright**: Copyright 2024 QwenPawTeam Authors

---

## 特性一览

| 特性 | 说明 |
|------|------|
| 🔐 **内置认证** | HMAC-SHA256 登录，支持自定义 Token 解析器对接 SSO/OAuth |
| 📁 **完整数据隔离** | 工作空间、配置、API Key、环境变量、Token 统计、日志、备份全部隔离 |
| 🧩 **灵活用户字段** | 一个字段（用户名）= 多用户模式；多字段（orgId/deptId/userId）= 企业层级隔离 |
| 🌍 **4 语言界面** | 前端登录表单和用户信息支持中文 / English / 日本語 / Русский |
| 🔄 **自动注册** | 通过环境变量预置管理员，开箱即用，适合 Docker / K8s |
| 🎯 **零入侵上游** | 仅 2 处上游文件修改（通用插件钩子），升级无压力 |
| ✅ **完全向后兼容** | 禁用插件 = 原生单用户 QwenPaw，一行配置切换 |

---

## 快速上手

### 1. 设置环境变量

```bash
QWENPAW_MULTI_USER_ENABLED=true
QWENPAW_AUTH_ENABLED=true
QWENPAW_AUTH_USERNAME=admin
QWENPAW_AUTH_PASSWORD=changeme
```

### 2. 启动服务

```bash
python run_server.py
```

### 3. 打开浏览器登录

访问 `http://localhost:8000/login`，使用配置的用户名和密码登录。

---

## 典型场景

### 场景 A：简单多用户（开发 / 小团队）

所有用户共用一套配置，仅通过用户名隔离。

```bash
QWENPAW_MULTI_USER_ENABLED=true
QWENPAW_AUTH_ENABLED=true
QWENPAW_AUTH_USERNAME=admin
QWENPAW_AUTH_PASSWORD=secret123
```

每个用户自动获得独立工作目录：`WORKING_DIR/users/{username}/`

> **免注册**：新用户直接用新的用户名 + 密码登录即可自动创建账号。

### 场景 B：企业层级隔离（多字段）

```bash
QWENPAW_MULTI_USER_ENABLED=true
QWENPAW_AUTH_ENABLED=true
QWENPAW_USER_FIELDS=orgId,deptId,userId

# 中文标签
QWENPAW_USER_FIELD_LABELS_ZH={"orgId":"机构编号","deptId":"部门编号","userId":"用户编号"}
# 英文标签
QWENPAW_USER_FIELD_LABELS_EN={"orgId":"Organization ID","deptId":"Department ID","userId":"User ID"}
# 日语标签
QWENPAW_USER_FIELD_LABELS_JA={"orgId":"組織ID","deptId":"部門ID","userId":"ユーザーID"}
# 俄语标签
QWENPAW_USER_FIELD_LABELS_RU={"orgId":"ИД организации","deptId":"ИД отдела","userId":"ИД пользователя"}

# 管理员账号
QWENPAW_AUTH_ORGID=ACME
QWENPAW_AUTH_DEPTID=ENG
QWENPAW_AUTH_USERID=alice
QWENPAW_AUTH_PASSWORD=secret123
```

目录结构：`WORKING_DIR/users/ACME/ENG/alice/`

登录表单根据 `QWENPAW_USER_FIELDS` 动态渲染，自动匹配语言。

### 场景 C：SSO / 网关集成

对接已有的 Keycloak、Auth0、Nginx OAuth 代理等。

```bash
QWENPAW_MULTI_USER_ENABLED=true
QWENPAW_AUTH_ENABLED=false
QWENPAW_USER_FIELDS=orgId,userId
QWENPAW_TOKEN_PARSER_MODULE=my_sso.parser
```

实现自定义 Token 解析器：

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

## 配置项参考

| 环境变量 | 说明 | 默认值 |
|----------|------|--------|
| `QWENPAW_MULTI_USER_ENABLED` | 启用多用户插件 | `true` |
| `QWENPAW_AUTH_ENABLED` | 启用内置 HMAC 认证 | `true` |
| `QWENPAW_USER_FIELDS` | 用户字段名，逗号分隔 | `username` |
| `QWENPAW_USER_FIELD_LABELS_ZH` | 中文标签 | `{"username":"用户名"}` |
| `QWENPAW_USER_FIELD_LABELS_EN` | 英文标签 | `{"username":"Username"}` |
| `QWENPAW_USER_FIELD_LABELS_JA` | 日语标签 | `{"username":"ユーザー名"}` |
| `QWENPAW_USER_FIELD_LABELS_RU` | 俄语标签 | `{"username":"Имя пользователя"}` |
| `QWENPAW_TOKEN_PARSER_MODULE` | 自定义 Token 解析器模块路径 | 内置解析器 |

---

## 数据隔离清单

| 数据类别 | 隔离方式 |
|----------|----------|
| 工作空间（Agent、对话、Memory、Skills） | 每个用户独立目录 |
| 配置文件（config.json） | 每用户一份 |
| API Key / Provider 凭证 | 每用户独立覆盖 |
| 环境变量（envs.json） | 每用户独立 |
| Token 消耗统计 | 按（用户 × Agent）统计 |
| 后端日志 | 每用户独立日志文件 |
| 备份 / 恢复 | 每用户独立备份目录 |

---

## 支持的语言

前端登录界面支持以下 4 种语言，可根据浏览器语言偏好自动切换：

| 语言 | 语言代码 | 状态 |
|------|----------|------|
| 🇨🇳 中文 | `zh` | ✅ 完全支持 |
| 🇺🇸 English | `en` | ✅ 完全支持 |
| 🇯🇵 日本語 | `ja` | ✅ 完全支持 |
| 🇷🇺 Русский | `ru` | ✅ 完全支持 |

---

## API 接口

| 接口 | 方法 | 说明 |
|------|------|------|
| `/api/auth/login` | POST | 登录（支持动态字段，自动注册新用户） |
| `/api/auth/status` | GET | 获取认证状态、用户字段、界面标签 |
| `/api/auth/verify` | GET | 验证 Token 有效性 |
| `/api/auth/resolve-user` | GET | 从上游 Token 解析用户身份 |
| `/api/auth/init-workspace` | POST | 初始化用户工作空间（仅集成模式） |
| `/api/auth/users` | GET | 列出所有已注册用户 |
| `/api/auth/update-profile` | POST | 修改密码 |
| `/api/auth/users/{id}` | DELETE | 删除用户账号 |

---

## 常见问题

**Q: 禁用插件后数据会丢失吗？**  
A: 不会。禁用后系统回到原生单用户模式，`users/` 目录被忽略，原有数据完整保留。

**Q: 后续可以增加用户字段吗？**  
A: 可以。更新 `QWENPAW_USER_FIELDS` 和对应标签环境变量后重启即可。

**Q: 需要外部数据库吗？**  
A: 不需要。用户数据存储在 `SECRET_DIR/auth.json`（JSON 文件）中。

**Q: 不同用户可以使用不同的模型 API Key 吗？**  
A: 可以。每个用户可独立配置自己的 API Key，覆盖全局设置。

---

## 插件文件结构

```
src/qwenpaw_plugins/multi_user/
├── __init__.py              # 插件入口（activate/deactivate）
├── constants.py              # 环境变量名、默认标签
├── user_context.py           # 异步用户 ID 传播（ContextVar）
├── token_parser.py           # 可插拔 Token 解析器
├── auth_extension.py          # HMAC 认证、AuthMiddleware
├── router_extension.py        # 8 个认证 API 端点
├── manager_extension.py       # 多用户 Agent 管理器包装
├── provider_extension.py      # 多用户 Provider 凭证覆盖
├── config_extension.py        # 每用户配置（monkey-patch）
├── envs_extension.py          # 每用户环境变量（monkey-patch）
├── agents_extension.py        # 每用户工作空间目录
├── migration_extension.py     # 工作空间懒初始化
├── token_usage_extension.py   # 每用户 Token 统计
├── console_extension.py       # 每用户后端日志
├── backup_extension.py        # 每用户备份 / 恢复
└── middleware.py             # 中间件工厂
```
