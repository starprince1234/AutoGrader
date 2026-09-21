# AutoGrader B2 执行规则（给 AI Agent）

本文件是**执行规则**，不是百科。完整文档从 [docs/README.md](docs/README.md) 进入。

## 开始任务前

1. 读 [docs/README.md](docs/README.md)（索引 + 状态表）、[docs/requirements/traceability.md](docs/requirements/traceability.md)、[docs/collaboration/workpackages.md](docs/collaboration/workpackages.md)。
2. 读 MAIN 任务书 [../MAIN/docs/workstreams/B2.md](../MAIN/docs/workstreams/B2.md) 与文档规范 [../MAIN/docs/collaboration/module-doc-spec.md](../MAIN/docs/collaboration/module-doc-spec.md)。
3. 确认契约版本：`contracts.lock` 与 `module.yaml` 的 `contract_version` **相同**（以 `module.yaml` 为准），且 `contracts.lock.digest` 与 `../MAIN/contracts` 实时摘要一致。**不要把版本号写进文档或断言**：它由 MAIN 的契约流程推进（写死会产生假失败，本模块已因此修掉两处）。
4. 检查 Git 状态：不得在脏工作区上改共享文件；确认未改 `module.yaml`、`pyproject.toml`、`contracts.lock`、`alembic/**`。

## 我负责什么 / 不负责什么

- 我拥有 `b2` schema：submission、artifact、evaluation run/result、security finding、email checkpoint；评测证据与分数；成绩导出。
- 我不拥有题目/参考答案/测试用例（属于 B3）、身份/课程/选课（属于 B4）、UI（属于 B1）。
- 我依赖 B4 的 JWT/JWKS 与课程作用域、B3 的 evaluation package，但**从不导入它们的代码**。

## 硬规则

1. **安全**：不执行 shell 字符串（启动参数固定数组）；不在沙箱外执行学生代码；禁网、只读根、临时工作区；超时必须清理整个进程树；不得以 root 运行待测代码。
2. **数据**：只读写 `b2` schema；禁止跨 schema 外键/SQL/ORM；服务端生成存储路径。
3. **日志/事件/导出**：禁止出现源码、参考答案、隐藏用例、凭据、内部绝对路径、超范围 PII。
4. **诚实**：禁止 mock 评测充当交付；静态未通过不得伪装为"运行成功"；"未检查"不得写成"安全"；失败与缺口必须写进 `docs/phases/active/`。
5. **禁止兼容层**：不留字段别名、双写、隐藏开关（见 [../MAIN/docs/contracts/change-protocol.md](../MAIN/docs/contracts/change-protocol.md)）。

## 文档同步

改代码时同步：`docs/requirements/traceability.md`（状态/证据）、`docs/phases/active/`（真实命令与结果）、`docs/api/contract.md`（仅新增/删除端点或事件关系时）、`docs/database/schema.md`（新增表或约束意图时）、`docs/testing/cases.md`（新增用例时）、`docs/README.md`（索引/状态表变更时）。

## 验证

```powershell
uv sync --all-groups
uv run ruff check .
uv run pytest
uv run alembic upgrade head
```

> `uv` **实测可用**（2026-09-16：`uv 0.10.2`，`uv run python -c` 通过），上方三条命令已用 `uv run ...`
> 真实执行并留档在 [docs/phases/active/phase-01-implementation.md](docs/phases/active/phase-01-implementation.md)
> 的"验证记录"（含未执行项及原因）。**仍不得伪报通过**：任何一条没跑就写"未执行 + 原因"。
> `uv sync --all-groups` 本轮未执行（环境已按 `uv.lock` 装好），需要重装时才跑。

## 契约变更

发现契约与实现/声明不一致时：**不**顺着实现改契约、也**不**偷偷改实现对齐；按 [../MAIN/docs/contracts/change-protocol.md](../MAIN/docs/contracts/change-protocol.md) 提交 CHG，由 MAIN 判定哪边为准。
