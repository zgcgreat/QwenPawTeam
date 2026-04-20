# 多租户插件对原始代码的入侵分析

> 目标：识别所有对 `qwenpaw/` 和 `console/src/`（非插件目录）的修改，
> 评估哪些可以进一步移入插件目录，以最小化上游合并冲突。

---

## 一、后端修改清单（`qwenpaw/` 目录）

### 1. `qwenpaw/app/_app.py` — **已优化为通用插件机制**

**修改内容（优化后）：**

| 行号 | 修改 | 说明 |
|------|------|------|
| 254-258 | lifespan 内 `run_lifespan_hooks("post_manager_init", ...)` | 通用插件钩子，非多租户专属 |
| 266-269 | lifespan 内 `run_lifespan_hooks("post_provider_init", ...)` | 通用插件钩子，非多租户专属 |
| 493-494 | 模块级 `from qwenpaw_plugins import run_lifespan_hooks` | 通用插件框架 import |
| 496-508 | 模块级插件激活 | `if QWENPAW_MULTI_TENANT_ENABLED → activate_multi_tenant(app) + 重新导入 AuthMiddleware` |

**lifespan 内仅 4 行通用钩子代码**（`run_lifespan_hooks` 不引用任何多租户模块），模块级激活块约 12 行。

**优化结果：**
- ✅ lifespan 中的多租户专属代码已完全移除，替换为通用 `run_lifespan_hooks()` 调用
- ✅ Manager/Provider wrap 逻辑通过 lifespan hooks 机制移入插件

---

### 2. `qwenpaw/app/routers/agents.py` — **已优化为可 monkey-patch 的函数**

**修改内容（优化后）：**

```python
# 第 40-47 行：新增可 patch 的函数
def _resolve_workspace_base_dir() -> Path:
    """Return the base directory for new agent workspaces.

    Plugins (e.g. multi-tenant) may monkey-patch this function at
    module level to return a tenant-specific directory instead of
    the global ``WORKING_DIR``.
    """
    return WORKING_DIR

# 第 323-324 行：create_agent 中简化调用
_base_dir = _resolve_workspace_base_dir()
```

**优化结果：**
- ✅ 原 14 行多租户 try/except 代码已完全移除
- ✅ 新增 8 行通用函数 + 2 行调用 = 净增 10 行，但**零多租户 import/依赖**
- ✅ 插件 `agents_extension.py` 通过 monkey-patch 替换该函数实现租户感知

---

## 二、前端修改清单（`console/src/` 目录）

### 1. `console/src/layouts/Header.tsx` — 用户菜单

**修改内容：**
```tsx
import { MULTI_TENANT_ENABLED } from "../multi_tenant";
import MtHeaderUserMenu from "../multi_tenant/HeaderUserMenu";
{MULTI_TENANT_ENABLED && <MtHeaderUserMenu />}
```

**约 3 行**。❌ 已最小。

---

### 2. `console/src/App.tsx` — 路由级条件渲染

**修改内容：**
```tsx
import { MULTI_TENANT_ENABLED } from "./multi_tenant";
import MtLoginPage from "./multi_tenant/LoginPage";
import MtAuthGuard from "./multi_tenant/AuthGuard";
const ActiveAuthGuard = MULTI_TENANT_ENABLED ? MtAuthGuard : UpstreamAuthGuard;
MULTI_TENANT_ENABLED ? <MtLoginPage /> : <LoginPage />
```

**约 6 行**。❌ 已最小。

---

### 3. `console/src/main.tsx` — 初始化钩子

**修改内容：**
```tsx
import { initializeMultiTenant } from "./multi_tenant";
initializeMultiTenant();
```

**约 2 行**。❌ 无法移走。

---

### 4. `console/src/api/request.ts` — 401 处理

**约 18 行**。⚠️ 部分可优化。

---

### 5. `console/src/api/authHeaders.ts` — 认证头桥接

**约 41 行**。❌ 已是最优委托模式。

---

## 三、总结

### 优化前后对比

| 文件 | 优化前 | 优化后 | 改善 |
|------|--------|--------|------|
| `_app.py` | ~17 行多租户专属代码 | ~4 行通用钩子 + ~8 行激活 | ✅ lifespan 内零多租户代码 |
| `agents.py` | ~14 行多租户 try/except | ~8 行通用函数 + 2 行调用 | ✅ 零多租户 import |
| `Header.tsx` | ~3 行 | ~3 行 | ❌ 已最小 |
| `App.tsx` | ~6 行 | ~6 行 | ❌ 已最小 |
| `main.tsx` | ~2 行 | ~2 行 | ❌ 已最小 |
| `request.ts` | ~18 行 | ~18 行 | ⚠️ 待优化 |
| `authHeaders.ts` | ~41 行 | ~41 行 | ❌ 已最优 |

### 核心优化成果

1. **`_app.py` lifespan**：多租户 wrap 代码 → 通用 `run_lifespan_hooks()` 钩子机制
2. **`agents.py`**：多租户 try/except → 可 monkey-patch 的 `_resolve_workspace_base_dir()` 函数
3. **新增文件**：`agents_extension.py`（插件内，提供租户感知的 `_resolve_workspace_base_dir` 实现）

### 上游合并影响

- `_app.py`：lifespan 中 2 处 `run_lifespan_hooks()` 调用是**通用插件框架**的一部分，不引用任何多租户模块，上游不太可能修改这些位置
- `agents.py`：`_resolve_workspace_base_dir()` 是**通用扩展点**，上游可以保留或移除，不影响核心逻辑
- 模块级激活块（`QWENPAW_MULTI_TENANT_ENABLED` 判断）位于文件末尾，与上游代码无冲突
