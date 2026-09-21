# 本地开发运行手册

本文件中的命令都在本仓库真实执行过（结果见
[../phases/active/phase-01-platform-conformance.md](../phases/active/phase-01-platform-conformance.md)）。
B5 是**插件**，没有自己的端口、没有自己的数据库：它随 MAIN 的组合宿主一起运行。

## 1. 环境变量

全部有默认值，集成部署通常一个都不用设（见 [.env.example](../../.env.example)）。
宿主按 ADR-0008 把 `B5_<NAME>` 映射为 B5 设置视图里的 `<NAME>`。

| 变量（宿主侧名） | B5 读到的名字 | 默认值 |
|---|---|---|
| `B5_CONTRACTS_DIR` | `CONTRACTS_DIR` | `<仓库>/MAIN/contracts` |
| `B5_JWKS_URL` | `B4_JWKS_URL` | `http://127.0.0.1:18000/api/v1/auth/jwks` |
| `B5_JWT_ISSUER` | `JWT_ISSUER` | `autograder` |
| `B5_JWT_AUDIENCE` | `JWT_AUDIENCE` | `autograder-api` |

> `B4_JWKS_URL` 保留 B4 的名字：密钥集是 B4 发布的公开面，B3 也按这个名字消费它。
> 另起一个名字会为同一件事造出两套约定。

## 2. 安装

```powershell
cd D:\VScodeProjects\AutoGrader\B5
uv sync
```

`uv sync` 会装上生产依赖 **和 dev 组**（`autograder-host` 与 B2/B3/B4 源码），
因为一致性套件要装载真实的组合。首次执行会下载若干包（本项目缓存已有大部分）。

## 3. 静态检查与一致性套件

```powershell
uv run ruff check .
uv run pytest -q
```

一致性套件需要 `MAIN/.env.integration`；缺失时它在**导入期**报错并说明原因（不静默跳过）。

## 4. 在集成宿主里验证端点

集成宿主由 MAIN 启动（本手册不重复其步骤，见
[MAIN 集成运行手册](../../../MAIN/docs/runbooks/integration.md)）：

```powershell
pwsh -File D:\VScodeProjects\AutoGrader\MAIN\scripts\run_host.ps1 -Port 18000
```

宿主起来后，B5 的路由出现在同一端口上：

```powershell
curl.exe -i http://127.0.0.1:18000/api/v1/platform/conformance
# → 401 + application/problem+json（没有令牌）
```

**不为了本地验证而另起一个服务**：需要在 HTTP 上打通全链路（含认证）时，用组装后的应用：

```python
import os, sys
sys.path.insert(0, r"D:\VScodeProjects\AutoGrader\MAIN\host\src")
# 先按 run_host.ps1 的方式加载 MAIN/.env.integration
from fastapi.testclient import TestClient
from autograder_host.main import create_app

client = TestClient(create_app())
print(client.get("/api/v1/platform/conformance").status_code)   # 401
```

## 5. readiness

```
GET http://127.0.0.1:18000/health/ready
```

B5 的健康项由 `B5Module.health()` 产生，内容是**契约树可读性与路径条数**
（例如 `37 contract paths under D:\VScodeProjects\AutoGrader\MAIN\contracts`）。
它不报告数据库或 NATS：B5 不连接它们。契约树不可读时该项为 `false`，并给出原因类型。

## 6. 跨模块门禁（在 MAIN 侧执行）

```powershell
cd D:\VScodeProjects\AutoGrader\MAIN
uv lock
.\.venv\Scripts\python.exe -m autograder_host.checks
```

三条 "ok" 输出即通过。治理门禁按 `glob("B*/module.yaml")` **自动**纳入 B5，无需在任何列表里登记——
这正是 ADR-0013 修掉的"装载了却逃过治理"。

> B5 必须**已安装进 MAIN 的虚拟环境**（`uv sync --extra modules`），否则门禁在
> `check_module_descriptors()` 一步会报 `cannot build descriptor from
> autograder_b5.plugin:create_module`——它按 `module.yaml` 声明的工厂真实调用一次。

## 7. 端点与路径速查

| 项 | 值 |
|---|---|
| 端点 | `GET /api/v1/platform/conformance` |
| 路由前缀（清单/描述符声明） | `/api/v1/platform` |
| 入口点 | `autograder_b5.plugin:create_module` |
| 契约文件 | `../MAIN/contracts/openapi/b5.yaml` |
| 契约锁 | [contracts.lock](../../contracts.lock) |
| 集成宿主端口 | `18000`（MAIN 的 compose 约定，B5 不新增端口） |
