# B2 信任边界与数据边界

## 1. 信任边界（一切外部输入不可信）

| 边界 | 我信任什么 | 我不信任什么 |
|---|---|---|
| 网络入口（B1 提交） | 无（请求本身不信任） | 请求体、路径、文件内容、`content_sha256`、幂等键、任何 header |
| 邮件入口（IMAP） | 无 | 发件人地址、主题、附件、正文、元数据（都可伪造） |
| 认证 | 仅 B4 签发的 JWT 通过本地验签（`iss`/`aud`/`role`/作用域） | token 中除验签后的声明外的任何内容；前端可见性控制不构成任何边界 |
| B3 evaluation package | 仅限"契约已锁定的结构 + 摘要绑定" | package 中的任何可执行内容与路径；不得 import 待测模块 |
| 待测代码 | **完全不信任** | 源码、静态内容、运行时行为（联网/越权写/提权/fork） |
| 存储路径 | 服务端生成的路径 | 客户端提供的任何路径（`..`、绝对路径、反斜杠逃逸） |

**落地规则**：所有外部输入必须经 JSON Schema 校验 + 摘要核对 + 作用域校验；待测代码只在 nsjail 内、以最低权限、无网络、只读根、临时工作区执行。

## 2. 数据边界（只碰 `b2` schema）

- **我只读写** `b2` schema（`submission`、`artifact`、`evaluation_run`、`evaluation_result`、`security_finding`、`email_checkpoint`，见 [../database/schema.md](../database/schema.md)）。
- **我禁止**：
  - 跨 schema 外键、跨 schema SQL/视图/ORM（读 `b3`/`b4` 均禁止）。
  - 使用 `public` schema 共享 ORM。
  - import B3/B4 的 Python 包或 ORM 模型。
- 跨模块引用**只传 UUID**（`question_version_id`、`course_id`、`user_id` 等），需要更多信息时按 ID 拉取或由事件 payload 携带必要字段。
- 运行角色使用最小权限 login role（`autograder_b2`），仅拥有 `b2` 权限。

## 3. 沙箱安全边界

权威决策见 [../adr/ADR-0002-sandbox-execution-policy.md](../adr/ADR-0002-sandbox-execution-policy.md)。要点：

| 项 | 要求 |
|---|---|
| 隔离 | nsjail + cgroup v2 + seccomp |
| 网络 | 完全禁用（`-N` 等价物，无网络命名空间） |
| 文件系统 | 只读根 + 独立临时工作区（每作业一个） |
| 权限 | 非 root，降权执行 |
| 资源 | 单并发作业 ≤96 MB RSS / 0.45 vCPU；初始仅 1 个动态评测并发 |
| 进程 | 超时后清理**整个进程树**，不残留 |
| 启动 | 固定参数数组，**禁止** shell 字符串拼接 |

## 4. 日志脱敏范围

禁止出现在日志 / 事件 / 导出中的内容（见 [../../../MAIN/docs/api/identifiers.md](../../../MAIN/docs/api/identifiers.md) 第 2.1 节与 RQ-B-2-99）：

- 源码内容（学生提交的原始文件字节/文本）。
- 参考答案（任何形式，含文本、哈希、摘要）。
- 隐藏测试用例 ID 与内容、`expected_output`。
- 凭据：访问令牌、刷新令牌、cookie 值、密钥指纹之外的密钥信息。
- 内部存储路径、数据库 schema/表名、内部主机名与端口。
- 超范围 PII（学号之外的明文身份信息）。

**允许记录**：`submission_id`、`evaluation_run_id`、`correlation_id`、`request_id`、错误类别与脱敏后的原因、规则/用例 ID（内部）、状态迁移。
