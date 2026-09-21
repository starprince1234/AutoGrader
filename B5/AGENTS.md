# AutoGrader B5 执行规则（给 AI Agent）

本文件是**执行规则**索引，不是百科。完整知识从 [docs/README.md](docs/README.md) 进入。
遵守 [../MAIN/docs/collaboration/module-doc-spec.md](../MAIN/docs/collaboration/module-doc-spec.md) 的三条铁律。

## 开始任务前

1. 读 [docs/README.md](docs/README.md)（权威表 + 状态表）、[docs/requirements/scope.md](docs/requirements/scope.md)。
2. 读 [../MAIN/docs/adr/ADR-0013-open-module-count.md](../MAIN/docs/adr/ADR-0013-open-module-count.md) 与
   [../MAIN/docs/contracts/changes/CHG-0010-b5-conformance-module.md](../MAIN/docs/contracts/changes/CHG-0010-b5-conformance-module.md)：
   它们是 B5 存在的**理由**，也规定了 B5 必须证明的四件事。
3. 确认契约现状：本仓库 [contracts.lock](contracts.lock) 的 `contract_version` 与
   `../MAIN/contracts` 实时摘要一致；不一致**停下**走契约变更规程，不要硬写实现。
4. 确认接口：`../MAIN/contracts/openapi/b5.yaml`（唯一权威，禁止在文档或代码里手抄字段表）。
5. 检查 Git 状态，保留未提交的修改。

## 我负责什么 / 不负责什么

- 我拥有：平台一致性审计（运行中的组合 vs 契约 vs 宿主平台路由的三视图比对）。
- 我不拥有：MAIN 契约（属 MAIN，ADR-0003）、运行期模块注册表（属 B4，RQ-B-4-40）、
  任何业务数据（题库存 B3、提交与成绩存 B2、身份与课程存 B4）。
- 我禁止：写任何数据（无 schema、无迁移、不写文件）；读别的模块的 schema 或代码；
  导入 `autograder_b2/b3/b4`（测试中验证 B4 注册表行为的那一处例外已在文件内注明）。

## 硬规则

1. **只读**：端点的副作用只有"读 `MAIN/contracts/openapi/*.yaml`"与"读 `app.state`"。
   任何写操作、迁移、事件发布都必须先有 ADR。
2. **单一权威**：字段/状态码只以 `../MAIN/contracts/**` 为准；文档只写"我提供什么"，
   **禁止复制字段表**。本模块的结构真源是 `module.yaml` + `src/autograder_b5/plugin.py` 的 descriptor。
3. **诚实**：状态词只用 `骨架` / `待实现` / `已完成` / `阻塞`。未执行的验证必须写明原因
   （见 [docs/phases/active/phase-01-platform-conformance.md](docs/phases/active/phase-01-platform-conformance.md)）。
   **不得**把"套件通过"写成"平台已验证一致"——报告说一致，是报告在说，不是证明。
4. **不削弱断言**：一致性套件必须有会红的负例；为了让它通过而放宽断言，比没有套件更糟。
5. **认证**：令牌由 B4 签发，本模块**本地验签**（不回调 B4 判定身份）；失败一律 `401`
   且不透露主体是否存在；不记录令牌、cookie 或完整 PII。
6. **禁止** mock 交付路径、兼容层、别名路由、自动降级；不得 shell 字符串拼接执行命令。
7. **依赖**：B5 只有 `pyyaml` / `pyjwt` / `cryptography` 三个真实依赖（理由见
   [docs/architecture/boundaries.md](docs/architecture/boundaries.md) §依赖）；新增依赖必须先讨论。

## 文档同步

改代码时同步更新：`docs/requirements/traceability.md`（约束来源与证据）、
`docs/architecture/*`（若结构变化）、`docs/api/contract.md` / `events.md`（若提供或消费关系变化）、
`docs/testing/cases.md`（新增用例）、`docs/phases/active/`（**真实**命令与结果）、
`docs/README.md` 状态表（若状态变化）。

## 验证

```powershell
cd B5
uv sync
uv run ruff check .
uv run pytest -q
```

> 一致性套件装载**真实**的 `autograder_host.main._load_modules()` 与**真实**的模块 entry point，
> 因此需要 `MAIN/.env.integration`（集成环境变量）。缺失时套件**拒绝收集**并给出原因——
> 静默跳过的一致性检查与通过无法区分。

跨模块门禁在 MAIN 侧执行（它会自动纳入 B5）：

```powershell
cd ../MAIN
uv lock
.\.venv\Scripts\python.exe -m autograder_host.checks
```

## 契约变更

发现契约与实现/文档不一致时：**不**顺着实现改契约，也**不**偷偷改实现对齐。按
[../MAIN/docs/contracts/change-protocol.md](../MAIN/docs/contracts/change-protocol.md) 提交 `CHG-*`，
由 MAIN 判定哪边是权威。禁止自行修改 `../MAIN/contracts/**`（本模块对 `MAIN/pyproject.toml`
的两行依赖声明是同一次变更的一部分，见 CHG-0010）。
