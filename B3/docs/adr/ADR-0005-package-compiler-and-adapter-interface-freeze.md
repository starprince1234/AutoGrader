# ADR 0005 package 编译器签名与 adapter manifest → package 转换接口冻结

- **状态**：接受（冻结）
- **日期**：2026-09-15
- **背景**：
  - ADR-0002 决定"题目版本 → 不可变 package 的确定性编译、三 adapter 共用同一结构"，但把编译器接口签名留成 A↔B 泳道交界处的**待冻结**项（`docs/collaboration/workpackages.md` 串行点 ②）。
  - 签名不冻结就不能并行：A 泳道负责"发布/版本/受保护存储"，B 泳道负责"adapter 校验/编译"，两者必须在**同一组函数**上接合。
  - `MAIN/contracts/schemas/evaluation-package.schema.json` 是 package 结构与 `additionalProperties: false` 约束的唯一权威，本 ADR 不复制字段表。
- **决策**：
  1. **单一编译入口**：`compile/compiler.py: compile_evaluation_package(...)` 是模块内**唯一**的 package 生产函数。任何 adapter、任何调用方（发布流程、内部 HTTP 端点）都只能经它产出 package。
  2. **显式 `created_at`（确定性）**：`created_at` 由调用方传入，编译器内部**不调用** `now()`。发布流程必须传 `snapshot.published_at`，使"同一题目版本的同一内容 → 同一 `package_digest`"在时间维度上也成立。
  3. **摘要定义**：`package_digest = sha256(canonical_json(package 去掉 package_digest 字段))`；规范化规则 = `json.dumps(sort_keys=True, separators=(",", ":"), ensure_ascii=False, allow_nan=False)` 的 UTF-8 字节。规则实现在 `compile/canonical.py`，B2 侧比对必须使用同一规范化（ADR-0002 遗留风险的落地口径）。
  4. **统一校验入口**：三型 adapter 的 manifest 都经 `compile/manifest.py: validate_adapter_manifest(...)`；该函数是 manifest 合法性的**唯一**判据（拒绝绝对路径、`..`、shell 字符串解释、工作区外加载、越界枚举）。
  5. **adapter 注册表即扩展点**：`compile/registry.py` 持有 `AdapterCompiler` 注册表；`compile_evaluation_package` 与 `validate_adapter_manifest` **不得**出现 `if kind == ...` 分支，只按注册表分派。
     - 新增题型 = 实现一个 `AdapterCompiler` + `register_adapter_compiler(...)`，**不改**编译器核心；B2 runner 按 package 的 `adapter.kind` 分派，同样无需改动。
     - 注意：新增 kind 若不在契约枚举内，仍会在 package schema 校验阶段被拒——这是**契约层**门槛，不是编译器耦合（见第 7 条）。
  6. **受保护内容只经内部 DTO 进入编译器**：编译器入参是 `dto/internal.py` 的类型（`PublishedVersionSnapshot` / `AdapterManifestInternal` / `TestCaseInternal` / `FixtureInternal`），**不接受 ORM 对象、字典或公开 DTO**。这使"受保护内容只由 B2 内部 package 接口访问"成为类型级事实。
  7. **契约优先与缺口上报**：package 结构、`adapter.files[].mount`、`comparison` 的枚举一律以 MAIN schema 为准。B3 若发现"需求要求的能力在冻结 schema 中不可表达"，**不自行扩展 package 结构**，而是记录 `CHG-*` 变更记录并只实现可表达的部分（当前实例：file adapter 的"文本差异"比较粒度，见 `../contracts/changes/CHG-B3-0001-file-comparison-granularity.md`）。
- **冻结签名**（细节见 `../collaboration/frozen-interfaces.md` §2）：
  - `compile_evaluation_package(snapshot, manifest, cases, fixtures, *, created_at, policy=None) -> EvaluationPackage`
  - `compute_package_digest(package) -> str`、`canonical_json_bytes(value) -> bytes`
  - `validate_adapter_manifest(raw, *, cases, fixtures) -> AdapterManifestInternal`
  - `AdapterCompiler.validate(...)` / `AdapterCompiler.to_package_section(...)`、`register_adapter_compiler(...)` / `adapter_compiler_for(kind)`
- **后果**：
  - 获得：A/B 泳道可在冻结签名上并行；package 编译可被单元测试穷尽（确定性、越界拒绝、三型同构）。
  - 获得：新增题型不改核心（可被测试证明：注册表分派路径）。
  - 付出：编译器入参必须由调用方组装内部 DTO；多一层映射。
  - 遗留风险：若 B2 对"规范化摘要"的实现与 `compile/canonical.py` 不一致，同一内容会出现两个摘要。缓解：规范化规则写死在本 ADR 与 `frozen-interfaces.md`，并由 `TC-ADP-04` 断言"同输入同摘要、微改内容摘要变化"。
- **关联**：
  - 需求：`RQ-B-3-40/50/60`、`RQ-B-0-10`、`RQ-B-0-30`；`RQ-B-3-20`（发布时编译）
  - ADR：`ADR-0001`（不可变版本）、`ADR-0002`（编译与摘要绑定）、`ADR-0004`（DTO 边界）
  - 契约：`../../../MAIN/contracts/schemas/evaluation-package.schema.json`、`../../../MAIN/docs/adr/ADR-0005`（确定性评分）
  - 冻结清单：`../collaboration/frozen-interfaces.md` §2/§3
  - 测试：`../testing/cases.md` TC-ADP-01/02/03/04、TC-VER-02
