# 测试策略

## 1. 分层与目标

| 层 | 目标 | 本模块的形态 |
|---|---|---|
| 比对逻辑（纯函数） | 三视图判定在**每一条**规则上都可证伪 | `tests/test_conformance_views.py`，构造契约目录与 descriptor，无网络无框架 |
| 装载与组合（集成） | 真实 loader 能装载真实模块；违规组合**响亮失败** | `tests/test_composition_load.py`、`tests/test_load_failures.py` |
| 治理门禁（契约/治理） | 治理门禁确实纳入了 B5，且 B5 的清单/描述符/锁/文档全部合规 | `tests/test_governance_discovery.py`（调用 MAIN 的真实门禁函数） |
| 接口（HTTP） | 端点对**组装后的应用**的真实行为，响应体符合契约 schema | `tests/test_conformance_endpoint.py`（`TestClient`） |
| 端到端（真端口） | — | **未做**，见 §5 |

分层原则：**不在低层重复高层的断言**。例如"报告是否 `consistent`"只在接口层断言一次，
比对层只断言"哪一条规则产生哪种差异"。

## 2. 每层覆盖的风险

| 风险 | 覆盖它的用例 |
|---|---|
| 前缀与全路径被按字面比较（MAIN 曾因此报 27 处假不一致） | `test_a_full_path_under_a_declared_prefix_is_not_a_difference` |
| 宿主自己的路由被误报为"契约未描述" | `test_a_host_route_outside_the_contract_is_not_a_difference` |
| 报告只给计数、不给缺失侧 | 每个差异用例都断言 detail 同时含**来源**与**对象** |
| 装载顺序碰巧正确（字母序） | `test_b5_is_placed_by_capability_rather_than_by_name`（探针 `B0` 排在 `B5` 之后） |
| 破坏性输入被静默接受 | `test_load_failures.py` 的 5 个用例 |
| 未认证被放行 / 令牌被当成已认证 | 接口层 4 个认证用例 |
| 响应体与契约漂移 | 接口层用 `b5.yaml` / `common.yaml` 的 schema 校验响应（**不**手抄期望字段） |
| B5 悄悄逃过治理（ADR-0013 记录的静默路径） | `test_governance_discovery.py` 的四项门禁调用 |

## 3. 真实基础设施的要求

- **不 mock 交付路径**：装载用的是真实 `autograder_host.main._load_modules()`；模块来自真实的
  `autograder.modules` entry point；比对用的是真实的契约文件；HTTP 用的是真实组装的应用。
- **允许替换的外部服务**：只有 B4 的 JWKS **端点**（`conftest.issuer`，一个本地 HTTP 夹具服务器，
  每次会话一枚新的 Ed25519 密钥）。替换的是"一个有状态的不可控外部服务"，不是校验逻辑本身：
  `kid` 选键、Ed25519 验签、`iss`/`aud`/`exp` 校验全部真实执行。
  这与 [MAIN 的测试策略](../../../MAIN/docs/testing/strategy.md) 一致。
- **不使用 SQLite 代替 PostgreSQL**：本模块不接触任何数据库，因此这条对 B5 不适用（不是放宽）。
- **不起第二个服务**：不启动 uvicorn，不绑定业务端口；需要 HTTP 时用 `TestClient` 打组装后的 app。
  唯一的端口是 JWKS 夹具服务器的临时端口（`127.0.0.1:0`，随夹具关闭）。

## 4. 门禁命令

```powershell
cd B5
uv sync                 # 生产依赖 + dev 组（host 与 B2/B3/B4 用于一致性套件）
uv run ruff check .
uv run pytest -q
```

```powershell
cd ../MAIN
uv lock                                       # 依赖解析（含 CHG-0010 增加的两行）
.\.venv\Scripts\python.exe -m autograder_host.checks
.\.venv\Scripts\python.exe -m pytest -q
```

## 5. 依赖环境与"拒绝收集"的取舍

一致性套件需要**两样本仓库之外的东西**：

1. `MAIN/.env.integration`（宿主环境变量：数据库 DSN、B4 私钥路径、模块根目录）；
2. 已同步的 dev 环境（`autograder-host` 与 B2/B3/B4 的 entry point）。

缺任何一样，`tests/conftest.py` 在导入期**直接报错**，而不是 `skip`。
这是有意的：一个被跳过的一致性检查与一个通过的一致性检查在报告里长得一样，
而"即插即用是否成立"正是本项目最不该被静默跳过的问题。代价是套件不能在没有集成环境
的机器上收集——这一代价记录在此，而不是用 `pytest.skip` 掩盖。

## 6. 当前**不能**执行的检查（及原因）

| 检查 | 原因 |
|---|---|
| 真实端口上的端到端（uvicorn + 真 B4 登录令牌） | 会与正在运行的服务争用端口；且需要 B4 的私钥签发令牌，测试不应接触私钥。改用组装后的应用 + 夹具 issuer |
| 跨机独立部署（只有 B5 与 MAIN 的机器） | 未演练。`CONTRACTS_DIR` 默认值经 SDK 的 `deployment_root()` 解析（`B4_MODULE_ROOT` + 校验到真实契约树），已安装形态不再依赖仓库布局；跨机仍需部署演练 |
| 性能/资源基准 | 契约路径 37 条、报告 KB 级，未做基准；不做没有风险的基准 |
| 覆盖率门槛 | 未设门槛。覆盖率不是本模块的风险（逻辑分支少且被逐条断言），设一个数字只会催生无意义用例 |
