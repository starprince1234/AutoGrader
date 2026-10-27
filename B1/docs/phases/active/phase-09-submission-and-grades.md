# Phase 09 — P10 作答/提交闭环与 P11 成绩、导出、学习提示（成员四）

- **状态**：已完成（代码与定向自动化检查）；**浏览器端到端与真实 B2/B3/B4 联调未执行**
- **契约**：`MAIN/contracts` 从 `0.22.0` 升到 `0.23.0`（CHG-0030）；五份 `contracts.lock` 同步
- **涉及页面**：P10 `/submit/:questionVersionId`、P11 `/grades`

## 1. 改了什么

### P10 读题 → 写代码 → 提交 → 看结果

- 双栏布局：左 42% **冻结题干**（`QuestionVersionPublic.stem_markdown` + `stem_assets`，受控读取），
  右 58% 编辑器；`<900px` 单列堆叠。题目标题/版本/语言/动态上限固定在顶部；不再显示 base64 字节量。
- 版本不是 `published` 时给出“尚未发布，暂不能作答”，**不渲染编辑器与提交入口**（服务端仍独立拒绝）。
- 本地草稿：恢复前先询问（`Modal`），选择“不恢复”只清当前 `ownerId + question_version_id` 的键；
  自动保存只在恢复询问结束后启动，避免覆盖学生刚输入的内容。
- 删除文件改为 `Modal` 确认（“移除 {path}？未提交的内容会一并移除。”）；新增文件受 `max_files` 约束。
- 提交成功把真实 `submission_id` 写回 URL；202 无 `run_id` 时轮询 `GET /submissions/{id}`，拿到后
  **再补一次** URL。刷新走 `GET` 恢复状态并从最后 sequence 续读 SSE，不重复 `POST`。
- 终态：`completed` 显示“查看本次结果”（带真实 `evaluation_run_id` 跳 P11）；`rejected`/`failed`
  只显示状态与原因，**不显示 0 分**；无 run id 时提供“查看成绩记录”。

### P11 成绩与证据

- 筛选：课程 / 课序 / **题目选择器（“标题 · vN”，不再手抄 UUID）** / 起止时间（本地输入 → UTC RFC 3339，
  `from > to` 字段级提示且不发请求）。
- 教师表格：学号、姓名（B4 授权名单按 `(section_id, student_id)` 关联）、题目、提交时间、状态、
  得分/满分、结果、证据；映射不到显示“名单中未找到”，不把平台 ID 当学号。
  学生表格：题目、提交时间、状态、得分/满分、结果；无班级/他人/导出入口。
- 汇总：明细视图自动取完全部游标页；仅在完整时显示平均分/通过率/未完成数与分布图，否则显示
  “数据不完整，暂不提供全班汇总”。平均分/通过率只基于 `completed` 且有分数的行。
- 导出：`Modal`（Drawer 形态）选择 XLSX/CSV、固定必需列、勾选可选列；导出 query 与页面筛选一致，
  文件由 B2 生成；成功显示“成绩已下载”，失败显示可读原因/request_id。
- 证据下钻：`?evaluation_run_id=` 直接打开同一次 report；关闭清 URL。技术摘要收进 `<details>`；
  评语 provenance 收进 `<details>`；AI 参考分**只在教师视图**渲染。
- 学习提示：仅学生、仅 `completed` run；`not_requested`/`pending`/`ready`/`failed`/`unavailable`/
  `not_applicable` 逐态显示；1–3 条按“知识点 → 排查方向 → 自检思路”逐层展开，隐藏/展开不改记录、
  不重复请求；尾部“返回作业”回到 P10（保留本地草稿）。

## 2. 实际执行的检查

环境：Windows；无 `git`/`pnpm` 在 PATH。使用运行时自带的 Node（v24.21.0）直接调用项目脚本，
等价于 `pnpm lint / typecheck / test / build`（在真实仓库 `Documents\\GitHub\\AutoGrader` 中执行）：

```text
node node_modules/eslint/bin/eslint.js . --max-warnings 0     # exit 0
node node_modules/typescript/bin/tsc -b --pretty false        # exit 0
node node_modules/vitest/vitest.mjs run                       # 49 files / 416 tests passed
node node_modules/vite/bin/vite.js build                      # 成功（仅既有 chunk 体积警告）
```

- `api:check` 等价验证：用同一 `openapi-typescript@7.13.0` 从 `MAIN/contracts` 重新生成四个文件，
  与仓库中的生成物**逐字节一致**（`drift=False`）。
- 新增/更新的定向用例：
  - `src/features/submission/SubmitPage.test.tsx`（草稿先询问）
  - `src/features/submission/SubmitPage.flow.test.tsx`（题干+编辑器、拒绝恢复、删除确认、终态跳转、draft 版本）
  - `src/features/grades/GradesPage.test.tsx`（自动取完分页、导出 Drawer、403 原样展示）
  - `src/features/grades/GradesPage.p11.test.tsx`（UTC 时间范围、身份缺失、可选列、深链、参考分可见性、题目选择器）
  - `src/features/hints/LearningHintsSection.test.tsx`（7 个提示状态/权限/逐层展开用例）

## 3. 未验证（不得写成已通过）

- **浏览器端到端**：Playwright 需要真实 B2/B3/B4 与课程数据；本环境未启动服务、无真实课程/题目。
- **390 / 768 / 1440 px 视觉验收**：仅通过 CSS 断点与 DOM 结构静态检查，未截图核对。
- **真实 SSE**：断线重连/`Last-Event-ID` 由既有单元测试覆盖；真实网络中断与多 worker 行为未实测。
- **Monaco chunk 加载失败**：编辑器降级路径有既有测试；真实 chunk 屏蔽未在浏览器验证。
- **真实导出文件**：未打开 B2 生成的真实 XLSX/CSV（需要 B4 名单与数据库）；格式/列/公式注入由
  B2 测试覆盖，B1 只断言请求参数与下载反馈。
- **真实学习提示生成**：需要配置 provider 与真实 completed run；模型输出质量、时延、预算 429 未实测。
- **B3 `learning-context`**：契约 `0.18.0` 已提供（`QuestionLearningContext`）；B2 消费者默认使用该路径，但本环境未配置 B3 通道，未联调。

## 4. 已知限制/待联调

- 按组长反馈清理了本组页面的**用户可读文案**：P10/P11 不再出现“契约 / DTO / B2 / B4 / 字段名 / 状态码”
  这类面向开发者的表述；错误提示改成用户能执行的话术；教师证据表的英文列名改为中文；
  绑定摘要等仍保留在默认收起的“技术详情”里。
- 截图中的两处 P06 文案（“公开题目 DTO 中不存在……”与“契约的 `course_id` 声明为必需查询参数……”）
  与页头“契约: 0.17.0”**不属于本组页面**：P06 属成员三，页头/外壳属成员一；已转交对应负责人。
- B1 的“返回作业”链接到 P04 `/assignments`；`assignment_id` 未随 `evaluation_run_id` 传递（契约无此字段）。
- 学生成绩表的“题目”列使用 `question_version_id`（学生不能浏览全量题库）；教师侧显示“标题 · vN”。
- 深链状态下（`?evaluation_run_id=`）明细表被收窄到该 run；“谁还没交”标签仍按课程/课序全量判定。
- 导出列的可读“课程编号/课序号/题目名称”不在 B2 拥有的数据内；当前列为课程/课序/题目版本的 ID。
  若课程要求人类可读编号，需要 B2 通过 B4/B3 授权接口解析（新的跨模块调用，需与 MAIN/成员二/三协商）。
