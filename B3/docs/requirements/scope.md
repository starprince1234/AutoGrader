# B3 范围（Scope）

## 范围：本模块交付什么

B3 是"题库与测试包"服务，向系统交付以下能力（**均已实现**，见 [traceability.md](traceability.md) 与 [../phases/active/phase-01-question-bank-and-packages.md](../phases/active/phase-01-question-bank-and-packages.md)）：

1. **题目聚合**（`RQ-B-3-10`）：一条题目由题干（富文本 + 图片资源）、参考答案（受保护）、测试用例（受保护）组成，三者绑定到不可变 `question_version`。
2. **题库维护与发布**（`RQ-B-3-20`）：按课程组织题目，教师增删改、创建 assignment、显式发布不可变版本。
3. **去标识化统计**（`RQ-B-3-30`）：消费 B2 的去标识化 `evaluation.completed` 事件，聚合题目作答次数、通过率、失败类别分布；**不计算独立作答人数**（事件禁止 `student_id`，见 [CHG-B3-0003](../contracts/changes/CHG-B3-0003-distinct-student-statistic.md)）。
4. **三类测试框架的 package 编译**（`RQ-B-3-40/50/60`）：stdin/argv、file I/O、callable 三种 adapter，把题目版本编译成不可变 evaluation package。
5. **结构化日志**（`RQ-B-3-99`）：`producer=B3` 的脱敏 JSON 日志。

核心可交付物是 **evaluation package** —— B2 runner 直接消费它执行评测。它是 B2↔B3 之间最高风险的耦合点，以 `../../../MAIN/contracts/schemas/evaluation-package.schema.json` 为唯一权威。

**尚未完成（跨模块，必须如实保留）**：B2 runner 端到端联调、NATS durable consumer/DLQ 真实行为、B4 真实 JWKS、统计性能基准、Linux 冷启动验收。

## 非目标（越界即缺陷）

- **不执行**学生代码、不做静态/动态评测、不做安全沙箱（属 B2）。
- **不计算/存储**分数、不生成评测报告/成绩导出（属 B2）。
- **不拥有**评测证据（evidence）、提交物（submission）（属 B2）。
- **不拥有**用户/角色/课程/选课/审计/JWKS（属 B4）；**不提供**前端 UI（属 B1）。
- **不读取** `b2` / `b4` schema；不导入其他模块的 ORM；禁止跨 schema 外键 / SQL / 视图。
- **不恢复**历史遗留：旧 scoring / evidence / report API、旧 `evaluation_runs` 模型、旧迁移、旧测试、`RQ-B-3-70` 编号、任何兼容/别名路由。
- `taskone/` 只作为旧研究材料存档，**不是**需求来源或运行时代码。

## 角色与用例

| 角色 | 用例 | 关键约束 |
|---|---|---|
| 教师 | 创建/编辑题目、上传图片、编写参考答案与用例、创建 assignment、发布版本、查看题目统计 | 课程作用域内；发布前完整性校验 |
| 学生 | 只通过 B1 阅读已发布题干的公开 DTO；**无**直接 B3 数据访问 | 公开 DTO 永不含受保护内容 |
| B2（服务间） | 拉取 `evaluation-package` 执行评测；发布去标识化 `evaluation.completed` | 服务间认证；package 不可变 |
| MAIN / B4（运维） | 装载 B3、聚合健康、注册表查询 | 经 SDK descriptor 与 `module.yaml` |

## 上游输入 / 下游输出去向

- **上游输入**：B4 的 `course_id`/`section_id` 作用域与 JWT/JWKS；B2 的去标识化 `evaluation.completed` 事件；教师通过 B1 提交的题目编辑内容。
- **下游去向**：B1（公开题目 DTO、题干图片受控资源）；B2（`evaluation-package` 内部接口）；统计查询结果（给 B1 展示）。

## 术语

本模块专有术语见 [../collaboration/glossary.md](../collaboration/glossary.md)；全局标识与时间规则见 `../../../MAIN/docs/api/identifiers.md`。

## 责任需求清单

`RQ-B-3-10`、`RQ-B-3-20`、`RQ-B-3-30`、`RQ-B-3-40`、`RQ-B-3-50`、`RQ-B-3-60`、`RQ-B-3-99`（共 7 条）。此外 `RQ-B-0-10`（Python/bash 语言对象）与 `RQ-B-0-30`（Unix 作业表达能力）是全体需求，B3 通过三类 adapter 与 `language` 字段承接其评测对象表达部分（见 [traceability.md](traceability.md) 附注）。
