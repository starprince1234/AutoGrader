# Phase 10 隔离区的行级课程范围（P13 的越权修复）

- **来源**：P13 复核工作区（`/review?tab=mail`）的强制验收门槛——"教师只能读取和操作授权范围内
  有可靠 course/section 关联的记录；无法验证的记录不能泄露给普通 staff；管理员可以按规则处理
  unscoped 记录；数据过滤发生在 SQL 分页和 cursor 计算之前"。契约 `0.19.0` 的
  `IntakeQuarantineView.scope_course_id/scope_section_id`、`listIntakeQuarantines` 的
  `course_id/section_id` 参数与 `resolveIntakeQuarantine` 的行级描述。
- **此前的缺口**：`b2.intake_quarantines` 没有任何课程关联列，授权只能判到"staff 令牌带任意课程
  作用域"；另一门课的教师可以读到本部署的全部隔离记录。旧测试把它钉成"已知缺口"
  （`test_quarantine_scope_gap_is_recorded_not_hidden`），本批把它改成**不可读**的断言。

## 1. 行的课程范围从哪来（唯一来源）

`intake/matching.py::MappingEmailMatcher.match` 在原有结论之外返回 `scope_context`：命中规则的
`target` 里 `(course_id, section_id)` **去重后只有一个组合**时带出该对值，否则 `None`。
`intake/imap.py` 在**同一事务**里把 scope 与隔离行、checkpoint 一起写入。

| 场景 | `reason_code` | scope |
|---|---|---|
| 命中多条规则、候选是不同学生但同一 course/section | `ambiguous_match` | 该对值 |
| 候选跨课序 / 跨课程 | `ambiguous_match` | NULL |
| 没有任何规则命中 | `not_uniquely_matched` | NULL |
| 体积/结构/附件/内容闸门拒绝（匹配从未运行） | 各自的拒绝码 | NULL |

**不从** `mailbox`、发件人、附件名或请求参数推测 scope——那些都是攻击者可控输入，用它们补课程
关联等于伪造授权依据。历史行（0002 以来）没有、也无法证明课程归属，迁移**只加列**并把它们留成
NULL：部署后只对平台管理员可见（保守收紧），原始行数与其余列逐字节不变。

## 2. 迁移 `20260915_0015`（离线渲染的真实 DDL）

```sql
ALTER TABLE b2.intake_quarantines ADD COLUMN scope_course_id UUID;
ALTER TABLE b2.intake_quarantines ADD COLUMN scope_section_id UUID;
ALTER TABLE b2.intake_quarantines
  ADD CONSTRAINT ck_intake_quarantines_scope
  CHECK ((scope_course_id IS NULL) = (scope_section_id IS NULL));
CREATE INDEX ix_intake_quarantines_scope_created_at
  ON b2.intake_quarantines (scope_course_id, scope_section_id, created_at);
```

- 纯加列 + 成对 CHECK + 查询索引；`downgrade()` 逆序删除（课程关联随之丢失，见
  [../../database/migrations.md](../../database/migrations.md)）。
- `EXPECTED_SCHEMA_REVISION`、`module.yaml: database.migration_head` 与 Alembic head 同步为
  `20260915_0015`（readiness 判据；三者不一致时 B2 如实报 503，而不是带着旧结构服务）。
- 本机无 PostgreSQL：迁移以 `alembic upgrade 20260915_0014:20260915_0015 --sql` **离线渲染**
  核对 DDL；真实库上的 upgrade/downgrade **未执行**（见第 5 节）。

## 3. 授权实现（三条独立的判定）

1. **入口**（`auth/authorization.py::require_intake_quarantine_authority`）：角色 + staff 至少一个
   课程作用域 + 管理员豁免。学生与 `scope: []` 的 staff 一律 403。
2. **列表**（`intake/quarantine.py::_scope_predicate`）：staff 的 SQL 条件是
   `scope_course_id IS NOT NULL AND OR_i(scope_course_id = c_i AND (s_i IS NULL OR scope_section_id = s_i))`
   ——课程级作用域覆盖该课程全部课序，课序级要求精确相等；管理员不加限制（未关联行只出现在
   管理员响应里）。谓词与用户筛选、游标条件一起施加在 `ORDER BY/LIMIT` **之前**，因此响应、
   `next_cursor` 与分页都不暗示被隐藏的行。query 的 `course_id/section_id` 只是筛选：staff 显式
   越界 → 403（不是空列表），`section_id` 必须伴随 `course_id`（422）。
3. **归属**（`require_quarantine_row_authority`）：在 `SELECT ... FOR UPDATE` 锁住记录之后，对
   **这一行**重判——staff 对 NULL scope/跨课记录 403，且该判定先于"已处理（409）/不可归属（422）"，
   因此不能用错误码差异探测无权限记录的状态；管理员可处置未关联行。写入仍然只有一条
   `UPDATE b2.intake_quarantines`：不建提交、不发事件、不触发评测。

## 4. 本机执行过的验证（真实命令与读数）

| 命令 | 结果 |
|---|---|
| `ruff check src tests`（ruff **0.16.7**，与 `uv.lock` 一致） | `All checks passed!` |
| `pytest tests -q`（无 PostgreSQL：DB 用例跳过） | **630 passed, 684 skipped**，退出码 0 |
| `pytest tests/test_matching_scope.py tests/test_quarantine_scope_sql.py -q` | **10 passed** |
| `import autograder_b2.intake.{matching,imap,quarantine,api}`、`auth.authorization`、`db.schema`、`plugin` | 全部导入成功 |
| `alembic upgrade 20260915_0014:20260915_0015 --sql` | 渲染出第 2 节 DDL，退出码 0 |
| `B1 pnpm api:check`（契约 0.19.0 的生成类型） | `API types match MAIN/contracts.` |

## 5. 未执行 / 未验证（不写成通过）

1. **真实 PostgreSQL 上的 upgrade/downgrade 与 DB 用例**：本机没有数据库实例/Docker/psql。
   `test_intake_api.py`（T-QUAR-001..015）、`test_email_intake.py` 的 scope 持久化用例、
   `test_migrations.py`（含新约束/索引名）与 `test_storage_cas.py` 的 metadata↔DDL 逐列比对
   **全部被跳过**。命令：`B2_TEST_DATABASE_URL=… uv run pytest tests/test_intake_api.py
   tests/test_email_intake.py tests/test_migrations.py tests/test_storage_cas.py`。
2. **迁移在已有数据的库上的实际耗时与锁**：DDL 只加可空列 + CHECK（现有行全部 NULL，CHECK 恒真）
   + 建索引。表规模与索引构建时间未实测。
3. **跨模块**：B2 无法复核"学生是否仍在该课序"（没有 B2→B4 名单通道），resolve 只重查行 scope +
   调用者角色/作用域；客户端每次从 B4 现取候选。这是登记在案的残留缺口（契约 1e 节 ④）。
4. **质量聚合仍只判角色**：`question_version_id → course_id` 是 B3 的事实；P13 前端先经 B3 校验
   版本可见性，但直接调用 B2 quality-summary 的越权向量未关闭（1e 节 ③）。

## 6. 回滚

还原本批 B2 源码/测试/文档与契约 `0.19.0` 文本，执行
`alembic downgrade 20260915_0014`（删索引、约束与两列；课程关联丢失），把
`EXPECTED_SCHEMA_REVISION` 与 `module.yaml` 退回 `20260915_0014`，然后重跑
`relock_contracts.py`。旧行为（任意课程 scope 可读全局）随之恢复，因此回滚必须与契约回滚同批。
