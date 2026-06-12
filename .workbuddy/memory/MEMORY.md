# Project Memory

- 本项目 `QwenPawTeam` 是基于开源项目 `agentscope-ai/QwenPaw` 二次开发的多用户版本。上游 remote 为 `upstream`，多用户版本 remote 为 `origin`。
- 上游功能适配总原则：尽量不改动 upstream 主体代码，以最小改动完成；优先使用 `plugins/bundle/multi-user/` 插件系统、上游 `PluginApi` 注册、monkey patch、前端 `console/src/multi_user/` 请求扩展/鉴权包装，以及少量必要接入点实现多用户适配。
- 多用户保护边界：`plugins/bundle/multi-user/`、`console/src/multi_user/`，以及 upstream 接入点 `src/qwenpaw/app/_app.py`、`src/qwenpaw/app/routers/agents.py`、`src/qwenpaw/app/routers/console.py`、`src/qwenpaw/app/workspace/workspace.py`、`src/qwenpaw/app/routers/auth.py`、前端 `console/src/App.tsx`、`console/src/api/request.ts`、`console/src/api/authHeaders.ts`、`console/src/layouts/Header.tsx`。
- 合并上游前必须保护当前未提交改动；不要覆盖 `.env`，不要删除 `.workbuddy/`。
- ✅ 合并已完成（commit 3204ee3d），上游 87 个 commits 已合入。
- 多用户插件已迁移为上游新格式：`plugins/bundle/multi-user/plugin.json` + `plugin.py`，使用 `api.register_startup_hook()` / `api.register_shutdown_hook()` / `api.register_http_router()` 替代旧的手动激活。
- `_app.py` 保留约 20 行早期 AuthMiddleware patch（因为 middleware 在模块级注册，早于插件加载），通过 `sys.path` 从 `plugins/bundle/multi-user/` 直接导入 `patch_auth_module`。
- 旧目录 `src/qwenpaw_plugins/` 已完全删除，代码无重复。唯一来源是 `plugins/bundle/multi-user/`。
- `_app.py` 添加了 `plugins/bundle/` 到 plugin_dirs 扫描路径，使 PluginLoader 自动发现多用户插件。
- 前端 `request.ts` 保留了 `setHandle401` 机制和 `buildAuthHeaders` from `../multi_user/authHeaders`。
- `router_extension.py` 中 APIRouter 不再有 prefix（由 `register_http_router(prefix="/auth")` 统一处理）。
- 插件内使用直接导入（`from constants import ...`），非相对导入（`from .constants import ...`），因为 PluginLoader 通过 `sys.path` 加载。
