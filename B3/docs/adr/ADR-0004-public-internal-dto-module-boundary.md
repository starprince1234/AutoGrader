# ADR 0004 公开 / 内部 DTO 模块边界冻结与序列化白名单

- **状态**：接受（冻结）
- **日期**：2026-09-15
- **背景**：
  - `docs/collaboration/workpackages.md` 与 `docs/collaboration/interfaces.md` 把"公开 DTO 与内部 DTO 的模块划分"列为**待冻结**串行点；A/B 泳道若各自定义会直接造成受保护内容外泄（`MAIN/docs/workstreams/B3.md` §5 WP-B3-A 风险）。
  - ADR-0003 已决定"物理分离 + 自动化契约测试"，但没有钉死**具体的模块位置、符号清单与序列化口径**，无法据以实现与断言。
  - `MAIN/contracts/openapi/b3.yaml` 是公开 DTO 字段的唯一权威；`MAIN/contracts/schemas/evaluation-package.schema.json` 是内部 package 的唯一权威。本 ADR **不复制字段表**，只冻结"哪个文件放哪一类模型"。
- **决策**：
  1. **物理分离（文件级）**
     - 公开 DTO 只存在于 `src/autograder_b3/dto/public.py`。
     - 内部 DTO（受保护内容、package、消费事件）只存在于 `src/autograder_b3/dto/internal.py`。
     - `src/autograder_b3/dto/__init__.py` **不导出任何符号**（只有文档字符串），因此 `from autograder_b3.dto import X` 与 `import autograder_b3.dto as dto; dto.QuestionPublic` 都不可用；调用方必须显式写 `from autograder_b3.dto.public import ...`。这样"用了公开还是内部"在每一行 import 上可见。
  2. **依赖方向（单向）**
     - `dto/public.py` **禁止** import `dto/internal.py`（也不得 import `domain`/`compile`/`infra`）。
     - `dto/internal.py` 同样不 import `dto/public.py`（两套模型互不引用，映射由 `api/serializers.py` 承担）。
     - `api/` 层可以同时 import 两者；这是唯一同时看到两套模型的层。
  3. **受保护词汇表归内部模块所有**
     - 受保护字段名常量 `PROTECTED_FIELD_NAMES` 定义在 `dto/internal.py`。
     - 结论：`dto/public.py` 内**不出现**任何受保护字段名（`reference_answer` / `hidden_cases` / `expected_output` / `expected_stdout` / `expected_stderr` / `fixture_content` / `source_code` / `student_id` …），使"公开模块没有受保护词汇"成为可以 grep 与断言的事实，而不是审阅结论。
  4. **序列化白名单（防御第二层）**
     - 所有公开响应必须经 `api/serializers.py: serialize_public(dto)` 出站；该函数按 `dto/public.py` 的模型字段做**白名单投影**，并对任何受保护键名与非白名单键名抛错（不静默丢弃）。
     - 公开 DTO 一律 `model_config = ConfigDict(extra="forbid")`，构造期即拒绝未知字段。
     - 路由层禁止直接 `return orm_object` / `return internal_dto`。
  5. **内部接口的认证与序列化**
     - `GET /api/v1/question-versions/{id}/evaluation-package` 的响应体由 `dto/internal.py` 的 `EvaluationPackage` 产生，**不经** `serialize_public`；它必须经过服务间凭据校验（非服务间凭据 `401`/`403`）。
  6. **不变量测试（机械拦截，非审阅）**
     - 静态：源码级断言 `dto/public.py` 不含 `internal` import、不含受保护字段名；`dto/__init__.py` 不含导出。
     - 动态：对所有公开 DTO 断言 `字段集合 ∩ PROTECTED_FIELD_NAMES = ∅`；对真实 HTTP 响应断言受保护字段**缺失**（不是"值为空"）。
- **后果**：
  - 获得：公开/内部边界成为文件级事实；A/B 泳道可并行而不会互相污染模型；泄漏会被测试机械拦截。
  - 获得：新增字段时必须先回答"公开还是内部"，并且必须落到两个文件之一。
  - 付出：需要维护两套模型与一份显式映射（`api/serializers.py`）。
  - 遗留风险：ORM 模型（`infra/models.py`）天然同时包含公开与受保护列（同一张表族）。缓解：ORM 只在 `infra`/`domain` 内可见，公开路由**从不**接收 ORM 对象；公开响应的唯一出口是 `serialize_public`。
- **关联**：
  - 需求：`RQ-B-3-10`（验收 3-10-1/3/4）、`RQ-B-3-20`
  - ADR：`ADR-0003`（本 ADR 是其模块级冻结）、`ADR-0005`（编译接口冻结）
  - 边界：`../architecture/boundaries.md` §1/§2
  - 冻结清单：`../collaboration/frozen-interfaces.md` §1
  - 测试：`../testing/cases.md` TC-QST-01/02/03、TC-DTO-01
