# B5 — 平台一致性审计模块

**一句话定位**：B5 比较**三个视图**——MAIN 契约声明的路径、宿主（MAIN host）自己服务的平台级路由、
各已装载模块在 descriptor 里声明的路由——并把每一处差异连同**缺失的一侧**报告出来。

**当前状态**：`已完成`。代码、测试与阶段证据见
[docs/phases/active/phase-01-platform-conformance.md](docs/phases/active/phase-01-platform-conformance.md)。
本模块存在的理由不是"多一个端点"，而是把项目的头条目标（PRD §8.6 / ADR-0013）从**声称**变成**证据**：
B5 是约定冻结**之后**照着约定写的第一个模块，它能否不改 MAIN 一行被装载、被治理、被 B4 注册表校验，
就是那条判据本身。

## 已实现能力

- 插件注册：`module.yaml` + `ModuleDescriptor` + 工厂 `autograder_b5.plugin:create_module`。
- `GET /api/v1/platform/conformance`：三视图比对报告（Bearer 认证，失败 `401` Problem Details）。
- 本地验签：按 `kid` 从 B4 发布的 JWKS 取公钥，Ed25519（EdDSA）验签 `iss`/`aud`/`exp` 等注册声明。
- 部署健康：`/health/ready` 中 B5 的健康项报告"契约树是否可读"，而不是恒定健康。

## 明确非目标

- **不写任何东西**：无数据库、无迁移、无事件、不写文件。它只读契约文件与 `app.state`。
- **不做运行期注册表**：模块清单与三方一致性校验属 B4（`RQ-B-4-40`，ADR-0003）。B5 报告的是
  *运行中的组合与契约* 的差异，两者问的不是同一个问题，也不互为替代。
- **不替代 MAIN 的静态门禁**：`autograder_host.checks` 在**构建期**失败；B5 在**运行期**返回报告。
  门禁必须红，报告必须能读——这是有意的不对称。
- **不做修复**：报告只说明差异，不改契约、不改模块、不自动对齐。

## 在本系统中的位置

```text
                    ┌──────────────────────────────┐
                    │  MAIN host (create_app)      │
   entry points ───▶│  _load_modules() → DAG 顺序  │
                    └───────┬──────────────────────┘
        ┌───────────┬───────┴────────┬───────────────┐
        ▼           ▼                ▼               ▼
       B4          B3               B2              B5
   身份/课程    题库/版本       提交/评测/成绩   一致性报告
        │           │                │               │
        └───────────┴────────────────┴───────────────┘
                                                读 ▲
                    MAIN/contracts/openapi/*.yaml ──┘
```

B5 依赖 B2/B3/B4 的**能力**（不是代码）：报告是"关于这个组合"的，缺了任何一个业务模块，
契约里属于它的路径都会被报成"无人服务"——那不是更小的报告，而是**错的**报告。

## 前置条件

- Python `>=3.12,<3.13`；`uv`。
- 集成运行需要 `MAIN/.env.integration`（一致性套件与跨模块验证都要它，见
  [docs/runbooks/local-development.md](docs/runbooks/local-development.md)）。
- 运行期变量（见 [.env.example](.env.example)）：`CONTRACTS_DIR`、`B4_JWKS_URL`、`JWT_ISSUER`、
  `JWT_AUDIENCE`。`CONTRACTS_DIR` 的默认值经 SDK 的 `deployment_root()` 解析 —— 读
  `B4_MODULE_ROOT`（部署根，同时被 B4 的注册表与 B2 的契约读取使用），并**校验**该目录下确实
  存在 `MAIN/contracts`；只有从源码树导入时才回退到相对路径推导。

## 安装 / 校验 / 运行

```powershell
# Windows PowerShell
cd B5
uv sync                 # 生产依赖 + dev 组（dev 组装入 host 与 B2/B3/B4，用于一致性套件）
uv run ruff check .
uv run pytest -q        # 一致性套件：真实 loader、真实 entry point、五模块装载与四个负例
```

```bash
# Linux
cd B5
uv sync
uv run ruff check .
uv run pytest -q
```

跨模块门禁（治理门禁会**自动**纳入 B5，无需登记）：

```powershell
cd ../MAIN
uv lock
.\.venv\Scripts\python.exe -m autograder_host.checks
```

不单独起服务：需要在 HTTP 上验证端点时，用 FastAPI `TestClient` 打
`autograder_host.main.create_app()` 组装出的应用（见
[docs/testing/strategy.md](docs/testing/strategy.md)），不要另起端口。

## 开发者入口

- 端点：`GET /api/v1/platform/conformance`（前缀 `/api/v1/platform`，见 [module.yaml](module.yaml)）
- 插件工厂：`autograder_b5.plugin:create_module`
- 报告的三视图实现：`src/autograder_b5/conformance.py`

## 已知限制（真实，不是待办清单）

1. **B5 没有 `RQ-B-*` 编号可认领**：`checks.py` 的需求编号模式是 `RQ-B-[0-4]-\d{2}`，
   课程 32 条需求也没有 B5 的所有者。B5 的需求权威是 ADR-0013 与 CHG-0010（见
   [docs/requirements/traceability.md](docs/requirements/traceability.md)）。
2. **`MAIN/scripts/relock_contracts.py` 仍写死四个模块**（`("B1","B2","B3","B4")`），
   它算得出摘要却写不到 B5 的锁；本仓库的 B5 锁由该工具算出的摘要 + 逐字节相同的模板得到，
   并由 `check_manifests()` 复核。
3. **验签需要 B4 可达**（loopback HTTP 的 JWKS）：读不到密钥集时 B5 **fail closed**（`401`），
   不会放行未验证的调用者。
4. **未做跨模块端到端**：一致性套件在进程内组装应用，没有对着公网端口做 e2e；视频/演示脚本不在范围内。
5. 一致性套件的**负例**里有一条（B4 注册表的同名 entry point）直接调用了 `autograder_b4.registry`，
   没有走 host loader——这是为了让"两处都必须响亮失败"这句话被真正检查。

## 文档入口

→ [docs/README.md](docs/README.md)（权威表 + 状态表，唯一索引）
