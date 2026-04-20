# QwenPaw 多租户插件

> **状态**: ✅ 前后端全部完成，已优化最小化上游入侵

## 概述

本插件为 QwenPaw 添加**多租户（多用户）支持**。通过 Wrapper 类 + Monkey-patch + 通用扩展点组合策略，将对上游源码的修改压缩到最小，便于持续同步上游更新。

## 架构

```
QwenPaw/src/
├── qwenpaw/                              ← 上游代码（极少修改）
│   ├── app/_app.py                       ← 通用插件钩子 + 激活入口
│   ├── app/routers/agents.py             ← 可 patch 的 _resolve_workspace_base_dir()
│   └── ...
│
├── qwenpaw_plugins/                      ← 通用插件框架
│   └── __init__.py                       register_lifespan_hook / run_lifespan_hooks
│
└── qwenpaw_plugins/multi_tenant/         ★ 本插件
    ├── __init__.py                       activate() / deactivate() 生命周期
    ├── tenant_context.py                 ContextVar 租户上下文传播
    ├── token_parser.py                   可插拔 Token 解析器
    ├── constants.py                      所有常量定义
    ├── config_extension.py               租户感知的 load_config/save_config
    ├── agents_extension.py               租户感知的 workspace 基目录
    ├── auth_extension.py                 多用户认证 + AuthMiddleware 中间件
    ├── router_extension.py               8 个新 API 端点
    ├── manager_extension.py              租户隔离的 MultiAgentManager（lifespan hook）
    ├── provider_extension.py             按租户覆盖 API 密钥（lifespan hook）
    ├── envs_extension.py                 环境变量租户隔离
    ├── migration_extension.py            工作区自动初始化
    └── middleware.py                     中间件工厂
```

## 激活方式

启动 QwenPaw 前设置环境变量：

```bash
# Linux / macOS:
export QWENPAW_MULTI_TENANT_ENABLED=true

# Windows:
set QWENPAW_MULTI_TENANT_ENABLED=true
```

`_app.py` 中的激活钩子会自动调用 `activate_multi_tenant(app)`。

**可选**：启用内置 HMAC 认证：

```bash
export QWENPAW_AUTH_ENABLED=true
```

## 两种运行模式

### 独立模式 (`QWENPAW_AUTH_ENABLED=true`)
- 内置用户注册和登录功能
- HMAC-SHA256 Token（无 PyJWT 依赖）
- 密码以 SHA-256 哈希 + 盐值存储

### 集成模式 (`QWENPAW_AUTH_ENABLED=false`)
- 信任上游网关 Token
- 可插拔 `TokenParser`，支持自定义 Token 格式
- 无需密码 — 租户身份由网关提供

## API 端点

| 端点 | 方法 | 说明 |
|------|------|------|
| `/api/auth/login` | POST | 五字段登录 + 自动注册 |
| `/api/auth/init-workspace` | POST | 初始化租户工作区（集成模式） |
| `/api/auth/status` | GET | 认证状态 + 多租户开关信息 |
| `/api/auth/verify` | GET | 验证 Bearer Token 有效性 |
| `/api/auth/resolve-tenant` | GET | 从 Token 解析租户信息 |
| `/api/auth/users` | GET | 列出所有注册用户 |
| `/api/auth/update-profile` | POST | 修改密码 |
| `/api/auth/users/{id}` | DELETE | 删除用户账户 |

## 数据目录结构

```
{WORKING_DIR}/
├── config.json                    # 根配置（单用户回退）
├── .copaw.secret/
│   ├── auth.json                  # 用户数据库 + JWT 密钥
│   └── tenants/{tenant_id}/
│       ├── envs.json              # 租户独立环境变量
│       └── providers/             # 租户独立 Provider 配置
│           ├── builtin/           # 内置 Provider 覆盖（API Key 等）
│           ├── custom/            # 自定义 Provider
│           └── active_model.json  # 当前激活模型
└── tenants/
    └── {sysId}/{branchId}/{vorgCode}/{sapId}/{positionId}/
        ├── config.json            # 各租户独立配置
        └── workspaces/default/    # 默认 Agent 工作区
            ├── agent.json         # Agent 配置
            ├── chats.json
            ├── jobs.json
            ├── sessions/
            ├── memory/
            ├── active_skills/
            ├── customized_skills/
            └── PROFILE.md          # 注入租户身份信息
```

## 自定义 Token 解析器

如需对接现有 SSO 系统，可实现自定义解析器：

```python
# my_token_parser.py
from qwenpaw_plugins.multi_tenant.token_parser import TokenParser

class MySSOTokenParser(TokenParser):
    def parse(self, token: str):
        # 解码你的自定义 Token 格式
        return {"sysId": "...", "branchId": "...", ...}
    
    @staticmethod
    def extract_bearer(auth_header: str) -> str:
        # 从 Authorization 头提取 Token
        ...
```

然后设置：

```bash
export QWENPAW_TOKEN_PARSER_MODULE=my_token_parser.MySSOTokenParser
```

## 插件扩展机制

本插件使用三种扩展机制与上游代码对接，最小化入侵：

### 1. Lifespan Hooks（`qwenpaw_plugins` 通用框架）

```python
# qwenpaw_plugins/__init__.py 提供:
register_lifespan_hook(hook_point, callback)
run_lifespan_hooks(hook_point, app, obj)

# 支持的钩子点:
# - "post_manager_init"   → MultiAgentManager 创建后
# - "post_provider_init"  → ProviderManager 获取后
```

`_app.py` lifespan 中仅需 2 处通用调用（不引用任何多租户模块）：

```python
multi_agent_manager = await run_lifespan_hooks("post_manager_init", app, multi_agent_manager)
provider_manager = await run_lifespan_hooks("post_provider_init", app, provider_manager)
```

### 2. 可 Monkey-patch 的函数

上游代码中提供可替换的函数，插件在激活时替换为租户感知版本：

| 上游文件 | 函数 | 默认行为 | 插件替换 |
|----------|------|----------|----------|
| `agents.py` | `_resolve_workspace_base_dir()` | 返回 `WORKING_DIR` | 返回租户工作目录 |
| `request.ts` | `handle401`（通过 `setHandle401` 替换） | 清 token + 跳转 | SSO cookie 检测 + 额外清理 |

### 3. 委托模式（前端）

前端通过条件导入委托到插件版本：

```typescript
// api/authHeaders.ts — 委托模式
import { MULTI_TENANT_ENABLED } from "../multi_tenant/index";
import { buildAuthHeaders as mtBuildAuthHeaders } from "../multi_tenant/authHeaders";

export function buildAuthHeaders() {
  return MULTI_TENANT_ENABLED ? mtBuildAuthHeaders() : _upstreamBuildAuthHeaders();
}
```

## 设计原则

1. **最小上游入侵**：后端仅 `_app.py`（通用钩子 + 激活入口）和 `agents.py`（可 patch 函数），前端 5 个文件均为条件渲染/委托模式
2. **一行开关**：设一个环境变量即可启用/禁用
3. **完全向后兼容**：禁用时与原版 QwenPaw 完全一致
4. **数据隔离**：每个租户拥有独立的配置、工作区、环境变量、Provider 和 API 密钥
5. **干净合并**：上游改动集中在非核心位置，`git pull` 冲突极少

## 上游代码变更清单

### 后端

| 文件 | 变更 | 行数 | 性质 |
|------|------|------|------|
| `_app.py` | lifespan 内 2 处 `run_lifespan_hooks()` 调用 | +4 | 通用插件框架，非多租户专属 |
| `_app.py` | 模块级 `from qwenpaw_plugins import run_lifespan_hooks` | +1 | 通用插件框架 import |
| `_app.py` | 模块级激活块（环境变量判断 + activate + AuthMiddleware 重导入） | +12 | 多租户入口 |
| `agents.py` | `_resolve_workspace_base_dir()` 函数定义 | +8 | 通用扩展点，零多租户依赖 |
| `agents.py` | `create_agent` 中调用 `_resolve_workspace_base_dir()` | +2 | 替代原 14 行多租户 try/except |

### 前端

| 文件 | 变更 | 行数 | 性质 |
|------|------|------|------|
| `Header.tsx` | 条件渲染租户用户菜单 | +3 | 条件渲染 |
| `App.tsx` | 条件选择 AuthGuard + LoginPage | +6 | 条件渲染 |
| `main.tsx` | 初始化钩子 | +2 | 入口点 |
| `request.ts` | `setHandle401()` + 默认 handler | +9 | 通用扩展点 |
| `authHeaders.ts` | 委托模式 | +41 | 委托模式（已最优） |

## 插件文件清单

| 文件 | 行数 | 说明 |
|------|------|------|
| `__init__.py` | ~160 | 激活/停用生命周期 |
| `tenant_context.py` | 71 | ContextVar 租户上下文传播 |
| `token_parser.py` | ~471 | 可插拔 Token 解析器（从 CoPaw 移植） |
| `constants.py` | 97 | 常量定义 |
| `config_extension.py` | 197 | 租户感知的 load_config/save_config |
| `agents_extension.py` | ~60 | 租户感知的 workspace 基目录 |
| `auth_extension.py` | 614 | 多用户认证 + AuthMiddleware |
| `router_extension.py` | 349 | 8 个新 API 端点 |
| `manager_extension.py` | ~287 | 租户隔离的 MultiAgentManager + lifespan hook |
| `provider_extension.py` | ~570 | 按租户覆盖 API 密钥 + lifespan hook |
| `envs_extension.py` | ~150 | 环境变量租户隔离 |
| `migration_extension.py` | 152 | 工作区自动初始化 |
| `middleware.py` | 19 | 中间件工厂 |
| `qwenpaw_plugins/__init__.py` | ~96 | 通用 lifespan hook 框架 |

## 测试

运行完整测试套件：

```bash
python test_phase1_imports.py          # 模块导入测试（11 项）
python test_phase24_functional.py      # 功能测试（13 项）
```

两者都应输出 `ALL TESTS PASSED [OK]`。
