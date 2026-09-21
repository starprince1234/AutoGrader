# B3 冻结接口清单（Frozen Interfaces）

本文件是 `docs/collaboration/workpackages.md` 中"并行前提（先冻结）"三项的**冻结结果**。冻结含义：**冻结后他人不得再改**；任何变更走 `../../../MAIN/docs/contracts/change-protocol.md`（涉及 MAIN 契约）或本模块 ADR 修订（仅涉及 B3 内部）。

冻结日期：2026-09-15。决策依据：[ADR-0004](../adr/ADR-0004-public-internal-dto-module-boundary.md)、[ADR-0005](../adr/ADR-0005-package-compiler-and-adapter-interface-freeze.md)。

---

## 1. 公开 DTO / 内部 DTO 模块边界（对应 `workpackages.md` 待冻结项 ①）

| 项 | 冻结值 |
|---|---|
| 公开 DTO 模块 | `src/autograder_b3/dto/public.py`（**唯一**） |
| 内部 DTO 模块 | `src/autograder_b3/dto/internal.py`（**唯一**） |
| `dto/__init__.py` | **不导出任何符号**；调用方必须写全路径 import |
| 允许的 import 方向 | `api → {dto.public, dto.internal}`；`dto.public` 与 `dto.internal` 互不 import |
| 禁止的 import 方向 | `dto.public → dto.internal`、`dto.* → domain/compile/stats/infra` |
| 公开响应唯一出口 | `api/serializers.py: serialize_public(dto) -> dict`（白名单投影，受保护键名直接抛错） |
| 受保护词汇表所有者 | `dto/internal.py: PROTECTED_FIELD_NAMES`（`dto/public.py` 内不出现任何受保护字段名） |

公开 DTO 符号（字段以 `MAIN/contracts/openapi/b3.yaml` 为准，此处只列符号名）：
`QuestionPublic`、`QuestionVersionPublic`、`StemAssetPublic`、`QuestionStatisticsPublic`、`AssignmentPublic`、`CursorPage`。

内部 DTO 符号：`ReferenceAnswerInternal`、`TestCaseInternal`、`FixtureInternal`、`AdapterManifestInternal`、`SecurityPolicy`、`PublishedVersionSnapshot`、`EvaluationPackage`、`EvaluationCompletedEvent`、`QuestionVersionDraftInput`。

## 2. package 编译器内部函数签名（对应 `workpackages.md` 待冻结项 ②）

```python
# src/autograder_b3/compile/canonical.py
def canonical_json_bytes(value: Any) -> bytes: ...
def sha256_hex(payload: bytes) -> str: ...

# src/autograder_b3/compile/compiler.py
def compute_package_digest(package: Mapping[str, Any]) -> str: ...
def compile_evaluation_package(
    snapshot: PublishedVersionSnapshot,   # dto.internal
    manifest: AdapterManifestInternal,    # dto.internal（已经过统一校验入口）
    cases: Sequence[TestCaseInternal],    # dto.internal（含隐藏用例与 expected output）
    fixtures: Sequence[FixtureInternal],  # dto.internal
    *,
    created_at: datetime,                 # 必传；发布流程传 snapshot.published_at
    policy: SecurityPolicy | None = None, # 缺省用 compile/policy.py 的常量策略
) -> EvaluationPackage: ...
```

冻结约束：

1. `created_at` 必传且在编译器内部**不调用** `now()`（确定性，ADR-0005 第 2 条）。
2. `package_digest = sha256(canonical_json(package 去掉 package_digest))`（ADR-0005 第 3 条）。
3. 入参类型只接受内部 DTO；拒绝 ORM 对象 / 原始 dict / 公开 DTO。
4. 编译器**不含** `if kind == ...` 分派分支（只走注册表）。
5. 编译器不读写数据库、不发事件、不落盘（纯函数）。

## 3. adapter manifest → evaluation package 的转换接口（对应 `workpackages.md` 待冻结项 ③）

```python
# src/autograder_b3/compile/registry.py
class AdapterCompiler(Protocol):
    kind: AdapterKind                              # "stdin" | "file" | "callable"
    def validate(self, manifest: AdapterManifestInternal, *,
                 cases: Sequence[TestCaseInternal],
                 fixtures: Sequence[FixtureInternal]) -> None: ...
    def to_package_section(self, manifest: AdapterManifestInternal, *,
                           cases: Sequence[TestCaseInternal],
                           fixtures: Sequence[FixtureInternal]) -> dict[str, Any]: ...

def register_adapter_compiler(compiler: AdapterCompiler) -> None: ...
def adapter_compiler_for(kind: str) -> AdapterCompiler: ...   # 未注册 → ManifestInvalid
def registered_adapter_kinds() -> tuple[str, ...]: ...

# src/autograder_b3/compile/manifest.py —— 统一校验入口（唯一判据）
def validate_adapter_manifest(
    raw: Mapping[str, Any] | AdapterManifestInput,
    *,
    cases: Sequence[TestCaseInternal],
    fixtures: Sequence[FixtureInternal],
) -> AdapterManifestInternal: ...
```

转换语义（字段名与结构以 MAIN schema 为准）：`kind` 决定由哪个 `AdapterCompiler` 生成 `adapter` 段；三型产出**同一** package 顶层结构，仅 `adapter` 段语义不同；`manifest.kind` 必须在注册表内且必须在契约枚举内。

## 4. 变更规程

- 修改本文件任一冻结项：先改 ADR，再改代码，最后同步本文件（顺序不可颠倒）。
- 涉及 MAIN 契约（package schema / OpenAPI / AsyncAPI）的任何变更：**禁止**在 B3 内自行扩展，提交 `CHG-*`（[`../contracts/changes/`](../contracts/changes/)）。
