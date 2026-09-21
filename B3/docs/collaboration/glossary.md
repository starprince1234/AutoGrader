# B3 术语表（Glossary）

B3 专有术语。全局标识 / 时间 / 作用域规则见 `../../../MAIN/docs/api/identifiers.md`（不在此重复）。

| 术语 | 含义 |
|---|---|
| 题目（question） | 逻辑实体，跨版本稳定，用 `question_id` 标识；含题干、参考答案、测试用例三部分 |
| 题目版本（question_version） | **不可变**版本快照，`question_version_id` 标识；发布后内容不可改，"改必新版本" |
| 参考答案（reference answer） | 受保护内容，仅内部角色可读；按契约**没有任何出站路径**（package schema 与公开 DTO 均无该字段），用途是发布完整性校验 |
| 隐藏用例（hidden case） | 受保护的测试用例（含期望输出），`visible_to_student` 默认为 `false` |
| expected output | 用例期望输出（`expected_stdout`/`expected_stderr`/`expected_exit_code`），受保护，永不出现在公开面 |
| assignment | 作业集合（题目 × 课序），只可引用已发布版本 |
| evaluation package | 题目版本编译出的不可变产物，B2 runner 直接消费；含 adapter、测试输入、比较规则、fixture 引用、内容摘要 |
| package 编译（package compilation） | 题目版本 → 不可变 package 的**确定性纯函数**过程，绑定 `package_digest`；唯一入口 `compile_evaluation_package` |
| content_digest | 版本**全部内容**（题干 + 参考答案 + adapter + 用例 + fixture 摘要 + 限制项）的规范化摘要，发布时冻结 |
| package_digest | package 去掉自身摘要字段后的规范化 SHA-256；`content_digest` 相同的版本必然得到相同 `package_digest` |
| 规范化（canonical JSON） | `json.dumps(sort_keys=True, separators=(",",":"), ensure_ascii=False, allow_nan=False)` 的 UTF-8 字节；B2 比对摘要时必须一致 |
| adapter | 测试框架类型：`stdin`（命令行/stdin）、`file`（文件 I/O）、`callable`（import 后调用） |
| adapter manifest | 题目声明的 adapter 参数与比较策略（B3 内部设计，映射到 `evaluation-package` schema） |
| AdapterCompiler | 每种 adapter 的校验 + package 段构造实现；经注册表分派，是"新增题型不改核心"的扩展点 |
| fixture | 供评测用的输入/期望文件，内容寻址存储（`b3.blobs`），以摘要绑定进 package；声明摘要只与 B3 计算的摘要**比对** |
| 去标识化事件 | B2 发布的 `evaluation.completed`，payload 不含源码 / 答案 / 学生身份，B3 仅用于统计 |
| 去重 inbox | `b3.event_inbox`：以 `event_id` 为主键、`evaluation_run_id` 为业务唯一键，保证 at-least-once 投递下不重复计数 |
| outbox | `b3.event_outbox`：事件与业务变更在**同一事务**写出，由投递器异步发送；失败保留重试并记录 `last_error` |
| 公开 DTO / 内部 DTO | 物理分离的两套数据模型（`dto/public.py` / `dto/internal.py`）；公开响应唯一出口是 `serializers.serialize_public` |
| 白名单序列化 | 公开响应按 DTO 字段投影，出现受保护键名即**抛错**（不静默丢弃） |
| 受保护内容 | 参考答案、隐藏用例、expected output、fixture 内容/元数据的统称；数据库层由独立角色隔离（普通角色 `permission denied`） |
| 最小权限角色 | `autograder_b3_app`（公开读 + 统计消费，无受保护表授权）与 `autograder_b3_internal`（发布/编译路径） |
| 统计口径 | 作答次数、通过率（仅以事件显式 `is_pass` 计入）、得分分布（`score/max_score` 的 20 分带）、失败类别分布 |
| unbucketed | 事件缺 `max_score` 时无法分桶的作答计数（`unbucketed_attempt_count`）；B3 **不猜测**分数基准 |
| 绑定摘要 | 评测可重放所需的五类摘要之一（题目版本绑定、测试包摘要由 B3 生成，见 `identifiers.md` §4） |
| CHG | 契约缺口记录（`docs/contracts/changes/CHG-B3-*`）：只描述差异与请求裁决，**不修改** `MAIN/contracts/**` |

## 历史遗留术语（禁止使用）

`scoring` / `evidence` / `report`（旧职责，已移交给 B2）、旧评分运行表名（旧模型）、`RQ-B-3-70`（旧编号）——均**不存在**于当前 B3，禁止恢复。MAIN 的 `checks.py` 会扫描 `B3/src`、`B3/alembic`、`B3/module.yaml`（含 `__pycache__` 字节码）确认这些词零出现。
