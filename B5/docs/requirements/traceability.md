# B5 需求追踪

## 0. 为什么这张表里没有 `RQ-B-*` 行

规范要求"逐 RQ 的追踪表"，而 B5 **不认领任何课程需求编号**。这不是遗漏，是一条可核验的事实，
证据是 MAIN 门禁自己的正则：

- `MAIN/host/src/autograder_host/checks.py` 的 `_REQUIREMENT = re.compile(r"`RQ-B-[0-4]-\d{2}`")`
  与 `_REQUIREMENT_OWNER = re.compile(r"^RQ-B-([0-4])-")`：允许的模块是 `B0`–`B4`。
- `check_requirement_baseline()` 的 32 条基线固定在 `RQ-B-0-10` … `RQ-B-4-99`，没有第五条业务轨道。

也就是说：**ADR-0013 开放了模块数量，却没有开放需求编号空间**。一个 `RQ-B-5-xx` 会被全部门禁
当作不存在（`_requirement_row` 不会识别它），而课程 32 条需求也不属于 B5。B5 的需求权威因此是
ADR 与 CHG 编号，而不是 `RQ-B-*`。本文件如实记录这一点，而不是编一个编号来填满表格
（那会让"32 条课程需求"这条可核对的事实失效）。

> 这是本模块发现的**规范不足**之一，已写入
> [../phases/active/phase-01-platform-conformance.md](../phases/active/phase-01-platform-conformance.md)
> 的"风险与遗留项"。修它需要 MAIN 的决定（扩 `checks.py` 的编号空间或给团队轨道加编号），
> 不在 B5 的权限内。

## 1. 约束来源 → 实现 → 证据

| 约束 | 摘要 | 本模块实现方式 | 关联工作包 | 关联测试 | 状态 | 证据位置 |
|---|---|---|---|---|---|---|
| `ADR-0013` | 模块数量开放；新模块必须**同时**被装载与被治理 | 通过普通 entry point 装载；不修改 MAIN 代码（除 CHG-0010 规定的两行依赖声明） | `WP-B5-A` | `test_composition_load.py`、`test_governance_discovery.py` | `已完成` | [phase-01](../phases/active/phase-01-platform-conformance.md) §验证记录 |
| `ADR-0013` 决策 5 | 同名 entry point 必须**响亮失败**，host 与 B4 注册表口径一致 | 由 host loader 与 B4 注册表各自实现；B5 用测试固定两者的行为 | `WP-B5-C` | `test_load_failures.py::test_two_entry_points_sharing_a_name_fail_loudly_*` | `已完成` | 同上 |
| `CHG-0010` §2.1 | 提供 `GET /api/v1/platform/conformance`，三视图比对，两条差异各自标明缺失侧 | `src/autograder_b5/conformance.py` + `api.py` | `WP-B5-A` | `test_conformance_endpoint.py`、`test_conformance_views.py` | `已完成` | 同上 |
| `CHG-0010` §5.1 | `discovered_modules()` 返回五个模块 | 无代码；治理门禁按 `glob("B*/module.yaml")` 自动纳入 | `WP-B5-B` | `test_governance_discovery.py::test_the_gate_discovers_five_modules` | `已完成` | 同上 |
| `CHG-0010` §5.2 | `module_schema_owners()` **不**包含 B5 | `module.yaml: database.schema: none` | `WP-B5-B` | `test_governance_discovery.py::test_b5_is_not_a_database_schema_owner` | `已完成` | 同上 |
| `CHG-0010` §5.3 | host `_load_modules()` 装载五个模块且顺序满足 capability DAG | descriptor 声明 `requires` | `WP-B5-B` | `test_composition_load.py`（含"非字母序"反例） | `已完成` | 同上 |
| `CHG-0010` §5.4 | 破坏 B5 的清单/能力声明时，装载与门禁**各自**报错 | 无代码；由真实 loader 与真实门禁的行为保证 | `WP-B5-C` | `test_load_failures.py`（5 例） | `已完成` | 同上 |
| RQ-B-0-20（全体；**非本模块所有**） | 工具链/语言/运行环境符合课程文档 | Python 3.12 + FastAPI + uv，与 MAIN 基线一致；B5 **不声明**它的状态（所有者是 MAIN） | `WP-B5-A` | `checks.check_module_descriptors()`（版本一致性） | 引用 | `../../MAIN/docs/requirements/rq-catalog.md` §1 |
| RQ-B-4-40（B4 所有；**非本模块所有**） | 运行期模块注册表与三方一致性 | 不实现。B4 在装载期用 `validate_registry()` 校验 B5 的 `module.yaml` / descriptor / 锁；B5 的正确回应是"清单与描述符逐字段一致" | `WP-B5-B` | `test_governance_discovery.py::test_b5s_lock_is_the_live_contract_digest`，以及组装应用时 B4 的校验 | 引用 | `../../MAIN/docs/adr/ADR-0003-api-authority-and-runtime-registry.md` |
| `MAIN/docs/collaboration/module-doc-spec.md` | 三条铁律 + 必须存在的文档集合 | 本 `docs/` 全量文件；由 `checks.check_module_governance()` 强制 | `WP-B5-B` | `test_governance_discovery.py::test_b5s_documentation_satisfies_the_module_governance_gate` | `已完成` | 同上 |

> **上表两条 `RQ-B-*` 行故意不加反引号**：`checks._requirement_row()` 把"表格第一格里的
> 反引号编号"判定为一次**需求声明**，随后要求该行恰好含一个状态词。B5 不是这两条需求的所有者，
> 声明它们会导致 `aggregated_requirement_statuses()` 冲突或 `module_requirement_statuses()` 抛错。
> 不声明、只引用，才是这里唯一正确的写法。

## 2. 状态词汇

只使用 `骨架` / `待实现` / `已完成` / `阻塞`。

- `已完成` 的含义：有可运行实现 **且** 有真实执行过的测试 **且** 阶段文档记录了命令与结果。
- `引用` 不是状态词，只用于本表第 1 节中"该需求不属于 B5、我不声明其状态"的行；B5 不为
  别人的需求填写状态，否则会与所有者冲突（`aggregated_requirement_statuses()` 会直接抛错）。
- 本模块**没有** `待实现` / `阻塞` 的课程需求行；B5 自身的未完成项记录在
  [../README.md](../../README.md) §已知限制与 [../docs/README.md](../README.md) 状态表。
