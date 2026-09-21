# B2 组件职责与扩展点

## 组件清单

| 组件 | 职责 | 输入 | 输出 | 依赖 |
|---|---|---|---|---|
| **接收层（receiver）** | API 端点 `POST /api/v1/submissions`；IMAP 邮件适配器；信封校验、SHA-256 核对、幂等、作用域校验 | 外部 `SubmissionEnvelope`（B1 请求 / 邮件附件） | 内部 `SubmissionEnvelope` + `submission_id` | 存储层；B4 JWT 校验 |
| **存储层（storage）** | 内容寻址存储（SHA-256 去重）+ 元数据落库 + 保留/清理 | 归一化后的文件集合 | artifact 内容引用 + `submission` 元数据 | `b2` schema |
| **静态检查器（static checker）** | Python `ast` / Bash `bash -n` + 结构化规则；输出逐条细节评价；高危阻断 | 内部 `SubmissionEnvelope` + 语言枚举 | 静态检查结果 + 规则列表 | 无（不 import 待测模块） |
| **安全判定（security）** | 静态高危模式识别 + 执行期沙箱违规捕获 → `security_finding` | 静态结果 / runner 违规信号 | `security_finding` 记录 + 阻断决策 | runner、沙箱策略 |
| **runner** | nsjail 沙箱内按 package 逐用例执行；资源限制与进程树清理 | evaluation package + 提交文件 + runner policy | 逐用例原始执行结果 | nsjail、cgroup v2、seccomp |
| **比较器（comparator）** | 实际输出 vs 期望输出，按题目声明策略 | 逐用例原始结果 + 期望 + 比较策略 | `CaseEvidence` | 无 |
| **报告器（reporter）** | 汇总静态/安全/逐用例证据 → 总评 + 细评 + 绑定摘要 | 全部中间证据 | `evaluation-report`（受 schema 约束） | 比较器、安全判定 |
| **导出器（exporter）** | 按课程/班级导出 XLSX/CSV；防公式注入 | 成绩查询结果 | 导出文件 | 存储层查询 |

## 依赖方向与核心边界

- 单向依赖：`receiver → storage → static/security → runner → comparator → reporter → exporter`。
- **核心不改、扩展可加**：runner 核心只认识 evaluation package 的**抽象结构**与三类 adapter（`stdin` / `file` / `callable`）的抽象签名；语言相关细节放在语言插件/规则目录里，不影响 runner 核心。

## 扩展点

### 1. 新增题型（不改 runner 核心）

B3 的三种 adapter（`stdin`、`file`、`callable`，见 [evaluation-package.schema.json](../../../MAIN/contracts/schemas/evaluation-package.schema.json)）是 B2 runner 的稳定接口。新增一种题型 = 新增一个 adapter 实现，必须：

- 实现统一内部签名 `run_case(package, submission) -> CaseEvidence`（签名先冻结，见 [../collaboration/workpackages.md](../collaboration/workpackages.md)）。
- 在注册表登记 adapter 类型 → 处理器映射。
- **不**修改 runner 核心的沙箱启动、资源限制、进程树清理、证据汇总逻辑。

判据：新增 adapter 只新增文件 + 一处注册，不触碰 `runner` 核心模块。

### 2. 新增静态规则（不改检查器核心）

静态检查器核心只做"加载规则 → 遍历 → 汇总 severity/位置/说明"。新增规则 = 新增一个规则对象（规则 ID + severity + 匹配逻辑），注册进规则目录即可。判据：不修改检查器调度循环。

### 3. 新增比较策略（不改比较器核心）

比较器核心按策略名分发（精确、忽略行尾空白、数值容差等），策略名由 B3 package 声明。新增策略 = 新增一个策略函数并注册；学生代码无法影响策略选择。

### 4. 新增导出格式（不改导出器核心）

导出器核心产出"行集合"，格式化为 XLSX/CSV 是策略；新增格式 = 新增 formatter，不修改查询与防注入逻辑。

## 明确"核心不改"清单

以下属共享核心，改动只能由一人执行并同步文档（见 [../collaboration/workpackages.md](../collaboration/workpackages.md) 的串行点）：

- `runner` 的沙箱启动与资源限制策略（见 [../adr/ADR-0002-sandbox-execution-policy.md](../adr/ADR-0002-sandbox-execution-policy.md)）。
- 提交状态机取值（MAIN 锁定，见 [models.md](models.md)）。
- 事件 outbox 实现。
- `plugin.py` 的 `descriptor` 声明（与 `module.yaml` 同步）。
