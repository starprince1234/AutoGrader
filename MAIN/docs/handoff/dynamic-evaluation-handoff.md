# 代码动态评测 · 交接文档（给 B3 题库 / B2 评分开发）

> 日期：2026-11-17 　目标读者：负责题目逻辑（B3、B1 出题页）与评分逻辑（B2、AI 评语）的开发人员。
> 本文先说**环境已经做到哪一步**，再给出**要实现的三项需求**与**落点清单**。

## 0. 一句话结论

云端 UNIX 沙箱 + Python 环境**已经存在并已在生产验证可执行代码**，不需要再装环境。要做的是业务改造：
**题目增加测试用例录入 → 发布前 10 用例全过 → 学生代码跑同一套用例 → AI 评分上下文加“通过数”（双轨）。**

语言范围**只保留 `python` 与 `bash`**（契约 `language.schema.json` 已是此枚举）。题目只出 stdin/stdout 型的简单题；**不出邮件、网络、文件系统管理类 UNIX 题**。

## 1. 环境现状（已核实，不是推测）

| 项 | 状态 | 证据 |
|---|---|---|
| 同一台云服务器的 UNIX 沙箱 | 已就绪：nsjail，cgroup v2 子树，`/var/lib/autograder/workspace`（`drwx--x--x`）| 2026-11-17 `diagnose` 运行 44048566888 |
| Python 环境 | `/usr/bin/python3`；沙箱内 `PATH=/usr/local/bin:/usr/bin:/bin`，`PYTHONHASHSEED=0`，`PYTHONDONTWRITEBYTECODE=1`，UTF-8 | `B2/src/autograder_b2/runner/policy.py` |
| Bash 环境 | `/usr/bin/bash`（沙箱内 `/bin/bash`）| 同上 |
| 沙箱身份 | 作业以 `nobody`(65534) 运行，无网络，只读根，工作区绑定到 `/workspace` | `policy.py` |
| 评测链健康 | `/health/ready` 中 B2 `evaluation-chain: ok`（后端点、工作区根、版本均就绪）| `https://api.91idc.me/health/ready` |
| **生产真实执行** | 对已发布题目 `7fed91c6-…` 调 `POST /api/v1/reference-verifications` → `cases_total=1, cases_passed=1`（真实 Python 在沙箱里跑通）| 本次实测 |

### 默认资源上限（`policy.py` 的 `LedgerPolicy`，可由包内 `security_policy` 收紧）
`max_cpu_seconds=10`、`max_wall_seconds=20`、内存见 `LEDGER_MAX_MEMORY_BYTES`、`max_processes`≤64、输出≤1 MiB。
10 个用例**逐用例各起一次沙箱**（旧模式，包里没有 `execution` 字段）；请据此估算 10 用例总耗时（简单题每例通常 <1s）。

### 未验证项（如实告知）
- **Bash 路径没有在生产实测**：代码与沙箱同一条路径，但本次只跑了 Python。开发完成后请各跑一次 bash 题的参考答案验证。
- 并发上限未压测；10 用例 × 多学生同时提交时的排队情况需要评估。
- `diagnose` 工作流会在末尾因 `journalctl --grep` 无匹配返回 1 而显示红色失败，**不代表环境有问题**（实际检查项全部输出正常）；顺手把该命令加 `|| true` 即可。
- CI 只能“静态”验证；沙箱执行必须看线上（`reference-verifications` 或真实提交）。

### 日常如何确认环境可用
1. `GET https://api.91idc.me/health/ready` → B2 `evaluation-chain: ok`。
2. 管理员登录后 `POST /api/v1/reference-verifications {"question_version_id": "<uuid>"}`。返回 `503` = 沙箱不可用（绝不会假装成功）。
3. 服务器诊断：GitHub Actions → *Deploy AutoGrader backend* → `operation=diagnose`。

## 2. 已有能力（直接复用，不要重造）

- **评测包契约**：`MAIN/contracts/schemas/evaluation-package.schema.json`，示例 `examples/evaluation-package.json`。
  `tests[]` 已含 `case_id / name / ordinal / weight / visible_to_student / stdin / args / expected_stdout / expected_exit_code`。
  **`visible_to_student` 默认 false，天然支持“题干 1 个公开 + 评测 10 个隐藏”**。
- **发布门禁**：B3 `publish` 要求绑定 `content_digest` 的“参考答案验证凭证”（`B3/src/autograder_b3/domain/attestations.py`）；
  B2 `POST /api/v1/reference-verifications` 逐用例跑参考答案，**只有 `cases_passed == cases_total` 才让 B3 记存证**（`B2/src/autograder_b2/reference/verification.py`）。
  编辑题目会使存证失效（摘要变化）。
- **逐用例证据**：B2 评测报告含每个用例的通过/失败、失败分类、权重；`view=teacher` 与学生视图已分级。
- **AI 评语**：`B2/src/autograder_b2/commentary/prompt.py`，输出 `commentary + advisory_score`，已有“证据不足给 null、不得越界”等纪律。
- **AI 出题草稿**：`B3/src/autograder_b3/draft_generation.py`（`normalize_proposal` 校验 AI 提案）。

## 3. 要实现的需求与落点

### 需求 1：题目 = 题干 + 参考答案 + 测试用例（固定 1 公开 + 10 评测）

约定（建议写成 B3 的硬校验，而非前端提示）：

| 类别 | 数量 | 学生可见 | 用途 |
|---|---|---|---|
| 题干示例用例 | 恰好 1 | 是（`visible_to_student=true`），展示在题干 | 让学生理解输入输出格式 |
| 评测用例 | 恰好 10 | **否，仅老师可见**（`visible_to_student=false`）| 发布验证 + 学生提交评测 |

> 待确认：“题干 1 个”是否**也**计入 10 个评测？本文按**独立的 1 + 10**设计（题干示例不参与评测计分）。若要计入，把约束改为“总数 10，其中恰好 1 个公开”，其余不变。请产品/老师确认后再编码。

落点：
- **B3 校验**：`B3/src/autograder_b3/domain/questions.py`、`compile/manifest.py`。现状 `max_cases_per_version=200`（`config.py:82`），需要为新题加“公开=1、评测=10”的强约束。
  这是破坏性规则变更，**旧题**（如作业 PDF 导入的、无用例的题）要明确处理：标记“旧题/仅人工评分”，不得混进新发布门禁（不加兼容层的话需先确认旧题怎么处置）。
- **契约**：用例字段已够，不必加字段；只需在 `MAIN/contracts/` 的描述里写明数量规则，并同步 `contracts.lock`、B1 生成的类型（`B1/src/api/generated/b3.ts`）。
- **老师可见**：评测用例只经老师出题/复核接口返回。`dto/public.py` 的“公开 DTO 不得含隐藏用例”约束保持不变，并加回归测试。
- **B1 出题页**：`B1/src/features/authoring/` 新增“测试用例”编辑区（stdin、期望 stdout、可选退出码、公开/评测标识、10 个计数）。
- **AI 出题要有评测**：`draft_generation.py` 的提案要产出 1+10 用例；`normalize_proposal` 要拒绝数量不符或期望输出为空；**AI 生成的用例必须用参考答案真实跑一遍**（即走需求 3 的验证），不能只信 AI 给的期望输出。建议增加“用例由参考答案自洽验证”的标记，AI 提案未通过验证不得进入发布。

### 需求 2：双轨评分（把测试用例通过数喂给 AI）

- 轨 A（确定性）：沙箱逐用例通过数，权重和满分已由 B2 计算（`weight`，报告里已有）。
- 轨 B（AI）：`commentary/prompt.py` 的证据载荷增加：`cases_total`、`cases_passed`、逐用例通过/失败与失败分类（**隐藏用例只给结果与分类，不给输入/期望输出**，`assert_no_protected_payload` 已有保护，沿用）。
- 需决定并在代码中写明：**最终分如何合成**（例：测试通过率分 × 权重 + AI 建议分 × 权重，或 AI 只作参考不改分）。现有设计中 `advisory_score` 是“参考”，不能被客户端当作正式分显示；若要进入正式分，需改契约与 `ADR-0014` 的约束。**此项请产品/老师先拍板。**
- 提示词需升版本（`PROMPT_VERSION`），并加规则：证据里通过数与 AI 建议分明显矛盾时要在 `rationale` 里说明。
- 改动同步：`B2/src/autograder_b2/commentary/{prompt,service,store}.py`、契约 `b2.yaml` 的评语/报告结构、B1 `features/commentary/`、`features/grades/`。

### 需求 3：动态评测（发布门禁 + 学生提交）

- **发布**：已具备“参考答案真实执行”机制；需要把门槛从“全部用例通过”收紧为“**10 个评测用例全部通过**（且题干示例也通过）才记存证”。逻辑位置：`B2/src/autograder_b2/reference/verification.py` 的 `cases_passed == cases_total`；`B3` 发布端在 `publish` 前再校验用例数量。
- **学生提交**：学生代码走现有 B2 评测链（`POST /api/v1/submissions` → 取评测包 → `runner.run_case` 逐用例）。需要：
  - 保证评测包里的 10 个评测用例都被执行并入报告；
  - 学生视图只展示通过数（如 7/10）与题干公开用例的细节，**不得**泄露隐藏用例输入输出（沿用 `report?view=student` 的分级）。
- **仅 Python/Bash、stdin 适配**：`adapter.kind = "stdin"`；暂不开放 `file`/`callable`。
- 参考答案、隐藏用例仍是敏感数据：不得写入日志（`logging.redaction.FORBIDDEN_FIELD_NAMES` 已列 `reference_answer`），不得出现在事件里。

## 4. 题目设计约束（给出题人 / AI 出题提示词）

- 只出**纯函数式的输入输出题**：读标准输入、写标准输出，确定性，运行 <1 秒。
- Python 仅用标准库；Bash 仅用 coreutils 常见命令（`echo/cat/sort/uniq/wc/tr/cut/awk/sed/grep/head/tail/seq`）。
- 不出：邮件、网络、`ssh`、`cron`、进程/权限管理、读写固定系统路径、需要 root 的题。（沙箱无网络、非 root、只读根，这类题天然跑不通。）
- 输出比较：默认 `ignore_trailing_whitespace`；浮点题设 `numeric_tolerance`。
- 避免随机、时间、并发相关输出。

## 5. 验收清单（线上验收，按 `AGENTS.md` 约定：本地只跑静态检查与受影响测试，推送后看 Actions 全绿）

1. 新建 Python 题：1 公开 + 10 评测，参考答案触发验证 → 10/10 通过才可发布；故意改错参考答案 → 不可发布。
2. 新建 Bash 题，同上（**必须实测**）。
3. 学生提交满分代码 → 通过数 10/10；错误代码 → 通过数 <10；死循环/超时 → 被沙箱终止，记失败，不拖垮服务。
4. 学生与其他学生的接口响应里**抓不到**隐藏用例的输入与期望输出。
5. AI 评语请求体含 `cases_total/cases_passed`，且不含隐藏用例内容。
6. AI 出题产物未经参考答案验证不可发布。
7. 测试账号：管理员 `admin`、学生 `testuser`（密码见项目 `AGENTS.md`）；线上 `https://www.91idc.me`，API `https://api.91idc.me`。

## 6. 待产品/老师确认的决策（编码前）

1. “1 + 10”是独立还是 10 含 1？（§3 需求 1）
2. 最终分的合成公式；AI 建议分是否进入正式分？（§3 需求 2）
3. 旧题（无测试用例的邮件/PDF 题）如何处理：保持人工评分，还是下线？
4. 单题 10 用例的整体评测超时预算，以及学生提交的并发上限。
