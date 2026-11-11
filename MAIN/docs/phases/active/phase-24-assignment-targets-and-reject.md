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
| 10. 契约/锁/迁移 | 当前契约 `0.34.0`;五份 `contracts.lock` 一致 | 本批要升版 |

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

### 2.2 契约(`MAIN/contracts`)

- `b3.yaml`:`AssignmentTarget`(`section_ids: Uuid[]`、`student_ids: Uuid[]`,`additionalProperties: false`),
  `AssignmentPublic.target`(required)、`CreateAssignmentRequest.target`(optional,
  省略 = 全课程),`AssignmentAudience` 保持"解析后的学生 id 列表"不变(B2 只消费它)。
- `b2.yaml`:新增 `POST /api/v1/intake/quarantines/{id}/reject`,请求 `{note?}`,
  响应沿用 `IntakeQuarantineView` 加一个 `notification`(`queued|unavailable`),
  语义:打回 + 向**原发件地址**发失败通知;失败不影响打回本身,响应里如实说"通知没发出去"。
- 版本 `0.34.0 → 0.35.0`;`CHANGELOG.md` 追加;五份 `contracts.lock` 用
  `contract_digest()`(**机器计算**,不许手填)重算。

### 2.3 数据(B3,`b3` schema,迁移)

- 新表 `b3.assignment_target_sections` 与 `b3.assignment_target_students`
  (assignment_id + 目标 id,复合主键);**不**给历史作业写行 = 全课程。
- `Assignment` 的读模型只多一个 `target`,写入用"先删后插"保证幂等。

### 2.4 下游

- **B4**:投递仍按"受众"走,只是受众来源改为 B3 解析后的列表(B4 不解析目标)。
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
- **不发信的情况**:管理员/教师无该课程权限(服务端 403);发件地址为空(记录不带邮箱,
  该字段在隔离视图里是必填,所以实际不会出现)。

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

1. **契约 + 锁**(MAIN):`b3.yaml` 目标、`b2.yaml` reject、`0.35.0`、CHANGELOG、五份锁。
   - 验证:`contract_digest()` 重算后与锁一致;`openapi-typescript` 生成物 `--check` 通过。
2. **B3 目标模型**(迁移 + domain + API + pytest):创建/读取/受众解析/越权 422。
   - 验证:隔离 PostgreSQL 上跑 B3 测试;历史作业(无 target)仍解析为全课程。
3. **B2 reject + 通知**(迁移不需要:处置字段已有;新增 operation + SMTP 发送 + 测试)。
   - 验证:隔离 Linux 上跑 B2 测试;通知失败时响应是 `unavailable` 而不是 500。
4. **B1**:重新生成类型;composer 目标选择;复核页成功/失败与打回;测试。
5. **验收矩阵**(远端 CI + 隔离 Linux / 真实 PostgreSQL):
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
