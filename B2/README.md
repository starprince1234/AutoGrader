# AutoGrader B2（评测模块）

B2 是整个 AutoGrader 系统的**分数与执行证据唯一权威**：接收待测代码（API / 邮件）、存储管理、静态合法性检查、安全性确认、nsjail 动态执行、生成可解释可重放的逐项证据、成绩与报告持久化、按格式导出；另有一条**独立于分数轨**的教学评语轨（`RQ-T-20`）与一套**沙箱自证**入口（`ADR-0010`）。

**当前状态：`已完成`**（2026-09-16 按实现刷新；2026-09-18 补评语轨与逐用例证据）。九条需求
`RQ-B-2-10..99` 均为 `已完成`，判词口径 = 可运行实现 + 真实依赖测试 + 阶段证据，逐行见
[docs/requirements/traceability.md](docs/requirements/traceability.md)（该表另含 `RQ-T-20` 与
`RQ-B-0-30` 验收 2 两条落到本模块的需求）。
**仍有未验收项**（不因 `已完成` 而消失）：真实 IMAP/TLS 仅夹具、跨进程并发上限未测、
容器内"沙箱不可用 ⇒ 拒绝执行"未验、真实沙箱资源账本未实测（本批把读数写进证据，
但**真实 nsjail 下的取值仍未在 Linux 上验收**）、金丝雀在真实内核上的跑批未执行——逐条见
[docs/phases/active/phase-01-implementation.md](docs/phases/active/phase-01-implementation.md) 与
[docs/phases/active/phase-03-commentary-and-per-case-evidence.md](docs/phases/active/phase-03-commentary-and-per-case-evidence.md)。

## 已实现能力

- 插件通过 entry point `autograder.modules → b2` 注册到 MAIN host（见 `pyproject.toml`）。
- `B2Module.descriptor` 声明模块名 `B2`、契约版本（见 `module.yaml`，**本文件不写死版本号**）、
  `provides/requires/routes/events`。
- **9 条端点真实注册**（`src/autograder_b2/plugin.py` 拼 3 个路由族）：
  `POST /api/v1/submissions`、`GET /api/v1/submissions/{submission_id}`、
  `GET /api/v1/evaluation-runs/{evaluation_run_id}`、`…/events`（SSE）、`…/report`、
  `GET`/`POST …/commentary`（评语轨）、
  `GET /api/v1/grades`、`GET /api/v1/grades/export`（与 `MAIN/contracts/openapi/b2.yaml` 逐条对账，
  差集只允许是显式登记的另一批端点——见 `tests/test_b2_routes.py::_PENDING_CONTRACT_PATHS`）。
- `health()` 委托 `runtime.health_summary()` 返回真实健康详情（**不再**返回 `business evaluator pending` 占位文案）。
- 接收（API + IMAP 邮件）、内容寻址存储与孤儿回收审计、静态合法性检查、安全判定、nsjail 动态执行、
  报告与成绩持久化、XLSX/CSV 导出（防公式注入）均已实现并有真实依赖用例。
- **教学评语轨**（`RQ-T-20`）：默认关闭（未配置 provider 时 `GET` 返回 `unavailable`、`POST` 返回 `409`，
  且没有出网调用点）；开启后只从**字段级白名单**的已落库证据生成，`input_scope` 随 provenance 返回；
  评语有自己的表与端点，清空它不影响分数与可重放性。
- **逐用例证据补全**（`CHG-0012`）：资源实测读数（读不到就是 `null`，绝不写 0）、文件变更清单与内容摘要
  （补上 `RQ-B-0-30` 验收 2 这个此前无人实现的缺口）、规则推导的解释文本（模型输出进不了这里）。
- **沙箱自证**：`uv run python -m autograder_b2.canary` 跑六项必须失败的金丝雀作业，逐项给出
  `发现风险 / 未发现风险 / 未检查`；**本机（Windows）全部是 `未检查`**，退出码 3 而不是 0。
- Alembic 迁移链 `20260915_0001..0011`（head `20260915_0011`），业务表全部创建。
- 契约锁 `contracts.lock` 指向 `MAIN/contracts` 的 SHA-256 摘要（版本号见该文件）。

## 明确非目标（本模块不做）

- **不拥有**题目、参考答案、测试用例（属于 B3）；身份、课程、选课（属于 B4）；UI（属于 B1）。
- **不**直接读取 `b3`/`b4` schema，不 import B3/B4 的 ORM 或 Python 包。
- **不**执行用户提供的 shell 字符串（启动参数必须是固定数组）。
- **不**把源码 / 参考答案 / 隐藏用例 / 凭据写入日志、事件或导出。
- **不**用 mock 评测充当交付物。

## 在系统中的位置

```text
B1 static UI ── HTTP/SSE ── MAIN host
                              ├── B4 identity/course ── PostgreSQL b4
                              ├── B3 question-bank ──── PostgreSQL b3
                              └── B2 evaluation ─────── PostgreSQL b2 ── nsjail
                                                         └── NATS JetStream
```

B2 只经版本化 HTTP / 事件契约与邻居交互，从不导入它们的代码（见 [docs/architecture/overview.md](docs/architecture/overview.md)）。

## 前置条件

- Python `>=3.12,<3.13`；包管理 `uv`；代码检查 `ruff`；测试 `pytest`。
- 数据库：PostgreSQL 17（`b2` schema，最小权限 login role `autograder_b2`）。
- 消息：NATS JetStream 2.10。
- 动态执行验收依赖 **Linux + cgroup v2 + nsjail**（Windows 仅用于开发与契约/单元测试）。
- 环境变量见 [.env.example](.env.example)：`DATABASE_URL`、`NATS_URL`、`NSJAIL_PATH`。

## 安装 / 迁移 / 启动 / 验证

> `uv` **实测可用**（2026-09-16：`uv 0.10.2`），下列命令均以 `uv run ...` 真实执行过，读数与未执行项见
> [docs/phases/active/phase-01-implementation.md](docs/phases/active/phase-01-implementation.md) 的"验证记录"。
> **更正**：本文件此前写"本机沙箱无法写入 `uv` 缓存目录…尚未执行验证"，已过期。
> `uv sync --all-groups` 本轮**未执行**（环境已按 `uv.lock` 装好），需要重装时才跑。

```powershell
# PowerShell（Windows 开发）
uv sync --all-groups
uv run ruff check .
uv run pytest
uv run alembic upgrade head
```

```bash
# Linux（验收环境）
uv sync --all-groups
uv run ruff check .
uv run pytest
uv run alembic upgrade head
```

## 开发者 / 用户入口

- 本模块**提供**的 API 前缀：`/api/v1/submissions`、`/api/v1/evaluation-runs`、`/api/v1/grades`
  （`/api/v1/intake` 由 CHG-0014 的隔离区复核工作项实现，本批未做）；
  **9 条端点已真实注册**（`plugin.py` 拼 3 个路由族，与 `MAIN/contracts/openapi/b2.yaml` 逐条对账），
  路由与作用域清单见 [docs/api/contract.md](docs/api/contract.md)。**更正**：此前写"尚未注册真实路由，
  `router()` 返回空 `APIRouter()`"，已过期。
- 本模块**提供**的 capability：`b2-submissions`。
- **运维 CLI**：`uv run python -m autograder_b2.worker`（推进评测 + 排空 outbox）、
  `uv run python -m autograder_b2.canary`（沙箱自证，见上）。
- 无 HTML 路由。

## 文档入口

全部文档从 [docs/README.md](docs/README.md) 进入（唯一索引：权威表 + 状态表）。

## 已知限制（真实，不含未实现声称）

> 2026-09-16 按实现刷新：下列 1/2/4/5 条原为骨架阶段的过期表述（与本文件正文及 `docs/README.md` 状态表矛盾），
> 已更正；3 条保留（Linux 侧已用特权容器完成验收，见 [tests/linux/README.md](tests/linux/README.md)）。

1. **真实依赖未验收项**：真实 IMAP/TLS 仅夹具；跨进程并发上限未测；容器内"沙箱不可用 ⇒ 拒绝执行"未验；
   真实沙箱的**资源账本**（≤96 MB RSS / 0.45 vCPU）已把读数写进证据，但**真实 nsjail 下的取值**
   仍只能在 Linux 容器里验收（本机读数路径全为 `null`，这是如实呈现而不是"通过"）。逐条见 phase-01
   与 phase-03 的"未执行项"。
2. **`descriptor.events` 与 `module.yaml` 的一致性已解决**：`descriptor.events` 现为 `module.yaml` 声明的
   全部 6 个 subject（由 `tests/test_plugin.py::test_descriptor_events_equal_module_yaml_publishes_and_subscribes`
   钉住）；`event_id` v5 vs v4 等**契约侧**差异另见 `MAIN/docs/contracts/changes/CHG-0001..0005`（待 MAIN 裁定）。
   剩余限制只是 SDK 的扁平元组无法表达方向，不阻塞。
3. **nsjail 仅 Linux 可验收**：动态执行与资源账本实测无法在 Windows 完成（Windows 上 `run_case` 走
   "沙箱不可用 ⇒ 拒绝执行"路径）。Linux 侧已在特权容器内完成：隔离矩阵 `13 passed, 1 skipped, 1 xfailed`、
   真实 runner 验收 `5 passed, 1 skipped`、宿主驱动模式 `1 passed, 14 skipped`。
4. **迁移已到 head**：`module.yaml: database.migration_head` = `20260915_0011`，迁移链 `0001..0011`，
   业务表全部创建（`0010` 建评语表、`0011` 给运行表补三态安全结论列）；**基线 0001 的 `downgrade` 缺陷**
   （`DROP SCHEMA` 无 `CASCADE` ⇒ `downgrade base` 必失败）已登记为 phase-01 缺口第 36 条。
   **部署提醒**：`EXPECTED_SCHEMA_REVISION` 是 readiness 判据，因此升级本构建前必须先跑
   `uv run alembic upgrade head`；库停在上一个 revision 时评测/成绩/评语路由会按设计 503。
5. **`uv` 已实测可用**（见上方"安装 / 迁移 / 启动 / 验证"）。
