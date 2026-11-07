# Unix 作业评测扩展：邮件、SSH、文件状态与分段评分

本文件是「示例题目」课程 Unix 作业评测改造的跨模块设计基线。所有模块实现以本文件的字段名与语义为准；
公共契约（`MAIN/contracts/**`）是字段的机器可读权威，本文件解释**为什么**这样声明。

## 1. 范围与不变量

目标：让第 1、2、6、7、8、9 题的评测规则真实覆盖题面要求，并让参考答案检验与学生提交走同一条真实判定链。

不可协商项（与既有架构一致）：

1. 普通题默认策略不改：独立网络命名空间（无 lo）、只读根、非 root、seccomp 默认拒绝、cgroup v2 限额、进程树清理。
2. 不通过题目标题/标签/路径/魔法字符串切换行为。行为只由 package 中显式的 `execution` 声明决定。
3. 已有已发布题目的数据与摘要必须保持有效：新字段的可选缺省语义必须使旧内容编译出的 package 字节不变。
4. 沙箱不可用 ⇒ `failed + infrastructure_error`，绝不折算成学生答错；学生答案错误才产生用例级失败。
5. 任何受保护内容（期望输出、fixture 字节、SSH 口令、参考答案）不得进入公开 DTO、事件、日志或学生可访问存储。

## 2. `evaluation-package.schema.json` 扩展

新增**可选**顶层字段 `execution`（缺省 = `{"mode":"per_case"}`，即现有行为）。B3 编译时只有非缺省配置才输出该字段，
因此旧版本重新编译得到的字节与摘要不变。

```json
"execution": {
  "mode": "per_case | single_run | ssh_session | email_delivery",
  "segment_delimiter": "string|null",        // single_run 分段时必填
  "email": {                                  // email_delivery 必填
    "attempt": 1,
    "subject_template": "{student_number}_{attempt}",
    "required_attachment_template": "{student_number}.txt"
  },
  "ssh": {                                    // ssh_session 必填
    "host": "127.0.0.1",
    "port": 22,
    "username": "user01",
    "password": "<protected>",
    "login_timeout_seconds": 10,
    "session_timeout_seconds": 10
  }
}
```

`tests[]` 新增可选字段：

```json
"segment": 0,
"file_checks": [
  {"path": "week6_1.txt", "content_sha256": "<sha256|null>", "mode": "0644|null"}
],
"ssh_check": "service_ready | auth_succeeded | session_closed",
"email_check": "email_received | subject | sender | attachment"
```

校验规则（B3 编译期拒绝 + B2 解析期再拒绝，双端一致）：

- `per_case`：禁止 `segment`/`ssh_check`/`email_check`；`file_checks` 可选（每个用例各自一次运行）。
- `single_run`：`adapter.kind ∈ {"stdin", "file"}`；每个用例恰好声明 `segment` 或非空 `file_checks` 之一；
  `segment` 值必须是 `0..N-1` 的完整集合（N = 声明 segment 的用例数）；声明 segment 时必须给出
  `segment_delimiter`（独占一行的固定标记，长度 8..120，可打印 ASCII，不含行尾空白），且
  `adapter.kind` 必须是 `"stdin"`（分段比较的是程序 stdout）；`file_checks` 元素必须至少声明
  `content_sha256` 与 `mode` 之一。
- `ssh_session`：`adapter.kind == "stdin"`，`language == "bash"`；每个用例恰好一个 `ssh_check`；
  三种 check 每种至多一个；`host` 必须是 `127.0.0.1`；口令只存在于受保护 package。
- `email_delivery`：`adapter.kind == "stdin"`，`language == "bash"`；每个用例恰好一个 `email_check`；
  四种 check 每种至多一个；`attempt` 1..99。

`evaluation-report.schema.json` 扩展（均为可选/可空字段，旧报告仍合法）：

- `case_evidence[]` 新增 `segment: integer|null`。
- `case_artifacts` 新增 `file_checks: array`，元素
  `{path, expected_sha256|null, actual_sha256|null, expected_mode|null, actual_mode|null, matched}`。
- 顶层新增 `execution`：`{mode, invocation_count, evidence_count}`（`invocation_count` = 实际启动的沙箱次数）。
- `static_analysis.checks[].status` 与 `security.status` 允许新值 `not_applicable`；仅 `email_delivery`
  使用（该模式不执行代码，因此静态/安全检查不适用；不是“没检查”）。

## 3. 各模式语义

### 3.1 `per_case`（默认，现有行为）

每个用例一次沙箱执行。唯一变化：`adapter.files` 中 `mount=input` 的 fixture 现在**真的**由 B2 从 B3
取回字节并物化（见 §5），执行前逐字节校验摘要；用例 `file_checks` 在本次运行后检查文件。

### 3.2 `single_run`（第 6、7、8、9 题）

- 一次沙箱执行，完整采集有界 stdout；`adapter.expect_exit_code` 与超时/资源/截断是**整次运行**级判定：
  运行级失败（timeout / resource_limit / crash / security_blocked）⇒ 所有用例带同一失败类别，均不得分。
- 运行成功且声明了 `segment_delimiter`：按“整行等于分隔符”切分 stdout。
  段数必须等于声明 segment 的用例数，且按出现顺序对应 segment 0..N-1。段内容保留原行与行尾换行。
  段数不符（缺/多分隔符）⇒ 所有 segment 用例 `output_mismatch`，消息明确写“分段结构不合法”，
  **绝不**把某一段错位判给其他小题。
- 每个 segment 用例只比较自己的段：期望值来自该用例 `expected_stdout`，比较策略来自 adapter。
- 每个用例的 `actual_output_excerpt`/`stdout_digest` 是该段内容的摘要与摘录，而不是整份 stdout；
  证据、权重、可见性、失败类别沿用现有字段与规则。
- 声明 `file_checks` 的用例不参与分段；它们在运行结束后直接检查工作区文件（见 §3.4）。
- 参考答案检验与学生提交使用同一个批量执行入口（§6）。

### 3.3 `ssh_session`（第 2 题）

一次沙箱执行生成三条用例证据，判定依据是**真实 SSH 服务端事件**（sshd 自有日志）与客户端退出：

- `service_ready`：练习环境内 sshd 真正启动并监听 `127.0.0.1:<port>`。
- `auth_succeeded`：sshd 记录 `Accepted password for <username> from 127.0.0.1`。
- `session_closed`：sshd 记录该用户的 session closed，且 SSH 客户端在时限内以成功状态退出、不是被强杀。

学生提交的脚本被解释为**交互式会话指令序列**，不是交给 shell 的任意程序：

- 第一行必须是 `ssh` 命令；解析后 argv 必须严格形如 `ssh [user@]127.0.0.1`
  （允许 `-p <port>`、`-l <user>`；拒绝 `ProxyCommand`、`LocalCommand`、`-o`、重定向等任何可执行本地命令的参数）。
- 后续每一行在登录成功后按顺序写入远端会话；`exit`/`logout` 关闭会话。
- 环境固定注入：`StrictHostKeyChecking=no`、`UserKnownHostsFile=/dev/null`、只允许密码认证；
  题面已声明不要求处理 `yes` 与 host key（该约定属于评测环境，不属于学生答案）。
- 口令由练习环境在密码提示出现时输入；学生文件里**不能也不应**包含口令（题面要求每行只是命令）。
- 若第一行不是合法 ssh 命令：三条用例全部失败（`output_mismatch`），并给出明确消息；不执行任何 shell。

隔离模型（详细边界见 §7）：sshd、B2 会话驱动与远端 shell 均运行在同一 nsjail 内的独立用户/网络/挂载命名空间中；
NSS 使用的 passwd/shadow 是每次运行生成的临时文件；远端会话被 chroot 到最小练习根。学生代码永远不会在
宿主上下文、默认沙箱或生产账号下执行。

### 3.4 `file_checks`（第 6 题）

执行结束后由 B2 **直接检查工作区文件**（不读学生 stdout）：

- `path` 必须过现有工作区相对路径白名单；解析后仍须在工作区内（挡符号链接穿越）。
- 文件必须存在且是普通文件（`lstat`，符号链接即失败）。
- `content_sha256` 非空时直接对最终字节计算摘要并比较（受保护期望值）。
- `mode` 非空时检查文件权限位（八进制文本，例如 `"0744"`），与进程 umask 无关。
- 失败类别：缺文件 `missing_output`；内容或权限不符 `output_mismatch`；证据写 `file_checks`
  明细（期望/实际摘要与权限、是否匹配）。

**可变工作区的权限边界（B2-owned `workspace_prep` 沙箱 profile）**：物化后的 fixture/提交文件属于
服务账号，而被囚学生身份是 subordinate uid，没有写权限也无法 `chmod`（`chmod` 要求文件所有者）。
因此当某个 package 的用例声明了 `file_checks` 时，runner 使用 **受控准备 profile**：
nsjail 内以“用户命名空间内的 root”（宿主 uid 仍是服务账号，不是宿主 root）启动 B2 固定的准备程序，
它把工作区条目 `chown` 给被囚 uid、目录置为可写，然后 `setgroups/setgid/setuid` 降权，再 `exec`
解释器执行学生脚本。学生代码始终以 subordinate uid 运行；准备程序是 B2 自有代码，不由学生输入驱动。
该 profile 必须保留 seccomp、cgroup 限额、只读根与网络隔离，并只由显式的 `file_checks` 声明触发；
普通题 profile 逐字节不变。

### 3.5 `email_delivery`（第 1 题）

邮件由**现有 IMAP 收件链路**真实接收；该模式不执行沙箱，也不读取学生 stdout。评测读取这次提交在
`b2.email_verifications` 中记录的、来自真实邮件的事实，按共享规则引擎逐项判定：

- `email_received`：存在与本次提交绑定、来自真实 IMAP 拉取的验证记录（含 mailbox/uidvalidity/uid/message-id）。
- `subject`：实际主题等于 `"{student_number}_{attempt}"`（学号来自已核验账号，attempt 来自 package）。
- `sender`：实际发件地址等于该账号已验证邮箱，且等于该学生在该课程/课序绑定的首次发件地址。
- `attachment`：实际附件名包含 `"{student_number}.txt"`。

失败类别：字段不符 `output_mismatch`；没有收到邮件 `missing_output`；邮件收件依赖不可用（心跳过期/
未配置）⇒ 整次运行 `failed + infrastructure_error`，绝不记作学生答错。

参考答案检验：`email_delivery` 没有可执行程序，参考答案声明为一份受保护的「合成邮件回执 JSON」，
经**同一个规则引擎**判定。这是参考解自检，不是生产邮件端到端验证；报告与文档必须如实区分。

## 4. 邮件验证的所有权证明与一次性关联

威胁模型：`From` 头可伪造，仅按发件地址匹配会让攻击者把提交冒充给任意账号；重放、跨用户认领与并发竞态也必须挡住。

机制：

1. **账号邮箱所有权**：账号邮箱在注册时已通过平台外发的一次性验证码证明归属（B4 现有注册流程），
   本特性不新增“邮箱是否验证”的旁路；收发信身份查询走 B4 现有 internal 端点。
2. **一次性核验码**：学生用已认证会话请求本题的核验码（随机高熵、只在响应中出现一次、库里只存 SHA-256），
   绑定 `(student_id, course_id, section_id, question_version_id)`，有效期 30 分钟。
3. **邮件必须携带核验码**：收件端解析邮件正文（现有 MIME 解析已产出有界正文摘录）中的核验码，按哈希查找挑战，
   校验未过期、未消费、发送者账号 == 挑战所有者。校验通过后在**同一事务/行锁**中标记消费并写入
   `email_verifications`（同时写入发件地址、主题、附件名、message-id、mailbox/uidvalidity/uid）。
4. **首次发件地址绑定**：首次通过验证的邮件写入 `b2.email_sender_bindings(student_id, course_id, section_id, sender)`；
   之后同一学生同一课程/课序的邮件必须使用同一地址，否则隔离（`email_sender_mismatch`）。
5. **邮件成为提交**：验证通过后经现有 `SubmissionIntakeService.persist` 创建提交（文件 = 通过附件安全闸门的附件），
   并把 `email_verifications.submission_id` 与提交同事务写入；评测由现有 worker 触发。
6. **失败反馈**：核验码端点提供状态查询（pending / received / rejected(reason) / expired / intake_unavailable），
   学生能看到“邮件没到/被拒/依赖不可用”的区别。被拒邮件进入现有隔离区并记录原因码，绝不冒充提交。
7. **依赖故障**：收件拉取循环写心跳（`b2.intake_state.last_successful_pull_at`）。评测发现验证记录缺失且心跳
   过期/未配置时判 `infrastructure_error`，不判学生失败。
8. **防竞态**：挑战消费用 `SELECT ... FOR UPDATE` + 唯一约束（`email_verifications.challenge_id` unique）；
   邮件重复投递由 `(mailbox, uidvalidity, uid)` / message-id 唯一约束兜底；绑定写入用唯一约束 + `ON CONFLICT`。

新增公开端点（B2，契约）：

- `POST /api/v1/email-verification/challenges`（学生）→ `201 {challenge_id, code, expires_at}`。
- `GET /api/v1/email-verification/challenges/{challenge_id}`（学生）→ 状态与脱敏原因码。

新增内部表（b2 schema，迁移）：`email_verification_challenges`、`email_verifications`、`email_sender_bindings`、`intake_state`。

## 5. 受保护 fixture 字节交付链

现状：package 只声明 fixture 摘要，B3 已把 fixture 字节存在 `b3.blobs`，但 B2 没有取回通道 ⇒ 第 6 题的初始文件
无法复现。补齐：

- B3 新增 internal 端点 `GET /api/v1/question-versions/{id}/fixture-contents?mount=input`（serviceToken only），
  返回 `{question_version_id, items: [{path, mount, content_sha256, size_bytes, content_base64}]}`；
  总量与单项有上限（单项 ≤ 1 MiB，总量 ≤ 4 MiB），超过即 409/413 而不是截断。只返回 `mount=input`；
  `expected_output` 的字节没有读取端点（比较只需摘要）。
- B2 新增 `FixtureProvider` 端口与 B3 实现；runner 在执行前把 input fixture 物化进工作区：
  路径白名单 → 解析后仍位于工作区 → 已有同名提交文件即失败（fixture 不得覆盖学生文件，学生文件也不得覆盖 fixture）
  → 写字节并校验摘要与 package 声明逐字节一致。摘要不符/缺失 ⇒ `infrastructure_error` 证据（技术故障，不是学生错）。
- `adapter.kind == "stdin"` 在声明了 `files` 时也必须把 `files` 编进 package（现有遗漏；没有已发布版本依赖该遗漏，
  实现时需在远端核对线上已发布版本）。

## 6. 执行接口与计数

- 冻结签名 `run_case(package, submission)` 保留给 `per_case`。
- 新增批量入口 `run_cases(package, batch_input) -> tuple[CaseEvidence, ...]`：
  一次批量 = 一次沙箱执行（`single_run`/`ssh_session`）或一次投递核验（`email_delivery`）。
- `single_run`/`ssh_session`：编排层与参考答案验证都只准备**一份**批量工作区、只调用一次批量入口。
- `email_delivery`：编排层跳过工作区/静态/安全/沙箱，调用 `DeliveryVerifier` 端口；参考答案验证调用同一规则引擎。
- 计数：`runner_invocations` = 实际沙箱启动次数（`per_case`=用例数，`single_run`/`ssh_session`=1，`email_delivery`=0）；
  用例证据数不变。报告新增 `execution.invocation_count`/`evidence_count` 如实表达。
- 逐用例进度帧仍按证据逐条产生（cases_done 1..N），但不得把它解释成执行次数。

## 7. SSH 练习环境安全边界

- 位置：当前 AutoGrader 服务器上的隔离 nsjail 作业；不使用宿主 root、生产 `autograder` 账号作为练习账号，
  不暴露宿主 SSH、数据库、Docker socket 或宿主文件系统私有内容。
- 网络：独立网络命名空间，仅启用 lo；`network=false` 常量语义不变（无外部网络）。SSH 专项允许
  `socket/connect/bind/listen/accept` 等本地网络系统调用，但仅存在于该模式，普通题策略不得改动。
- 进程：nsjail 内 root 只是用户命名空间内的 root（宿主 uid 映射为服务账号或 subordinate id），
  不是宿主 root；远端会话被 chroot 到最小练习根并以 subordinate uid 运行。
- 学生代码只在远端会话（chroot + subordinate uid + seccomp + 资源限额）中执行；本地只运行严格 argv 的真实
  `ssh` 客户端。
- 清理：服务实例、sshd 进程、密钥/口令文件、工作区、命名空间在成功、失败、超时后都清理；
  超时整棵进程树 SIGKILL；不得残留监听端口或临时密钥。
- 失败语义：练习环境起不来（nsjail 不可用、ssh 二进制缺失、监听失败）⇒ `infrastructure_error`；
  学生脚本不是合法 ssh 登录/登录失败/未退出 ⇒ 学生侧失败；两者的证据必须可区分。

## 8. B3 存储、编译与兼容

- `b3.question_versions.execution_manifest jsonb NULL`（NULL = per_case）；
  `b3.test_cases.execution jsonb NULL`（用例级 segment/file_checks/ssh_check/email_check）。
- 版本内容摘要（`content_digest`）的规范化载荷在**非缺省** execution/用例执行声明时才加入相应键；
  缺省/空声明不进入载荷 ⇒ 已有已发布版本的内容摘要保持可复现。
- package 编译：`execution` 仅在非缺省时输出；用例级执行字段同理。旧内容重新编译字节不变。
- 写入路径：`QuestionDraftInput` 接收 `execution` 与 `TestCaseInput` 的新字段；`QuestionDraftContent` 回读它们，
  供作者界面编辑。`QuestionVersionPublic` 只暴露 `execution_mode` 枚举（不得暴露 SSH 口令等受保护配置）。
- 发布前校验与参考答案检验使用同一套编译结果。

## 9. 题目与参考答案更新（第 1、2、6、7、8、9 题）

- 第 1 题：`execution.mode=email_delivery`、`attempt=1`；题面增加“先在课程页面获取一次性核验码、写入邮件正文第一行”、
  “核验状态 pending/received/rejected/expired 的含义；收到合格邮件后平台自动创建提交并评测”的说明；
  参考答案为合成回执 JSON（四个字段断言全真）。真实邮件由 IMAP 收件链路消费核验码后**创建提交**，
  不需要再在网页上传同一份答案。
- 第 2 题：`execution.mode=ssh_session` + 练习账号配置；用例 `ssh_check` 三条；参考答案为两行
  `ssh user01@127.0.0.1` / `exit`。
- 第 6 题：`adapter.kind=file` + 两个 input fixture（`week6_1.txt`、`week6_2.dat`，初始权限 0644）；
  `execution.mode=single_run`；用例 1 `file_checks` 校验 `week6_1.txt` 期望内容摘要；
  用例 2 `file_checks` 校验 `week6_2.dat` 权限 `0744`。参考答案真实执行 vi 批处理与 chmod，不打印权限文本冒充。
- 第 7–9 题：`execution.mode=single_run` + 固定分隔行；用例按各小题（原题 1..5 / 1..2 / 1..2）拆分，
  `expected_stdout` 只包含该段期望输出；参考答案在每段之间输出固定分隔行，且最后一段后不输出分隔行。
- 题面/附件说明中写明固定分隔行原文、独占一行；不得在期望输出中出现该标记。

## 10. 验证矩阵（远端 CI / 隔离 Linux）

1. 邮件：正确绑定、伪造发件人/错误核验码拒绝、重放与并发消费一次成功、依赖故障判基础设施。
2. SSH：真实登录、错误口令、超时、非 0/异常退出、禁外网、清理无残留、学生脚本非 ssh 命令被拒。
3. 文件：vi 改对/改错、权限对/错、缺文件、符号链接、fixture 摘要不符（技术故障）。
4. 分段：全对、单段错、空段、缺/多分隔符、输出截断、超时、非 0 退出；断言只启动一次且参考答案/学生判定一致。
5. 回归：普通 Python/Bash 题、网络隔离、非 root、seccomp、资源限制仍有效。
6. 契约：schema 校验、锁摘要、B2/B3 接口测试、B1 类型与 lint。

## 11. 已明确不在本轮验证范围

- 线上真实学生作业提交（用户明确要求本轮不做）。
- 生产真实邮件端到端（除非另行授权发送测试邮件）。
- Python/C/C++ LeetCode 风格 OJ 扩展。

## 12. 实现期裁决（记录在案）

1. **`single_run` 的 adapter kind**：允许 `{"stdin", "file"}`（第 6 题是 file 题的一次执行）；
   只有声明 `segment` 的用例才要求 `adapter.kind == "stdin"`。B2 package 解析与 B3 编译使用同一谓词。
2. **`file_checks.mode` 格式**：统一为四位规范八进制 `^0[0-7]{3}$`（如 `"0744"`）；契约已从
   `^[0-7]{3,4}$` 收紧。三位/五位形式被拒绝，避免"实际权限格式化后逐字比较"出现两种写法。
3. **B2/B3 边界取严**：fixture path 全局唯一（跨 mount 也拒绝）；`stdout=numeric_tolerance` 时
   `numeric_tolerance` 必须给值。B3 的写入/编译路径与 B2 解析器一致；B3 诊断端点以 lenient 只读方式
   保留对存量声明的诊断能力。
4. **`execution_manifest` 的存储位置**：放 `b3.question_versions`（按 §8），因此 B3 的普通 app role
   在表级 SELECT 时能看到该列（包含练习账号口令）。接受理由：该口令是**题面已公开的练习账号口令**、
   不是生产凭据；公开 DTO 只投影 `execution_mode`，draft-content/package 走受保护通道，事件与日志不携带。
   残余风险：任何新增的 B3 代码都必须继续把该列当作受保护内容，不得把它序列化进公开响应或日志。
5. **邮件 `reason_code` 保留值**：`subject_mismatch` / `attachment_missing` 预留给"需要 package 模板的
   收件端检查"；当前收件端不产生它们，评测端以用例级 `output_mismatch` 如实报告主题/附件问题。
6. **第 1 题参考答案检验是合成回执自检**：它复用与学生评测完全相同的规则函数，但没有经过真实 IMAP
   收件；因此它不能被称为"生产邮件端到端验证"。
7. **SSH 练习环境的 subuid 约束**：宿主只给服务账号分配了 1 个 subordinate UID/GID。SSH profile 只映射
   调用者 uid 与这一个 subordinate id；sshd privsep 账号在运行时生成的 NSS 文件里使用 65534（已映射），
   **禁止**映射 `subuid+1`（newuidmap 会拒绝）。
8. **workspace_prep 的 chown 依据**：被囚进程是 userns root（宿主 uid = 服务账号），工作区文件也属于服务账号，
   因此内核对"属主对自有文件 chown"的路径允许其使用 userns 内的 CAP_CHOWN（Linux 6.6 实测通过）；
   学生代码随后 setuid 到 subordinate uid，不再拥有该能力。该 profile 只由显式 `file_checks` 触发。
