# B3 数据库 Schema（意图、权限与生命周期）

**DDL 权威**：`alembic/versions/**`（迁移文件）。本文件只写**表意图、关键约束、权限、生命周期、私有数据标记**，不抄 DDL。命名规范遵循 `../../../MAIN/docs/database/ownership.md` 与 B4 表结构规范（`RQ-B-4-30`）。

## 归属声明

- 只使用 `b3` schema；迁移版本表 `alembic_version_b3`（`alembic.ini`/`env.py` 固定）。
- **禁止**跨 schema 外键 / SQL / 视图 / ORM；禁止 `public` 共享 ORM；禁止超级用户连接。
- 当前实际状态：`已完成` —— 三个迁移（`20260915_0001` 建 schema、`20260915_0002` 完整业务、`20260915_0003` 凭证与消费可见性）可在空库从零执行，回滚可往返；`tests/test_migrations.py` 每次运行都会 drop `b3` 后重跑，因此这是**持续验证**的结论。

## 表清单（实际）

| 表 | 用途 | 关键约束 | 生命周期 |
|---|---|---|---|
| `questions` | 题目逻辑实体（跨版本稳定）：富文本题干、语言、状态、标签 | `question_id` PK；`course_id` 为**普通 UUID 列**（B4 拥有课程表，跨 schema FK 被禁止）；`language`/`status` CHECK；`tags` 三条 CHECK（数组 / 每项匹配契约正则 / ≤ 20 项）+ GIN 索引（`ix_questions_tags`，服务于 `GET /questions?tags=` 的**全部满足**检索，`jsonb @>`） | 教师创建；`publish` 时置 `published`；`deleted_at` 软删 |
| `question_versions` | 不可变版本快照：题干、adapter manifest、`content_digest`、`package_digest`、修订理由与结构化差异 | `uq_question_versions_question_id_version`；`status='draft' OR published_at IS NOT NULL`；`status='published' ⇒ 两个摘要非空`；`revision_note` 长度 / `revision_diff` 为对象；触发器 `trg_question_versions_guard` 拒绝已发布内容原地改写与非草稿删除（SQLSTATE `23001`） | 教师建草稿；每次写入草稿内容后重算 `content_digest`（凭证绑定的对象）；`publish` 冻结；`archive` 保留引用 |
| `question_assets` | 题干图片（字节 + 内容摘要 + 类型 + alt 文本） | `uq_question_assets_question_id_content_sha256`（同题同图去重）；`content_sha256` 正则 CHECK；`size_bytes > 0` | 上传仅限草稿版本；发布时绑定到版本；随题保留 |
| `reference_answers` | **受保护**：参考答案（按语言） | `uq_reference_answers_question_version_id_language`；`trg_reference_answers_guard` | 草稿期可改；发布后不可变 |
| `test_suites` | **受保护**：版本 ↔ 用例集合的绑定 | `uq_test_suites_question_version_id`；`trg_test_suites_guard` | 同上 |
| `test_cases` | **受保护**：输入、期望 stdout/stderr、期望退出码、权重、可见性 | `uq_test_cases_test_suite_id_ordinal`；`weight > 0`；`trg_test_cases_guard` | 同上 |
| `blobs` | **受保护**：内容寻址的 fixture 字节（主键即 SHA-256） | `content_sha256` 正则 PK；`size_bytes > 0` | 上传即入库；被 `fixtures` 引用 |
| `fixtures` | fixture 元数据：工作区相对路径、`mount`、内容摘要与大小 | `uq_fixtures_question_version_id_path_mount`；`mount IN (input, expected_output)`；路径正则 CHECK；`fk_fixtures_content_sha256 → blobs` | 上传仅限草稿；发布时摘要绑进 package |
| `reference_attestations` | 外部验证器的**声明**：它跑过该版本的参考答案并观察到期望输出。**非**受保护表：只有摘要与计数，没有它所描述的内容 | 复合 PK `(question_version_id, content_digest)`（同一内容重复上报是替换，不是新增；内容改了旧凭证仍留档）；`uq_..._attestation_id`；`cases_total 1..1000`、`cases_passed ≤ cases_total`；`content_digest` 正则 CHECK；`fk → question_versions ON DELETE CASCADE` | 由服务间调用写入（仅草稿版本）；`publish` 要求存在与**重算摘要**一致的凭证；无凭证/不匹配即拒发 |
| `question_draft_generations` | **受保护**：一份**未被接受**的 AI 候选（AI-FLOW-01A）。它不属于任何题目/版本，因此单独一张表 | `owner_subject`（JWT `sub`，文本而非 UUID：issuer 不保证形式）；`status IN (ready, needs_review, failed)`；`warnings` 为数组、`proposal` 为对象或 NULL；`accepted_at ⇒ proposal IS NULL ∧ accepted_question_version_id 非空`；`discarded_at ⇒ proposal IS NULL`；`expires_at > created_at`；`fk → question_versions ON DELETE SET NULL` | 生成时写入候选正文与 `expires_at`；教师保存时**在同一事务**里回填 `accepted_question_version_id` 并置 `proposal = NULL`；显式丢弃只清正文、留 `discarded_at`；过期后读取被拒并删除该行（用量账本不受影响） |
| `ai_generation_usage` | **非**受保护：付费调用的去标识化账本。**没有任何列**能放 prompt、目标、响应正文或候选 | `status IN (ready, needs_review, failed)`；`duration_ms >= 0`；三组 token 计数 `>= 0` 或 NULL；索引 `(owner_subject, requested_at)` 服务预算窗口统计 | 每次**出网调用**写一条（含失败）；接受候选时回填 `accepted_question_version_id`；永不随候选一起删除 |
| `assignments` | 作业集合（课程 × 课序） | `status IN (open, archived)`；`course_id`/`section_id` 普通 UUID 列 | 教师创建 || `assignment_items` | assignment ↔ **已发布**版本的关联 | `uq_assignment_items_assignment_id_question_version_id`、`uq_..._ordinal` | "只引用已发布版本"由领域层保证（跨行条件不适合做 CHECK） |
| `event_inbox` | 事件去重 inbox（durable） | `event_id` PK；`uq_event_inbox_evaluation_run_id`；`producer='B2'` CHECK；`payload` 仅存**白名单校验后**的去标识化字段；复合索引 `(question_version_id, occurred_at, event_id)` 服务于窗口的 keyset 分页 | 消费器写入；重放的唯一数据源 |
| `event_outbox` | 发布侧 outbox（与业务变更同事务） | `uq_event_outbox_event_id`；`producer='B3'` CHECK；`attempts`/`last_error`/`published_at` | 发布事务写入；投递器标记已发布；失败保留待重试 |
| `consumption_counters` | 按投递结果（accepted / duplicate_event / duplicate_run / rejected）计数 + 最后发生时间 | `outcome` PK + 取值 CHECK；`delivery_count >= 0` | 消费器**每条路径**递增；供健康面区分"没有事件"与"事件被拒收"。被拒事件从不进 inbox，这是它唯一的落库痕迹 |
| `question_version_stats` | 去标识化预聚合（整数计数） | `pass_count <= pass_known_count`；各计数 `>= 0`；**无分数列** | 消费器递增；`rebuild` 覆盖写 |
| `question_version_score_buckets` | 得分分布（`bucket_label` → 计数） | 复合 PK；计数 `>= 0` | 同上 |
| `question_version_failure_categories` | 失败类别分布（`failed_category` → 计数） | 复合 PK；计数 `>= 0` | 同上 |

统计三张表**故意不设**指向 `question_versions` 的外键：统计必须能仅凭事件流重放重建，不能因为版本行被归档或不存在而失败。

## 权限模型（受保护内容的独立权限，验收 3-10-3）

| 角色 | 授权 | 用途 |
|---|---|---|
| `autograder_b3`（schema owner） | DDL；仅迁移使用 | `alembic upgrade head` |
| `autograder_b3_app`（最小权限） | 对业务表 **SELECT**；对 `event_inbox`、`consumption_counters` 与三张统计表 SELECT+INSERT+UPDATE+DELETE；对 `event_outbox` SELECT+UPDATE；对 `reference_attestations` 仅 SELECT | 公开读、统计消费、健康面、outbox 投递。**对 `reference_answers`/`test_suites`/`test_cases`/`blobs`/`fixtures`/`question_draft_generations` 已 `REVOKE ALL`**（候选可能含参考答案与隐藏用例，因此和它们同一档） |
| `autograder_b3_internal` | 对 `b3` 全部表 SELECT+INSERT+UPDATE+DELETE | 草稿写入（携带参考答案与用例）、发布/编译、package 重编译、**凭证写入**（写它需要读受保护内容来重算摘要） |

- 角色由 `infra/postgres/init.sql` 创建（模块本地库由 compose 自动执行；平台库上的角色供给属 B4）；迁移中的授权块在角色不存在时**跳过而非失败**，因此同一迁移也能跑在平台库上。
- 受保护表带 `COMMENT ... 'PROTECTED'`，便于审计工具识别。
- **禁止超级用户连接**：`tests/test_migrations.py::test_no_superuser_connection_is_used_by_the_application` 断言应用连接的 `is_superuser=off`。

## 私有 / 敏感数据标记

| 数据 | 敏感级别 | 存取规则 |
|---|---|---|
| 参考答案（`reference_answers`） | **受保护** | 仅内部角色可读；按契约**无任何出站路径**（package schema 与公开 DTO 均无该字段） |
| 隐藏用例与 expected output（`test_cases`） | **受保护** | 仅内部角色 + 服务间 package 端点；公开 DTO / 事件 / 日志永不含 |
| fixture 内容（`blobs`）、元数据（`fixtures`） | **受保护** | 内容寻址；对外只出现摘要，且仅在内部 package 中 |
| 题干图片（`question_assets`） | 受控 | 课程作用域 + 仅已发布题目对学生可见；响应带 `nosniff`/CSP/`ETag` |
| 凭证（`reference_attestations`） | 公开（教师可见） | 只有摘要、条数与计数，**没有**它所描述的内容；因此不列入受保护表，但只有内部角色能写 |
| 修订差异（`question_versions.revision_diff`） | 公开（教师可见） | 结构性差异（计数、长度、摘要、存在性），**键集合 = 契约 `RevisionDiff` 的 11 个键**（落库形状与公开形状同一个），由测试直接读契约文件断言；不含题干正文、参考答案或期望输出 |
| AI 候选（`question_draft_generations.proposal`） | **受保护** | 教师专用：可含参考答案与候选隐藏用例。只听调用方自己的 `generation_id`（另一个账号得到 403），只在该课程写权限内可读，按配置的保留期过期，接受/丢弃后正文即被清空 |
| AI 用量（`ai_generation_usage`） | 运维（去标识化） | 只有 actor/course、provider/model/prompt_version、时间、耗时、token 计数、状态与已接受版本；**表里没有可放 prompt 或响应正文的列** |
| 统计聚合 | 去标识化 | 只含聚合计数；无身份、无分数原值 |
| 消费计数（`consumption_counters`） | 运维 | 只有 outcome 计数与时间，无 payload |

## 数据流落库要点

- 内容寻址：fixture 字节以 SHA-256 为主键去重存储；题干图片按 (题目, 摘要) 去重。
- 不可变快照：`publish` 时把**全部内容**（题干、参考答案、用例、fixture 摘要、adapter manifest）的规范化摘要 `content_digest` 与编译产物摘要 `package_digest` 一起固化；此后任何内容变更都必须产生新版本。
- **草稿也有 `content_digest`**：每次写入草稿内容后重算并持久化。理由是两个要求互为前提——外部验证器只能从
  `QuestionVersionPublic.content_digest` 得知该上报哪个摘要（它看不到隐藏用例，因此算不出来），而 `publish`
  又只认与**重算值**一致的凭证。发布后该值不可变（触发器保证）。
- **重写草稿内容必然换摘要**，即使正文一字不改：用例的 `case_id` 由 B3 在每次写入时重新生成
  （`domain/questions.py::_cases_from_payload`），而它参与规范化摘要。这是"改内容即失效"的加强版，
  不是缺陷；副作用是"读出来再原样写回"需要重新验证，这一点已写进 `publish` 的 422 提示语。
- **修订差异的落库形状 = 契约形状**（`CHG-0013`）：`revision_diff` 存的就是 `RevisionDiff` 的 11 个键，
  因此版本 DTO 不需要投影层，"库里能读出什么"与"客户端能收到什么"是同一个结构。
  首个版本两个字段均为 `NULL`（`revision_diff` 的列 CHECK 只要求"是对象"或不存）。
- 统计只写预聚合表：不保留源码、身份或分数原值；`rebuild` 从 `event_inbox` 覆盖重建。
- 窗口统计不预聚合：`from`/`to` 路径按 keyset 分页从 `event_inbox` 重算（每页 1000 行，硬上限 100 万行），
  因此它是 `O(窗口内事件数)` 的；10 万量级的实测与不达标结论见
  [Phase 03](../phases/active/phase-03-quality-attestation-and-bounded-statistics.md)。

## 相关测试

`tests/test_migrations.py`（表/触发器/权限/命名/跨 schema/回滚）、`tests/test_versions.py`（触发器行为）、`tests/test_statistics.py`（预聚合语义）。

## 表规范对齐（`RQ-B-4-30` / E16）

`20260915_0004` 把 17 个主键约束命名为 `pk_<table>`、把 15 个 `created_at`/`updated_at` 改为 `timestamptz(3)`。
两点必须记住：

- **只改声明，不改设计**。复合主键仍复合（规范要求的是**约束名**），`blobs`/`consumption_counters` 等内容寻址、
  枚举自然键表**未被"修正"**——它们已被规范按声明合法豁免。
- **`timestamptz(3)` 是数据变更**：收窄类型会重写表并把已有值按毫秒四舍五入，亚毫秒精度丢失。
  实测与门禁状态见 [Phase 05](../phases/active/phase-05-table-standard-alignment.md)。
