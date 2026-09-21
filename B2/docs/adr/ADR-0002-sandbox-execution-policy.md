# ADR 0002 沙箱执行策略（nsjail + cgroup v2 + seccomp）

- **状态**：接受
- **背景**：
  - `RQ-B-2-50` 要求在沙箱中动态执行学生代码并强制资源限制、超时清理进程树。
  - `RQ-B-2-70` 要求执行期捕获联网、越权写、提权等违规并记为安全事件。
  - 约束：`RQ-B-0-20` 锁定部署目标为 Linux（nsjail/cgroup v2 依赖 Linux）；资源账本锁定单并发作业 ≤96 MB RSS / 0.45 vCPU、初始 1 个动态评测并发；`RQ-B-2-50` 验收 #6 禁止 shell 字符串执行。
- **决策**：
  1. **隔离**：nsjail 作为沙箱，叠加 cgroup v2（内存/CPU 限额）与 seccomp（系统调用过滤）。
  2. **禁网**：每个作业独立网络命名空间且无网络（等价 `-N`），任何外部网络访问失败并被记录。
  3. **文件系统**：只读根文件系统 + 每作业独立临时工作区；学生代码只能读写工作区。
  4. **降权**：以非 root 用户执行待测代码。
  5. **固定参数数组**：解释器与 nsjail 启动参数为固定数组（如 `["nsjail", "--config", policy, "--log", log, "--", "/usr/bin/python3", script]`），**禁止** shell 字符串拼接。`--log` 让沙箱自有诊断写进工作区之外的宿主自有文件，与学生流物理分开——执行期归因只读这份日志（学生流可伪造，见 [../phases/active/phase-01-implementation.md](../phases/active/phase-01-implementation.md) 第 26 条）。
  6. **进程树清理**：超时/结束时按 cgroup 或进程组杀掉**整个进程树**，不残留进程。
  7. **资源限制**：CPU/内存/输出/文件数/进程数均有上限；超限归入 `failed` 或该用例失败并记录具体限制项。
  8. **违规归类**：联网/写只读路径/提权 → `security_finding`，评测置 `rejected`（安全原因），区别于 `failed`（技术原因）。
- **后果**：
  - 获得：学生代码无法逃逸沙箱访问宿主、网络或他人数据；资源占用可控，符合资源账本。
  - 获得：违规可观测（`security_finding` + 日志），满足"未检查≠安全"的可区分要求。
  - 付出：**仅 Linux 可验收**；Windows 上动态执行无法验证，只能先交付平台无关部分（比较器、报告、持久化）。
  - 付出：沙箱参数与解释器版本必须纳入"执行环境摘要"（绑定摘要之一），否则可重放性不成立。
  - 遗留风险：seccomp/cgroup 配置细节可能随内核/nsjail 版本变化，需在 Linux 验收时实测并记录版本。
- **关联**：
  - 需求：`RQ-B-2-50`、`RQ-B-2-70`、`RQ-B-0-20`
  - 资源账本：[../../../MAIN/docs/workstreams/B2.md](../../../MAIN/docs/workstreams/B2.md) 第 8 节
  - 架构：[../architecture/boundaries.md](../architecture/boundaries.md)（沙箱安全边界）
  - 测试：[../testing/cases.md](../testing/cases.md)（`T-RUN-*`、`T-SEC-*`）
