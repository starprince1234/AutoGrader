# Phase 07 题库标签覆盖度：`GET /api/v1/questions/coverage`

- **状态**：**active**（未执行项见 §5）
- **范围**：实现 `CHG-0020` 已声明但未实现的 `getQuestionTagCoverage` / `QuestionTagCoverage`
  （契约 0.14.0，`MAIN/contracts/openapi/b3.yaml`）。纯读，无迁移，无新依赖。
- **触发**：`tags` 能写（`CHG-0008`）、能检索（`CHG-0013`），但**分布读不出来** ——
  `CursorPage` 只有 `next_cursor`、没有总数，"这个知识点是两道题还是二十道"只能靠人翻页数。

---

## 1. 落点

| 层 | 文件 | 内容 |
|---|---|---|
| 公开 DTO | `src/autograder_b3/dto/public.py` | `TagCountPublic`、`QuestionTagCoveragePublic`，加入 `PUBLIC_DTO_TYPES`（受 ADR-0004 的"无受保护词汇"断言约束） |
| 聚合 | `src/autograder_b3/infra/repositories.py:144`（`QuestionBankRepository.tag_coverage`） | 两个聚合，同一条可见性谓词 |
| 读路径 | `src/autograder_b3/domain/catalog.py:113`（`question_tag_coverage`）、`:32`（`_read_scope`） | 作用域与可见性与 `list_questions` 共用同一个解析函数 |
| 序列化 | `src/autograder_b3/api/serializers.py:200`（`coverage_to_public`） | 显式投影，只出 5 个声明字段 |
| 路由 | `src/autograder_b3/api/router.py:212` | 声明在 `/api/v1/questions/{question_id}`（`:249`）**之前** |

**与 `listQuestions` 同源**：原 `list_questions` 里的"非教职员强制 `published_only=True`、课程作用域取交集、
无权限即 `403`"三段判定被提取为 `_read_scope()`，两个读路径都调用它。这不是重构偏好：覆盖度如果与列表
在可见性上分歧，它统计的就是另一个题库。

## 2. 实际发出的 SQL（编译产物，非手写等价物）

```sql
-- 合计：tagged/untagged 各自 count(*)，不是相减得来
SELECT count(*) AS total,
       count(*) FILTER (WHERE jsonb_array_length(b3.questions.tags) > %s) AS tagged,
       count(*) FILTER (WHERE jsonb_array_length(b3.questions.tags) = %s) AS untagged
FROM b3.questions
WHERE b3.questions.deleted_at IS NULL AND b3.questions.course_id = %s
  AND b3.questions.status = %s            -- published_only=true 时

-- 逐标签：一次 GROUP BY，展开题目自己的 jsonb 列
SELECT anon_1, count(*) AS question_count
FROM b3.questions, jsonb_array_elements_text(b3.questions.tags) AS anon_1
WHERE b3.questions.deleted_at IS NULL AND b3.questions.course_id = %s
GROUP BY anon_1
ORDER BY count(*) DESC, anon_1 ASC
```

`question_count >= 1` 是结构性的：`GROUP BY` 只产出存在的分组，没有题目的标签不可能出现，
不需要额外过滤。排序在 SQL 里定死（计数降序、同数标签升序），不依赖后处理。

## 3. `CHG-0020` §5 的一处措辞与实测不符（**如实记录，未自行改 MAIN 文档**）

`CHG-0020` §5 写"一次 `GROUP BY` 聚合，**走 `questions.tags` 上的既有 GIN 索引**（`ix_questions_tags`）"。
前半句成立，后半句**不成立**：

```text
=== EXPLAIN (ANALYZE, BUFFERS) per-tag statement ===
Sort (actual time=0.071..0.071 rows=5 loops=1)
  Sort Key: (count(*)) DESC, tag.value
  ->  HashAggregate (actual time=0.038..0.040 rows=5 loops=1)
        Group Key: tag.value
        ->  Nested Loop (actual time=0.031..0.035 rows=5 loops=1)
              ->  Index Scan using ix_questions_course_id on questions q
              ->  Function Scan on jsonb_array_elements_text tag (loops=5)
Execution Time: 0.160 ms

=== indexes on b3.questions ===
ix_questions_tags | CREATE INDEX ix_questions_tags ON b3.questions USING gin (tags)
```

**原因不是实现选择，而是索引类型**：jsonb 上的 GIN 索引服务的是包含/存在谓词（`@>`、`?`），
而 `jsonb_array_elements_text()` 是集合返回函数展开，没有任何 GIN 操作符类能服务它。
`ix_questions_tags` 的真实服务对象是 `listQuestions?tags=` 的 `jsonb @>` 过滤（`CHG-0013`），
那条路径没有被本次改动触碰。

**这不影响功能与资源结论**：查询是"按 `course_id`（btree 索引）取本课程的题 → 展开 → 一次
`GROUP BY`"，单课程百题量级下 plan 与执行都在亚毫秒量级，**不建预聚合表**的判断不变
（第二事实源比这点扫描代价贵得多）。我在 B3 侧只记录，未改 `MAIN/docs/contracts/changes/CHG-0020-*.md`
——那属于 MAIN 的文档权威。

## 4. 验证（真实命令与原始读数）

```text
uv sync --all-groups --reinstall-package autograder-platform
  -> Installed 1 package in 12ms
     ~ autograder-platform==0.1.0 (from file:///D:/VScodeProjects/AutoGrader/MAIN/sdk)
     （PowerShell 因 uv 写 stderr 报 [exit code: 1]，安装本身成功；本模块既有记录同此）

uv run ruff check .
  -> All checks passed!

uv run pytest
  -> 373 passed, 6 warnings in 55.08s
```

`--reinstall-package` 是必须的：`uv` 不会因为路径依赖的版本号没变就重装 SDK。

### 4.1 `CHG-0020` §8 五条 + 路由顺序，逐条对应的测试

| §8 | 测试（`tests/test_questions_api.py`） |
|---|---|
| 1 合计恒等式，且**不是** `sum(tags)+untagged` | `test_coverage_identity_is_tagged_plus_untagged_and_the_tag_sum_is_larger` |
| 2 跨课程 `403`；学生只统计已发布 | `test_coverage_is_forbidden_across_courses_and_students_only_count_published` |
| 3 `published_only=false` 计入草稿，且与列表可见集合一致 | `test_coverage_agrees_with_the_list_visibility_rule` |
| 4 响应无任何 `question_id`/`student_id` | `test_coverage_response_carries_counts_only` |
| 5 空标签库：`tags == []`、`untagged == total`，不是报错 | `test_a_bank_where_no_question_is_tagged_reports_untagged_not_an_error` |
| 路由顺序（§2.1） | `test_coverage_route_precedes_the_question_path_parameter` |
| 鉴权/必填参数分支 | `test_coverage_requires_authentication_and_a_course_scope` |

几处刻意的强断言：

- **§8.1 的夹具让两个候选恒等式真的分叉**：`COURSE_B` 里放两道**同时带两个标签**的题和一道无标签题，
  于是 `sum(tags)=4`、`total=3`、`untagged=1`，`sum + untagged != total` 被**显式断言为不成立**。
  若夹具只放单标签题（`sum == total`），这条断言会在错误实现下同样通过。
  `COURSE_B` 在全会话中**没有被任何其他测试成功写入**（`test_questions_api` 有一次以跨课程身份
  创建、拿到 `403`；`test_auth`/`test_versions` 也只对它做被拒的跨课程操作），
  所以这里用的是绝对计数而非增量。
- **§8.3 与列表逐标签比对**，而不是与常量比对：对每个自有标签，`coverage` 的计数必须等于
  `listQuestions?tags=<tag>` 实际返回的条数，在默认与 `published_only=false` 两种取值下都成立。
  单边改动必然失败。
- **§8.4 四条独立判据**：顶层键集合**从契约文件读出**（不是本仓库常量）；嵌套项键集合为
  `{tag, question_count}`；任何键名都不以 `_id` 结尾（唯一例外是回显的 `course_id` 作用域）；
  刚创建的 `question_id` 不出现在响应原文里，且除作用域外响应中不存在任何 UUID 形态的取值。
- **§8.5 用第三个课程 `COURSE_C`**（新增到 `tests/conftest.py` 的 token 夹具）：覆盖度是聚合，
  想要精确期望值就需要一个没有别的测试往里写行的作用域；`COURSE_A` 会被每一个 HTTP 测试追加题目。
- **路由顺序同时做结构断言与行为断言**：前者断言 `router.routes` 里字面段的声明位置在参数化路由之前，
  后者断言该路径确实返回聚合体，并且 `{question_id}` 兄弟路由仍正常 404（没有被字面路由遮蔽）。

### 4.2 一处被实测纠正的预期：`422` 先于 `401`

第一版测试断言"无令牌 + 无 `course_id`"得到 `401`，实测是 `422`：

```text
assert client.get(COVERAGE).status_code == 401
E  AssertionError: assert 422 == 401
```

原因是 FastAPI 先校验端点签名（`course_id` 必填），端点的函数体——也就是取 token 的那一步——
根本没运行。契约只声明该操作会返回 `401` 与 `422`，没有规定两者先后，因此我**没有**为这一个端点
单独改成"鉴权优先"：那会让它与同样有必填 query 参数的 `POST /questions?course_id=` 行为不一致，
属于契约未要求的自造差异。测试改为如实钉住实际行为：带上作用域后无令牌 → `401`；缺作用域 → `422`。
这一点已写进 `docs/api/contract.md` 的错误约定一节。

## 5. 未执行 / 未验证（如实）

| 未执行 | 原因 |
|---|---|
| 真实宿主（MAIN composition host）装载后的端到端调用 | 与本模块既有口径一致：本轮证据是 `TestClient` + 真实 PostgreSQL（55434）；宿主装载、真实 JWT、B1 客户端未跑 |
| 106 道题、103 个已发布版本的真实题库上的覆盖度读数 | 本环境只有测试库；测试夹具是自造的 3/10 道题量级。**未**在真实题库上核对过任何数字 |
| `published_only` 在 100+ 题量级下的性能与 plan 稳定性 | 未做基准；§2 的 plan 是 10 行数据下的实测，不是规模结论 |
| `ix_questions_tags` 在当前数据量下是否被 planner 选中 | 未测。本文只断言"集合返回函数展开无法使用该 GIN 索引"，以及 `listQuestions?tags=` 的谓词是 `jsonb @>`（该索引的用途） |
| MAIN 侧跨模块门禁 `python -m autograder_host.checks` | **未通过，但原因与本轮改动无关**：`check_module_installations()` 在导入模块代码之前就中止，报 MAIN 的 venv 里 `autograder_b2` 与 `autograder_b3` 的**已安装副本与工作树不一致**（B2 我不曾触碰，可见这不是本轮引入的）。该 venv 由 MAIN 维护且正被并发写入，B3 未擅自 `uv sync` 进去。可独立运行的三个纯文本门禁单独跑过（在**最终文档状态**下重跑）：`check_module_governance()` → ok；`check_no_replacement_characters()` → ok；`check_document_encoding_and_links()` 失败且只剩 **3 条死链，全部在 B2**（`B2/docs/README.md` ×2、`B2/docs/requirements/traceability.md`，均指向尚未创建的 `phase-06-ai-advisory-score.md`），**B3 无一条** |
| 会话期间 `MAIN/contracts/**` 与 B3 共享文件的并发写入 | 文件时间戳显示有本模块之外的写入者：`B3/src/autograder_b3/plugin.py`、`B3/module.yaml`、`B3/contracts.lock` 与 `MAIN/contracts/openapi/b3.yaml` 同批落在 17:43:46/47（早于本轮第一次写入 17:52:38），而 `MAIN/contracts/openapi/common.yaml` 落在 18:00:02（**本轮之内**）。本轮**未**改这些文件。核验时点：`b3.yaml` = `0.14.0`、`contracts.lock.contract_version` = `0.14.0`、`module.yaml` / `plugin.py` = `0.14.0`、`migration_head` = `20260915_0004`，四者一致；覆盖度用例在 18:00 之后重跑仍全绿。若 MAIN 随后再升契约版本，`contracts.lock` 需要由 MAIN 的 relock 流程同步 |

## 6. 文档影响

- 本文件（新增）。
- `docs/README.md`：新增状态表行（标签覆盖度），工具链行读数由 `364 passed` 更正为实测 `373 passed`，基线指向本阶段。
- `docs/api/contract.md`：新增端点行、路由计数 16 → 18、覆盖度语义小节、`422`/`401` 先后约定。
- `docs/testing/cases.md`：新增 `TC-TAG-03` / `TC-TAG-04` / `TC-TAG-05`，并更正"当前测试实现"表的例数
  （此前表内合计 362，与实际 366 已漂移；现按 `pytest --collect-only` 的实测值列出）。
- `docs/requirements/traceability.md`：`RQ-B-3-20` 行补覆盖度实现与证据；基线指向本阶段；
  覆盖自检补一条（含 `CHG-0020` §5 措辞与实测不符的记录）。
- `docs/requirements/acceptance.md`：新增**验收 3-20-11**（覆盖度只有计数）；更正 3-20-7 下
  "筛选与覆盖度是一件事"的记录错误（筛选属 `CHG-0013`，覆盖度属 `CHG-0020`）；`RQ-B-3-*` 验收项计数
  32 → 33；基线指向本阶段。
- `docs/collaboration/onboarding.md`、`docs/testing/strategy.md`、`docs/runbooks/local-development.md`：
  这三处引用的是"**当前分支**的命令输出"，此前停在 `362 passed` 与 `20260915_0003 (head)`（在本轮之前
  就与实测的 366 漂移）。既然本轮改变了这个读数，一并更正为实测值并指向本文件。
- **未改**：`MAIN/contracts/**`、`B3/module.yaml`、`B3/contracts.lock`、`B3/pyproject.toml`、
  `B3/alembic/**`、`plugin.py` 的 `descriptor.contract_version`。
