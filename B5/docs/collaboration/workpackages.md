# 工作包拆分（2～3 人并行）

B5 的交付面比业务模块小，因此切成 **3 个可并行认领**的工作包：一条"报告本身"、一条"规范合规"、
一条"证据与负例"。三者的接口在开工前就已冻结（见 §并行前提），因此可以同时进行。

## WP-B5-A 端点与比对

| 项 | 内容 |
|---|---|
| 覆盖验收 | AC-04 ~ AC-07、AC-13 |
| 覆盖约束 | CHG-0010 §2.1；ADR-0013（不改 MAIN 代码） |
| 目标 | `GET /api/v1/platform/conformance` 返回三视图差异报告；Bearer 本地验签 |
| 输入 | `MAIN/contracts/openapi/b5.yaml`（唯一字段权威）、SDK `protocol.py` |
| 产出 | `src/autograder_b5/{api,conformance,contracts,auth,config,errors}.py`、`tests/test_conformance_endpoint.py`、`tests/test_conformance_views.py` |
| 依赖 | 契约已冻结（CHG-0010 落地 `b5.yaml`；版本与摘要见 [contracts.lock](../../contracts.lock)） |
| 验收 | `uv run pytest -q` 通过；响应体通过 `b5.yaml` schema 校验；未认证返回 `401` |
| 预估 | 1.5～2 人日 |
| 认领人数 | 1～2 |

## WP-B5-B 规范合规与治理

| 项 | 内容 |
|---|---|
| 覆盖验收 | AC-01、AC-03、AC-14 |
| 覆盖约束 | ADR-0013；`MAIN/docs/collaboration/module-doc-spec.md` |
| 目标 | 清单/描述符/锁/文档全部合规，且治理门禁**自动**纳入 B5 |
| 输入 | 契约摘要（由 `relock_contracts.py` 计算）、文档规范第 2 节的必须文件清单 |
| 产出 | `module.yaml`、`contracts.lock`、`docs/**`、`tests/test_governance_discovery.py` |
| 依赖 | 无（可与 WP-B5-A 并行） |
| 验收 | `python -m autograder_host.checks` 三条 ok；`discovered_modules()` 含 B5；`module_schema_owners()` 不含 B5 |
| 预估 | 1～1.5 人日 |
| 认领人数 | 1 |

## WP-B5-C 可插拔性证据与负例

| 项 | 内容 |
|---|---|
| 覆盖验收 | AC-02、AC-08 ~ AC-12 |
| 覆盖约束 | CHG-0010 §5.3/§5.4（"只会通过的一致性套件证明不了任何事"） |
| 目标 | 用**真实** loader 证明五模块装载与 DAG 顺序；用 5 个必红负例证明违规会被拒绝 |
| 输入 | `autograder_host.main._load_modules`、`AUTOGRADER_MODULES` 声明格式 |
| 产出 | `tests/{conftest,_probe_modules,test_composition_load,test_load_failures}.py` |
| 依赖 | WP-B5-A 的 descriptor 与 WP-B5-B 的清单**字段值**已定（能力名、路由前缀） |
| 验收 | 5 个负例各自的 `ModuleLoadError`/`RegistryError` 断言成立；`B0` 探针证明顺序不是字母序 |
| 预估 | 1～1.5 人日 |
| 认领人数 | 1 |

## 并行前提

| 必须先冻结 | 谁冻结 | 冻结后 |
|---|---|---|
| 端点路径与前缀（`/api/v1/platform/conformance`、`/api/v1/platform`） | MAIN 契约（CHG-0010，**已冻结**） | 任何人不得单方面改动；要改先走 CHG |
| 能力名（`b5-conformance`、`b4-auth`、`b3-questions`、`b2-submissions`、`host`） | 各自 `module.yaml`（**已冻结**） | 不得新增/改名；ADR-0013 已记录"能力名是自由字符串"的风险 |
| `ConformanceReport` 的两种 `kind` 取值 | MAIN 契约（**已冻结**） | 新增 `kind` = 契约变更 |
| `module.yaml` 与 descriptor 的字段一致性要求 | B4 的 `compare_descriptor_to_manifest()` | 改一处必须同时改另一处，否则装载失败 |

## 认领表

| 工作包 | 认领人 | 状态 | 预计完成 |
|---|---|---|---|
| WP-B5-A | | | |
| WP-B5-B | | | |
| WP-B5-C | | | |
