# Phase 24:作业目标模型与「打回」失败邮件

> 状态:**设计已定,分阶段实现中**。老师确认了两件事:
> 1. 作业改成**显式目标**(多个课序 + 指定学生),不再用"课程级作业"冒充;
> 2. 复核页把邮件结果折叠成**提交成功 / 提交失败**,失败记录有「打回」按钮,
>    打回时向**原发件邮箱**发一封"提交失败"通知。
>
> 本文只记录**这次要做什么、按什么顺序做、怎么验**。历史设计见
> `MAIN/docs/architecture/assignment-workflow.md` 与各模块 phase 文档。

## 1. 现状审计(对着老师给的 10 条起点,以当前 `main` 为准)

老师的起点清单写在更早的版本上,有几条已经过时。逐条核对:

| 起点 | 当前 `main` 的事实 | 结论 |
|---|---|---|
| 1. B3 支持课程/课序内建作业、选多个 published 版本 | `B3/src/autograder_b3/domain/assignments.py` 已有;发布路径有 reference 验证 + attestation | **已存在** |
| 2. 作业只有一个 `section_id` | **过时**:v0.32.0 改为课程级(`AssignmentPublic` 没有 `section_id`,作用于课程当前在读学生) | 与老师现在要的**相反**,本批改回"显式目标" |
| 3. B2 没有 `assignment_id` | **过时**:`b2.submissions.assignment_id` 已有(迁移 0022);envelope、幂等键、事件、worker、成绩过滤、导出全部带它;历史行 `NULL` 且**不猜测归属** | 已存在 |
| 4. 教师看不到逐题状态 | `useAssignmentProgress.ts` 已按 `assignment_id + question_version_id` 取每题最新一条,区分 `not_submitted/evaluating/failed/completed`,并有 `partial/submitted` | 已存在 |
| 5. 教师读答案走 `/submissions/{id}/files` | 已存在,`AnswerViewer` 与 `ManualGradeDrawer` 在用 | 已存在 |
| 6. 学生只有单题页 | **过时**:已有 `/assignments/{assignment_id}` 工作区:左侧题目列表、右侧粘贴或选 `.txt`、一次一题一个 `N.txt`、草稿按 `assignment_id+question_version_id+student_id` 隔离、报告可看 | 已存在 |
| 7. 邮件只收 `.txt`、按题号映射 | `intake/attachments.py`:`ALLOWED_ATTACHMENT_EXTENSIONS = {".txt"}`;正文要求课序/姓名/学号/作业编号;一次性核验码绑定 `(student, course, section, assignment)` | 已存在 |
| 8. 邮件与网页同一套提交 | `intake/service.py` 走同一个 `persist()` 与同一个 worker;`assignment_id` 必填;From 需要一次性核验码,不信任头部 | 已存在 |
| 9. 工作区有在途修改 | `git status` 干净,`HEAD = 62f9e65`,`origin/main` 同步 | 无需保留 |
| 10. 契约/锁/迁移 | 契约已升到 `0.38.0`,五份 `contracts.lock` 一致(见 §5) | 契约部分已完成 |

**所以真正缺的只有两件事**:作业的**显式目标**,以及复核页的**打回 + 通知**。
其余是"核对 + 补测试",不是重写。

## 2. 目标模型(改什么)

### 2.1 语义(先定清楚,后面都跟着它)

- `target.section_ids`:非空 = 只有这些课序的学生是受众;
- `target.student_ids`:非空 = 只在这些学生中投放(与课序取**交集**);
- 两者都空 = **全课程**(历史作业与"整门课"的作业都是这个形态,不需要回填);
- 受众永远按**读取/提交那一刻**的 B4 在读名单求值(不在作业里冻结名单快照):
  学生退课即不再看到、也不能提交;这与现在的 `assignment-audiences` 一致。
- `student_ids` 必须属于该课程(服务端校验),否则 422;`section_ids` 必须属于该课程,
  否则 422。**越权的目标直接拒绝,不是前端过滤。**

### 2.2 契约(`MAIN/contracts`)**已完成**

- `b3.yaml`:`AssignmentTarget`(`section_ids: Uuid[]`、`student_ids: Uuid[]`,两个数组必填、
  `uniqueItems: true`、`additionalProperties: false`;两轴**相交**,皆空 = 全课程),
  `AssignmentPublic.target`(required)、`CreateAssignmentRequest.target`(optional,省略 = 全课程)。
  内部 schema 叫 `AssignmentInternal`(B3 侧 DTO 是 `AssignmentAudience`),它新增
  `section_ids`/`student_ids`(缺键 = 全课程),由 B2 与 B4 名单求交——见 §2.4.1。
- `b2.yaml`:新增 `POST /api/v1/intake/quarantines/{id}/reject`(`rejectIntakeQuarantine`),请求
  `{note?}`,响应 `IntakeQuarantineRejection` = `{record, notification}`(`notification`:
  `queued|unavailable`);语义:打回 + 向**原发件地址**发失败通知,失败不影响打回本身。
  - 响应是**嵌套**的而不是把 `notification` 摊在 `IntakeQuarantineView` 同级:`IntakeQuarantineView`
    是 `additionalProperties: false` 的闭合 schema,用 `allOf` 加同级字段会被任何严格 JSON-Schema
    校验判成非法响应(先前的写法就是这种只有 spec 才成立的形状)。
  - `IntakeQuarantineView` 新增必填 `sender`(`anyOf[string, null]`,历史行 `null`)与枚举值
    `rejected`。原来文档里"发件地址在隔离视图里是必填"的假设是错的:该视图此前**没有**发件人字段,
    隔离表也不存发件人,所以这条要连带补一列(B2 迁移 `20261107_0023`)。
- 版本 `0.37.0 → 0.38.0`(本地原定 0.35.0,合入时上游已占用 0.35.0/0.36.0/0.37.0);`CHANGELOG.md` 追加;五份 `contracts.lock` 用
  `contract_digest()`(**机器计算**,不许手填)重算。五份 `module.yaml` 与四个 `plugin.py`
  的 `contract_version` 同步(否则 `check_manifests` 会在下一次集成时报出来)。

### 2.3 数据(B3,`b3` schema,迁移)

- 新表 `b3.assignment_target_sections` 与 `b3.assignment_target_students`
  (assignment_id + 目标 id,复合主键);**不**给历史作业写行 = 全课程。
- `Assignment` 的读模型只多一个 `target`,写入用"先删后插"保证幂等。

### 2.4 下游

- **B4**:原计划是"投递仍按受众走，B4 不解析目标"。实现时发现这做不到:B3 不持有名单,
  把目标解析成收件人列表需要 B4 的数据;B3 若自己存一份名单副本,那份副本就是会漂移的那份。
  因此改成 **B4 解析目标**:
  - 新增内部端点 `POST /api/v1/internal/course-targets/validate`(目标 id 属于课程,B3 在
    写作业**之前**调用,越权 422 / 课程不存在 404);
  - `POST /internal/assignment-notifications` 的请求体带上 `section_ids`/`student_ids`,
    B4 用同一份过滤收窄自己的名单快照。
  这样"谁能提交"和"通知发给谁"用的是同一个判定,不需要 B3 维护第二份名单。
- **B2**:`_require_assignment()` 已经拿 B3 的 audience;目标变化后**无需改调用点**,
  但要在 `email_verification` 建码时校验 `section_id` 属于目标,否则拒绝(422)。

### 2.4.1 目标必须在 `AssignmentAudience` 这一处穿过(已核对代码)

`B3/api/router.py::get_internal_assignment_endpoint` 是整个目标模型的**唯一瓶颈**:

- 它把 `AssignmentAudience(assignment_id, course_id, title, status, question_version_ids)`
  交给 B2(`B2/integration/assignments.py`),响应里**没有**任何名单事实——B3 不持有在读名单,
  B2 拿 B4 的 enrollment 自己组合;
- 因此目标**不能**在 B3 内部过滤掉:必须把 `section_ids` / `student_ids` 放进这个内部 DTO,
  由 **B2 在组合名单时求交**(课序 ⊆ 目标课序,学生 ⊆ 指定学生;两者都空 = 全课程);
- B2 的落点:`_require_assignment()` 已经拿到该 DTO,过滤放在它与 B4 名单合并的那一步,
  这样网页提交、邮件建码、邮件入库三条路径共用同一个判定,不会出现"网页被拒、邮件被收"。

这条是下一批改 `b3.yaml`(`AssignmentAudience` 加目标字段,内部 schema)与 B2 组合逻辑的
**唯一入口**;B4 的投递受众也要用同一份交集,避免"通知发给全班、提交只允许目标学生"。

- **B1**:`AssignmentComposer` 增加"课序多选 + 指定学生"两块(学生搜索限定在所选课序);
  `AssignmentDetailPage` 展示目标;学生工作区不改(它按 assignment 读,受众由服务端收窄)。

### 2.5 复核页(打回)

- 列表与详情的"识别失败原因"折叠为**提交成功 / 提交失败**;原因码仍记录在数据里,
  只在详情里以"为什么失败"的一句话出现(不再作为主列)。
  **已完成**(`d52cf2c`:列表列改为「提交结果 / 提交失败」,详情保留一句话原因)。
- 未处理记录给「打回」按钮:确认弹窗(备注可选)→ `POST .../reject` → 记录变已处理,
  响应里 `notification=queued` 时提示"已通知发件邮箱";`unavailable` 时明确"记录已打回,
  通知没发出去"。
- **不发信的情况**:管理员/教师无该课程权限(服务端 403,记录也不处置);记录没有可用的发件地址
  (`sender` 为 `null`,历史行就是这样)。**先前这里写的"发件地址在隔离视图里是必填,所以实际不会出现"
  是错的**:该视图此前没有发件人字段,隔离表也不存发件人;契约 `0.38.0` 补了 `sender`,
  B2 迁移 `20261107_0023` 补了列(历史行保持 `NULL`,不回填、不从邮箱头猜)。
- 「打回」在数据里记 `resolution=rejected`(**不是** `dismissed`):作废是"复核者丢弃了这一行",
  打回是"提交失败且已通知发件人",合并会让复核页再也答不出"学生被告知了吗"。因此 B2 需要放宽
  `ck_intake_quarantines_decision` 与 `ck_intake_quarantines_decision_audit` 两支 CHECK
  (同一支迁移 `20261107_0023`),这一条与 §3 里原来的"迁移不需要"相反。

### 2.6 这个目标模型以前有过,后来被删掉了(重要)

`B3/alembic/versions/20261107_0008_b3_assignment_targets.py` 就是**上一版**的目标模型:

- `b3.assignment_sections(assignment_id, section_id)`——**至少一行**是写路径的硬约束;
- `b3.assignment_students(assignment_id, student_id)`——**没有行 = 目标课序全体**,所以
  "不缩小"与"缩小到零人"不会被混淆;
- 同一支迁移删掉了旧的 `assignments.section_id` 列与索引。

随后 `20261108_0010_b3_course_level_assignments.py` 把这两张表删掉,改成课程级作业。
本次是**第二次**引入目标模型,语义与 0008 有一处**关键差异**:

| | 0008(旧) | 现在要的 |
|---|---|---|
| 课序行数 | **至少 1 行**(不能"不指定课序") | **允许 0 行** = 全课程(历史作业就是这个形态) |
| 学生行数 | 0 行 = 目标课序全体 | 同左(0 行 = 所选课序全体;课序也为 0 时 = 全课程) |

因此迁移 **不能照抄 0008**:新迁移 `20261108_0012_b3_assignment_targets_restore.py`
(revises `20261108_0011`)重建两张表,但**不加**"至少一行"约束;写路径按 `target` 为空
时不写行。历史作业不写行即解释为全课程,不做回填、不猜测。

## 3. 实施顺序(每步都能单独验证)

1. **契约 + 锁**(MAIN):`b3.yaml` 目标、`b2.yaml` reject、`0.38.0`、CHANGELOG、五份锁。
   **已完成**(细节见 §2.2 与 §5)。
   - 验证:`contract_digest()` 重算后与锁一致(已做);`openapi-typescript` 生成物 `--check`
     通过(已做);`python -m autograder_host.checks`(本机无 `pyyaml`,未做)。
2. **B3 目标模型**(迁移 + domain + API + pytest):创建/读取/受众解析/越权 422。
   **已完成**(细节见 §5);验收测试已写,但只能在隔离 PostgreSQL 上跑。
3. **B4 目标支持**:`validateCourseTargets`(目标 id 属于课程)+ 发布通知受众按目标收窄。
   **已完成**(契约随本批并入 `0.38.0`)。
4. **B2 reject + 通知**:迁移 `20261107_0023`(`sender` 列 + 放宽 decision/audit CHECK 容纳
   `rejected`)、`IntakeQuarantineRejection` 响应、SMTP 失败通知与测试。**已完成**(B2 路由测试里
   的已知缺口随之清空)。三条提交路径的目标求交**仍未做**,留待下一批。
5. **B1**:复核页「打回」按钮、确认/备注与 `queued|unavailable` 提示。**已完成**;
   composer 的课序多选/指定学生**未做**。
6. **验收矩阵**(远端 CI + 隔离 Linux / 真实 PostgreSQL):
   - 多课序/指定学生/草稿/跨课程/越权;
   - 一份多题作业的 未交/部分/全交/评测中/失败/重交;
   - 两个作业复用同一题目版本,互不串联;
   - 网页粘贴 + `.txt`;邮件合法、多附件映射、重复投递幂等、无效格式、伪造 From、越权;
   - 邮件与网页结果一致;教师看得到授权学生答案;学生看不到他人;
   - 历史 `assignment_id IS NULL` 的行不出现在任何作业视图;分页不把"未加载"当"未交"。

## 4. 明确不做

- 不回填历史作业的目标(空 = 全课程),不把历史提交猜给某个作业;
- 不重建数据库、不向真实学生发信、不创建真实提交、不发布题目;
- 不保留"课程级/目标级"两套并行语义(避免双写);老师确认的就是目标模型。

## 5. 交接:已完成 / 未完成(会话预算用尽)

**已完成并推送**(提交见 git log):

| 层 | 内容 | 提交 |
|---|---|---|
| 设计 | 现状审计 + 目标语义 + 迁移计划 | `ac7e815` `94a3201` `6f06b5d` |
| 迁移 | `20261108_0012` 重建 `assignment_sections/students`,0 行 = 全课程 | `f115ffa` |
| 模型 | `AssignmentSection` / `AssignmentStudent` | `f115ffa` |
| 仓储 | `replace_assignment_targets` / `assignment_targets` | `482a00b` |
| 领域 | `get_assignment_targets()` | `8c0a896` |
| B3 内部接口 | `AssignmentAudience` 输出 `section_ids/student_ids` | `8a8e3d6` `8c0a896` |
| B2 解析 | `AssignmentAudience` 读入目标(缺键 = 全课程) | `8a23d4c` |
| B1 复核 | 列表列改为「提交结果 / 提交失败」 | `d52cf2c` |
| 契约 | `0.38.0`:`AssignmentTarget`/`CreateAssignmentRequest`/`AssignmentPublic.target`/`AssignmentInternal` 目标字段、`validateCourseTargets`、`AssignmentNotificationRequest` 目标字段、`rejectIntakeQuarantine`、`IntakeQuarantineRejection`、`sender`、`rejected`;CHANGELOG;五份锁重算;五份 `module.yaml`+四个 `plugin.py` 同步;B1 生成类型重生成 | `feat/phase-24-targets` |
| B3 写路径 | `create_assignment` 先本地版本校验再向 B4 确认目标,写 `assignment_sections/students`;`AssignmentView.target`;列表批量读目标;`AssignmentPublic.target` 按角色投影 | `feat/phase-24-targets` |
| B3 读路径 | 学生列表在 SQL 里按目标收窄(`NOT EXISTS/EXISTS` 两轴),详情按目标判 `403`;内部受众端点直接复用读模型 | `feat/phase-24-targets` |
| B4 目标支持 | `classes.validate_course_target` + `POST /internal/course-targets/validate`;`materialise` 按目标收窄收件人 | `feat/phase-24-targets` |

**未完成**(下一会话从这里继续,顺序即依赖):

1. **B2 求交**:网页提交、邮件建码、邮件入库三条路径共用同一个
   `audience ∩ B4 enrollment` 判定(课序 ⊆ `section_ids`,学生 ⊆ `student_ids`,
   两者皆空 = 全课程);落点在 `_require_assignment()` 之后的准入分支与
   `email_verification` 建码处。**未做**(打回已单独做完,见 §3 第 4 条)。
2. **B1 composer 目标选择**:课序多选 + 指定学生(学生限定在所选课序);
   `AssignmentDetailPage` 展示目标;测试。**未做**(复核页的「打回」已完成)。
3. **验收矩阵**(见 §3.6):全部**未执行**——本机无 `uv`/PostgreSQL/nsjail,
   只能在远端 CI 与隔离 Linux 上跑。

**本轮已验证**:五份 `contracts.lock` 与 `MAIN/contracts` 的机器摘要一致
(复刻 `contract_digest()` 的算法:按 posix 相对路径排序 + `\0` + 字节);
`openapi-typescript` 生成物与契约一致(`api:check` 等价脚本通过);
五份 OpenAPI 文档用 Redocly core 做过结构校验(`struct`/未解析 `$ref`/`operationId` 唯一)通过;
`B1` 的 `tsc -b` 与 `src/features/assignments` 的 51 个 Vitest 通过;
`B3`/`B4` 改动过的 Python 文件全部 `compileall` 通过。
**未验证**:`uv run ruff`、B3/B4 的 pytest(需要真实 PostgreSQL)、`python -m autograder_host.checks`
(本机无 `uv`、无带 `pyyaml`+`jsonschema` 的解释器),以及任何数据库行为。新写的 test 只能算
"写好了、没跑过"。

**本轮顺手修掉的三处既有缺陷**:

1. `B3/module.yaml` 的 `migration_head` 仍是 `20261108_0011`,而 Alembic head 已是 `0012`
   (上一批加了迁移没同步描述符)。已改为 `20261108_0012`,否则 `checks.py` 的本地一致性检查会失败。
2. `B3/tests/test_migrations.py::test_course_level_assignments_replace_the_target_tables`
   断言两张目标表**不存在**——与迁移 `0012` 相反,是上一批漏改的。已改成
   `test_assignments_carry_a_target_and_no_section_column`(存在、复合主键、索引在、
   `assignments.section_id` 仍不存在)。
3. **权限事实**:迁移 `0012` 只给内部角色 `assignment_sections`/`assignment_students` 的
   `SELECT, INSERT`(目标行随作业写一次,不原地编辑),而"先删后插"需要 `DELETE`——
   带目标的创建会被数据库拒绝。已把写路径改成 `add_assignment_targets`(**只插入**),
   不新增授权;将来若真有"改目标"的写入口,必须连同它的授权一起进新迁移。

**文档**:`B3/docs` 的 9 个文件已按目标模型更新(端点表、模型、schema、迁移、术语、
验收 3-20-6、traceability、README、scope),`MAIN/docs/architecture/assignment-workflow.md`
顶部加了"已被 0.38.0 取代"的说明(其余部分保留为 v0.32.0 设计记录)。

