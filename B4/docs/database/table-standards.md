# B4 表结构规范（RQ-B-4-30 核心交付）

> 本文件是 `RQ-B-4-30` 的**规范成文交付**：约束 B4 自身与 B2/B3 的表结构风格。物理表仍由各模块自己的
> 迁移实现（B4 只定义规范，不代管 B2/B3 表）。
>
> **规范可自动校验**，两种模式：
>
> - `uv run python -m autograder_b4.ops.table_standards --scope catalog`：读真实 `b4` catalog，逐条检查
>   （B4 只有自己 schema 的读权限）；
> - `uv run python -m autograder_b4.ops.table_standards --scope migrations`：扫描**每个模块**的
>   `B*/alembic/versions/*.py` DDL 文本，使规范第一次能覆盖 B2/B3 的迁移（见 §9）。
>
> 不带 `--scope` 时两者都跑。`tests/test_schema_standard.py` 调用同一批函数，并对**故意违规的输入**
> 断言检查器确实会报错（检查器不能失败就等于没有检查器）。

## 1. 命名

- 表名：复数 snake_case（`users`、`sections`、`enrollments`）。
- 列名：snake_case（`course_id`、`created_at`、`is_active`）。
- 主键列：`<entity>_id`（`user_id`、`course_id`、`section_id`、`enrollment_id`、`audit_id`）。
- 主键约束：`pk_<table>`。
- 索引：`ix_<table>_<cols>`（如 `ix_enrollments_user_id`）。
- 唯一约束：`uq_<table>_<cols>`（如 `uq_users_student_number`）。
- 外键：`fk_<table>_<ref>`（如 `fk_sections_course_id`）。
- 检查约束：`ck_<table>_<rule>`（B4 自定约定，同样被自动检查）。
- 禁止：保留字、大小写混合、拼音、无意义缩写（`usr`、`tbl`）。
  - 注：实体 `user` 的物理表名为 `users`，正是为了避开 `user` 这一 SQL 保留字。

## 2. 主键

- `<entity>_id UUID`，**UUID v4**（Python `uuid.uuid4()`）。
- 由**数据 owner 模块生成**，不透明、不可变；创建后永不改。
- **不在数据库侧设默认值**：`test_uuid_primary_keys_are_generated_by_the_application` 断言无 DB 默认。
- 禁止自增整数暴露业务量（见 `../../MAIN/docs/api/identifiers.md`）。
- `<entity>` 是**表所建模的实体名**，在
  `src/autograder_b4/ops/table_standards.py: PRIMARY_KEY_COLUMNS` 中**显式声明**——从表名推断会得出
  `audit_record_id` 这类错误结论，因此推断被禁止：未声明的表直接判违规。

### 2.1 原型之一：内容寻址主键（`content_addressed`）

有些表的存在理由就是"按内容去重"，代理键会让理由消失（同一份内容可以有两行）。

- 主键**就是摘要列**：列名以 `_sha256` / `_sha512` / `_digest` / `_hash` 结尾；
- **必须有摘要长度/格式检查**：`CHAR(64)`/`VARCHAR(64)` 的类型长度，或 `CHECK (<col> ~ '[0-9a-f]{64}')`
  这类约束——"某个非 uuid 文本列"不算；
- 在 `NATURAL_KEYS` 中以 `(模块, 表名)` **声明**（例：`("B3", "blobs")`）。

### 2.2 原型之二：枚举自然键（`enumerated_natural_key`）

"每个取值一行"的计数/状态表，其身份就是那个取值本身（例：`consumption_counters.outcome`）。

- 主键为**单列短标签**：类型为 `VARCHAR(n)` 且 `n ≤ 64`；
- **必须被约束到固定取值集合**：`CHECK (<col> IN (...))`；
- 在 `NATURAL_KEYS` 中声明。

### 2.3 原型之三：复合自然键（`composite_natural_key`）

自然唯一性**就是那几列的组合**（例：`submission_files(submission_id, path)`、
`inbox_events(consumer, event_id)`、`question_version_score_buckets(question_version_id, bucket_label)`）；
加代理键只会引入一个没有语义的列和一个多余的唯一约束。

- 主键为**两列及以上**；列顺序即声明的顺序；
- 在 `NATURAL_KEYS` 中声明（逐列列出）。

> **声明的两条硬性要求**（检查器两条都查）：
> 1. **必须声明**——长得像原型不等于就是原型：`payloads(digest varchar(64) PRIMARY KEY)`、
>    未声明的复合主键、以及只是名字相近的 `event_inbox_backup` 都**照旧判违规**；
> 2. **必须真的符合该原型**——声明不能"洗白"一张并不符合模式的表：摘要键没有 64 位检查、
>    "复合键"其实只有一列、枚举键没有取值集合约束，都会被报出来。
>
> 这不是把规则关掉：`test_declared_archetypes_are_not_reported`（不得误报）、
> `test_undeclared_lookalikes_are_still_reported`（不得漏报）、
> `test_a_declaration_cannot_launder_a_table`（声明不能洗白）三向都钉住。

### 2.4 表名不需要复数的两种情况

- `event_inbox` / `event_outbox`：该模式的通用命名，且 `inbox`/`outbox` 不是可数名词；
- `case_evidence`：`evidence` 是不可数名词，`case_evidences` 不是英文。

两者都在 `NON_PLURAL_TABLE_NAMES` 中**按 `(模块, 表名)` 精确声明**；
其它任何单数表名（包括 `event_inbox_backup` 这类"看着像"的名字）仍然是违规。

## 3. 时间

- `created_at` / `updated_at` 均为 `timestamptz(3)`（毫秒精度），一律 UTC 存储。
- 对外 API / 事件用 UTC RFC 3339（`2026-09-15T08:30:00.000Z`）；格式化与比较集中实现于
  `src/autograder_b4/api/timestamps.py`，避免"写毫秒、比整秒"这类不一致。
- 事件/操作时间点用 `occurred_at`；操作者用 `..._by`（值为 `user_id` 或 `system`）。
- 乐观并发（`expected_updated_at`）按**毫秒**比较：截断到整秒会让同一秒内的第二次写入绕过保护。

## 4. 索引 / 唯一 / 外键

- 高频查询列建索引（`created_at` 范围查询、`course_id`/`section_id`/`user_id` 过滤）。
- 业务唯一性用显式唯一约束（如 `(section_id, student_number)` 选课唯一），不靠应用层口头约定。
- 可为空的自然键用 `UNIQUE … NULLS NOT DISTINCT`（PostgreSQL 15+），否则多行 `NULL` 会绕过唯一性
  （`sections(course_id, section_code, term)`、`roles(user_id, role, course_id)`）。
- 外键**仅限本 schema 内**；跨 schema 引用只传 UUID，不建外键。

## 5. 软删除

- 仅在需要时用 `deleted_at`（`timestamptz`，NULL=未删）或语义化的 `is_active`（`users`/`enrollments`）。
- 使用软删除的表必须**说明查询含义**：默认查询是否排除已删；审计/历史引用不受软删除影响。
- 不可变主键不随软删除变化。

## 6. 禁止项

- 跨 schema 外键、跨 schema 视图、跨 schema SQL/ORM。
- `public` 共享 ORM / 共享表。
- 枚举随意新增：角色等受限取值变更走契约变更。
- 手工改库（无迁移的 DDL）。
- 密码/令牌/PII 明文入表。

## 7. 迁移规范（对应 `migrations.md`）

- 每模块独立 Alembic 版本表 `alembic_version_<schema>`。
- 一次迁移只做一件事；可回滚或说明不可回滚原因。
- head 与 `module.yaml: database.migration_head` 一致。

## 8. 可校验性（验收依据）

自动检查项（`ops/table_standards.py`，对真实 catalog 执行）：

1. 表名/列名 lower snake_case；
2. 每表有主键，且主键列名等于声明的实体主键、类型为 `uuid`、约束名为 `pk_<table>`
   （§2.1–§2.3 声明的原型表改按其原型的前置条件校验）；
3. 所有时间列为 `timestamptz` 且精度为 3；
4. 外键全部落在 `b4`，且以 `fk_` 开头；
5. 唯一约束以 `uq_` 开头，其它索引以 `ix_`/`uq_` 开头；
6. 检查约束以 `ck_` 开头；
7. `public` 中不出现 B4 的表名。

检查项与 `../../MAIN/docs/database/ownership.md` 及本文件一致，不能各写一套。

## 9. 迁移文本静态检查（对全仓生效，`RQ-B-4-30` AC1）

`uv run python -m autograder_b4.ops.table_standards --scope migrations`

- **范围**：`B*/alembic/versions/*.py`；模块 → schema 取自各模块自己的 `module.yaml: database.schema`，
  不靠猜。
- **读文件 ≠ 读表**：不建立任何跨 schema 连接，结论是"**B2/B3 的迁移文本**不符合规范"，不是
  "B2/B3 的库不符合规范"。真实库的验收仍需 G-DB-01/04/05 在真实 PostgreSQL 上执行。
- **解析方式**：AST 解析迁移文件，识别 `op.execute("<DDL>")`（含 `f"…{SCHEMA}…"`，模块级常量会被代入）
  与 `op.create_table`/`create_index`/`add_column`/`create_*_constraint` 两种写法；字符串字面量、`--`/`/* */`
  注释与 `$$…$$` 体在结构扫描前被遮蔽（注释不遮蔽会让尾随注释变成**下一列**的前缀）。**主键每表只判定一次**，
  在读完整个表体之后——摘要/枚举检查可能出现在主键之后。
- **静态可判定规则**（与 §1–§4 一一对应）：表名 snake_case 且复数（§2.4 声明的两个名字除外）；
  列名 snake_case；`<entity>_id` 单列 UUID 主键且约束名 `pk_<table>`（§2.1–§2.3 声明的原型表按其前置条件
  校验，见下）；`ix_<table>_<cols>` / `uq_<table>_<cols>` / `fk_<table>_<ref>` / `ck_<table>_<rule>` 前缀；
  `created_at`/`updated_at` 为 `timestamptz(3)`；其它 `*_at` 不得为裸 `timestamp`；
  不得跨 schema 建表 / 建外键 / 建视图。
- **原型表的校验**：声明在 `NATURAL_KEYS`（按 `(模块, 表名)`）；检查器同时验证"声明与实际主键一致"与
  "确实符合该原型"（摘要键的 64 位检查、枚举键的取值集合、复合键的列数）。因此**声明既不能漏，也不能滥用**。
- **不会静默通过**：统计"识别到的 DDL 语句数"，某模块为 0 即报违规（正则/AST 失效会失败而不是放过）；
  测试另外钉住全仓语句数下限。
- **实测（2026-09-19，原型补全后）**：`migrations: 137 DDL statements in 16 files, 56 violation(s)`
  ——**35 条主键约束未命名 + 21 条 `timestamptz` 未声明 `(3)`，全部位于 B2/B3；B4 自身 0 条**。
  `--scope catalog` 对真实 `b4` schema 为 0 条。
  补全原型前该命令是 68 条，其中 **12 条是对正确设计的误判**（5 条复合主键、3 条表名非复数、
  2 条主键非 uuid、2 条主键列不以 `_id` 结尾）——这 12 条已随 §2.1–§2.4 消失，另外 56 条真违规**一条未减**。
  MAIN 对此的裁决是 `../../MAIN/docs/phases/active/phase-01-integration-runtime.md` §12.2（E16）。
  **按任务边界，B2/B3 的违规由各自模块修复，B4 只报告。**
