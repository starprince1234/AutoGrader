# B2 范围与非目标（Scope）

## 范围（我交付什么）

B2 是 AutoGrader 的**评测唯一权威**，交付一条完整评测链：

1. **接收**：经 `POST /api/v1/submissions`（来自 B1）与 IMAP 邮件适配器接收待测代码，二者归一化为同一内部 `SubmissionEnvelope`。
2. **存储**：待测代码按 SHA-256 内容寻址存储，元数据入 `b2` schema。
3. **静态检查**：不执行代码的前提下，对 Python（`ast`）与 Bash（`bash -n` + 结构化规则）做语法与结构合法性检查。
4. **安全判定**：静态高危模式识别 + 执行期沙箱违规捕获，结论落库为 `security_finding`。
5. **动态执行**：在 nsjail 沙箱（cgroup v2 + seccomp）中按 B3 evaluation package 逐用例执行并比较。
6. **评分证据与报告**：生成可解释、可重放的逐项证据与总体/细节评价。
7. **持久化与导出**：评测运行、结果、证据、报告持久化；按课程/班级导出 XLSX/CSV。
8. **结构化日志**：全链路可凭 `correlation_id` 串联，敏感内容脱敏。

对应 9 条需求：`RQ-B-2-10/20/30/40/50/60/70/80/99`（详见 [traceability.md](traceability.md)）。

## 非目标（明确不做什么）

- **不做**题目建模、参考答案、测试用例、assignment、题库维护、统计（属于 B3）。
- **不做**身份认证、角色、课程/课序/选课、审计、用户管理（属于 B4）。
- **不做**任何 UI、名单导入/编辑、成绩页展示（属于 B1）。
- **不**直接读取 `b3`/`b4` schema，不 import B3/B4 的 ORM/Python 包。
- **不**执行用户提供的 shell 字符串；**不**在沙箱外运行学生代码。
- **不**把静态检查未通过伪装为"运行成功"，**不**用 mock 评测充当交付。
- **不**在日志/事件/导出中携带源码、参考答案、隐藏用例、凭据或超范围 PII。

## 角色与用例

| 角色 | 用例 | 通过 B2 的什么能力 |
|---|---|---|
| 学生（经 B1） | 提交代码、查看进度、查看自己的报告 | 提交接收、SSE 进度、报告查询 |
| 教师/助教（经 B1） | 查看全班成绩、导出成绩 | 成绩查询、XLSX/CSV 导出 |
| B3 模块 | 提供不可变 evaluation package | 内部接口消费 |
| B4 模块 | 签发 JWT / 校验课程作用域 | 认证与作用域依赖 |
| 运维 | 观察评测链路日志、定位失败 | 结构化日志 + `correlation_id` |

## 上游输入 / 下游输出

- **上游输入**：B1 的提交请求；IMAP 邮件（附件）；B3 的 evaluation package；B4 的 JWT 作用域声明。
- **下游输出**：提交/评测进度事件（NATS）；评测报告与成绩（B1 查询）；成绩导出文件；去标识化 `evaluation.completed` 事件（供 B3 统计）。

## 术语

本模块专有术语见 [../collaboration/glossary.md](../collaboration/glossary.md)；跨模块标识（`submission_id`、`evaluation_run_id`、`question_version_id` 等）见 [../../../MAIN/docs/api/identifiers.md](../../../MAIN/docs/api/identifiers.md)。
