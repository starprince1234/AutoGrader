# AutoGrader MAIN 项目规则（给 AI Agent）

本文件是**执行规则**，不是百科。完整知识从 [docs/README.md](docs/README.md) 进入。
遵守 [docs/collaboration/module-doc-spec.md](docs/collaboration/module-doc-spec.md) 的三条铁律。

## 开始任务前

1. 读 [docs/README.md](docs/README.md)（索引 + 权威表 + 状态表）与 [docs/requirements/rq-catalog.md](docs/requirements/rq-catalog.md) 中受影响的部分。
2. 读受影响的 [docs/workstreams/B?.md](docs/workstreams/B1.md) 与相关 [ADR](docs/adr/README.md)。
3. 确认契约现状：`contracts/` 内容 + 四份 `contracts.lock` 的版本与摘要。
4. 检查 Git 状态，保留用户未提交的修改。

## 我负责什么 / 不负责什么

- 我拥有：`MAIN/contracts/**` 的**作者身份**与版本权威；Platform SDK；composition host；集成检查；需求基线与文档治理。
- 我**不**拥有：B1–B4 的任何业务数据。MAIN 不是业务服务，不实现题库、提交、评测、用户功能。
- 我不拥有 B2/B3/B4 的物理表结构（各自迁移为唯一事实源），只维护所有权目录与 migration head。
- 我不拥有接口的**运行期**注册表（属 B4，见 [ADR-0003](docs/adr/ADR-0003-api-authority-and-runtime-registry.md)）。

## 硬规则

1. **契约先行**：任何接口改动先改 `contracts/` 并升版本，再改实现。走 [docs/contracts/change-protocol.md](docs/contracts/change-protocol.md)。
2. **摘要必须由工具计算**：契约变更后运行 `checks.py` 得到新摘要，更新**四份** `contracts.lock`。**绝不手填**摘要。
3. **单一权威**：字段定义只在 `contracts/**`；文档只写"谁提供/谁消费"，**禁止复制字段表**。表结构以各模块迁移为准，MAIN 不复制 DDL。
4. **禁止兼容层**：不留旧字段别名、双写、隐藏开关、自动降级、旧路由。破坏性变更一次性切换。
5. **需求基线三处同步**：`rq-catalog.md`、`traceability.md`、`checks.py` 的 `check_requirement_baseline()` 必须一致，**不得出现编号差集**。
6. **诚实**：不得声称未实现的能力。状态词只用 `骨架` / `待实现` / `已完成` / `阻塞`。未执行的验证必须写明原因。
7. **安全**：源码、参考答案、隐藏用例、凭据、超范围 PII **不得**进入契约、示例、fixture、日志或事件。
8. **不创建**远端、CI workflow、PR，除非用户明确要求。

## 验证（真实可执行）

```powershell
cd MAIN
# 正常环境
uv sync --all-groups --extra modules ; uv run python -m autograder_host.checks ; uv run pytest

# 若 uv 报缓存目录无法访问（D:\dev\caches\uv 在工作区之外，os error 5 = 权限被拒），
# 使用仓库内既有虚拟环境执行等价检查：
.\.venv\Scripts\python.exe -m autograder_host.checks
```

**通过判据**（四条输出）：

```text
contracts, manifests and requirements: ok (<摘要>)
module documentation governance and links: ok
migration heads and local ports agree with compose: ok
test evidence is current and green for every module at this contract: ok
```

第四条要求先跑过一次交付扫描（`python MAIN/scripts/test_evidence.py --all`）：它比对的是**各模块套件的实测记录**与当前契约版本/摘要，而记录只能由一次真实运行产生，不能手写。它同时要求**每个模块的实测运行是通过的**——一条 `exit_code != 0` 的记录是真实读数，但它意味着那条套件确实有失败，门禁照实报出，不豁免、不容忍、不把"已知失败"记成绿。

未执行或失败的检查**必须**记入 [docs/phases/active/](docs/phases/active/phase-00-initialization.md)，不得写成"应该通过"。

## 文档同步

| 改了什么 | 同步更新 |
|---|---|
| 契约 | `contracts/**` + `contracts/CHANGELOG.md` + 四份 `contracts.lock` + `docs/api/*` |
| 需求 | `rq-catalog.md` + `traceability.md` + `checks.py` + 受影响模块的追踪表与工作包 |
| 架构/边界 | `docs/architecture/*` + 新 ADR（若为不可逆决策） |
| 数据所有权 | `docs/database/ownership.md` |
| 测试与门禁 | `docs/testing/*` |
| 阶段状态 | `docs/phases/active/*`（真实命令与结果） |
| 文档增删/移动 | `docs/README.md` 索引 |

## 契约变更

发现契约与实现/文档不一致时：**不**顺着实现改契约，也不**偷偷**改实现对齐。提交 `CHG-*`（[docs/contracts/changes/](docs/contracts/changes/README.md)），由 MAIN 判定哪边是权威。

## 什么时候必须停下并上报

- 需要放宽安全策略（例如沙箱需要 `--privileged`、需要取消资源限制）。
- 需要 Linux 专属验证，但当前只有 Windows 环境。
- 环境限制使某项门禁无法执行（如 `uv` 缓存不可写）。
- 需求归属需要变更（属 P3 级，需要新 ADR + 全模块同步）。
- 需要新增付费资源、外部账号或破坏性数据操作。
