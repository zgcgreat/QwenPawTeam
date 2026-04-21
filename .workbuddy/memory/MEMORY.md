# QwenPaw 项目记忆

## 项目概况
- QwenPaw: 基于 FastAPI 的插件化 LLM 应用
- 多用户架构通过 `qwenpaw_plugins/multi_user/` 插件实现，使用 wrapper + monkey-patching 模式扩展上游单用户系统

## 多用户架构 (2026-04-21 更新)
- **隔离字段可配置**: 通过 `QWENPAW_USER_FIELDS` 环境变量配置用户隔离字段（默认 `username`）
- **标签可配置**: 通过 `QWENPAW_USER_FIELD_LABELS_ZH/EN` 环境变量配置中英文标签映射（JSON）
- **单点配置**: `constants.py` 是唯一真相源，所有其他文件动态引用 `USER_FIELDS`
- **默认字段**: 不配置环境变量时默认使用 `username: 用户名` 进行隔离
- **动态 Pydantic 模型**: router_extension.py 使用 `create_model()` 动态构建请求/响应模型
- **前端动态表单**: LoginPage.tsx 从 `/auth/status` 获取字段配置动态渲染
- **响应格式转换**: authApi.ts 的 `_transformResponse` 将后端 flat 响应转为 `UserInfo { fields: Record<string,string> }`

## 关键中间件
- `AuthMiddleware`: HMAC token 验证 + 多用户路由
- `agent_scoped` / X-Agent-Id header: 智能体级别隔离
- `_current_user_id` ContextVar: 异步链路用户传播

## 用户偏好
- 中文交流，提问简练直接
- 倾向快速定位根因直接给修复方案
- 代码修改尽量非侵入式，避免改动原始代码
- 要求方案准确性以减少返工

## 重命名记录 (2026-04-21)
- 项目从 `multi_tenant` 重命名为 `multi_user`
- 目录 `src/qwenpaw_plugins/multi_tenant/` → `src/qwenpaw_plugins/multi_user/`
- 目录 `console/src/multi_tenant/` → `console/src/multi_user/`
- 环境变量 `QWENPAW_MULTI_TENANT_ENABLED` → `QWENPAW_MULTI_USER_ENABLED`
- 环境变量 `VITE_MULTI_TENANT_ENABLED` → `VITE_MULTI_USER_ENABLED`
- 环境变量 `QWENPAW_TENANT_FIELDS` → `QWENPAW_USER_FIELDS`
- 组件名 `MtLoginPage` → `MuLoginPage`
- 组件名 `MtAuthGuard` → `MuAuthGuard`
- 组件名 `MtHeaderUserMenu` → `MuHeaderUserMenu`
- API `mtAuthApi` → `muAuthApi`
- API `/auth/resolve-tenant` → `/auth/resolve-user`
- 测试文件 `test_multi_tenant.py` → `test_multi_user.py`
- i18n 文件 `tenant.json` → `user.json`
