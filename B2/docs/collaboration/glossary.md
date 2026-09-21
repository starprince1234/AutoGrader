# B2 术语表（Glossary）

本模块专有术语。跨模块通用标识（`submission_id`、`evaluation_run_id`、`question_version_id`、`correlation_id` 等）见 [../../../MAIN/docs/api/identifiers.md](../../../MAIN/docs/api/identifiers.md)，此处不重复。

| 术语 | 含义 |
|---|---|
| **SubmissionEnvelope（归一化后）** | API 与 IMAP 两条接收路径归一化后的统一内部提交模型（字段契约见 `submission-envelope.schema.json`） |
| **内容寻址存储** | 以文件内容 SHA-256 为去重键的存储方式，同一内容只存一份（见 [../adr/ADR-0003](../adr/ADR-0003-content-addressed-submission-storage.md)） |
| **artifact** | 内容寻址存储中一条文件内容条目 |
| **静态检查器** | 不执行代码的合法性检查（Python `ast` / Bash `bash -n` + 结构化规则） |
| **规则目录** | 静态/安全规则的集合，每条含规则 ID + severity + 说明 |
| **安全判定** | 静态高危模式识别 + 执行期沙箱违规捕获 → `security_finding` |
| **阻断门** | 静态高危结果阻断动态运行，置 `rejected` 的判定点 |
| **runner** | 在 nsjail 沙箱内按 evaluation package 逐用例执行学生代码的组件 |
| **adapter** | B3 声明的三类执行形态：`stdin` / `file` / `callable`（权威见 `evaluation-package.schema.json`） |
| **CaseEvidence（用例证据）** | 单条用例的执行证据：输入摘要、实际/期望输出摘要、判定、失败类别、证据摘要 |
| **比较器** | 按题目声明策略比较实际与期望输出的组件 |
| **绑定摘要** | 提交/题目版本/测试包/runner 策略/执行环境五项摘要，可重放判据 |
| **rejected vs failed** | `rejected`=安全原因（静态高危/沙箱违规）；`failed`=技术原因（超时/资源超限/崩溃/依赖不可用），二者不得混淆 |
| **隔离区（quarantine）** | 邮件无法唯一匹配学生/作业时的暂存态，不自动评分 |
| **检查点（checkpoint）** | IMAP 收取的幂等标记（UID/UIDVALIDITY 或 `Message-ID`） |
| **outbox / inbox** | 事件先写 outbox 再发；消费者按 `event_id` 去重的收件箱模式 |
| **孤儿内容** | 内容已写、元数据未写成功产生的无引用 artifact，可被清理任务回收 |

术语若与 MAIN 术语表冲突，以 MAIN 为准并反馈（走文档变更流程，见 [../../../MAIN/docs/contracts/change-protocol.md](../../../MAIN/docs/contracts/change-protocol.md)）。
