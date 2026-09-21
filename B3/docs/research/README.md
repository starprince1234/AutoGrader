# B3 旧研究材料（Research）

`../../taskone/` 是**2026-09-08 之前**产生的旧研究材料（约 29 个文件、约 11.8 MB），含旧报告（`AutoGrader_B3_需求分析与软件工程实践报告.docx` 等）、Pressman PPT、提取文本（`ppt_text.txt` / `xlsx_text.txt`）、渲染 PDF 与旧 `build_doc.py`。

## 定性：非权威、非运行时、默认不跟踪

- **非权威**：优先级链为 `用户决定 > MAIN 需求表(XLSX) > 思维导图 > B3 旧材料`（`../../../MAIN/docs/requirements/rq-catalog.md` 开头）。`taskone/` 处于最低优先级，与 XLSX 冲突时以 XLSX 为准。
- **非运行时**：不被任何 `src/**`、`tests/**`、`alembic/**`、`pyproject.toml` 引用；不进包（`pyproject.toml` 的 wheel 只打包 `src/autograder_b3`）。
- **默认不跟踪**：`.gitignore` 已排除 `taskone/`。

## 为何保留

仅作为**课程过程证据**存档（历史报告、需求梳理轨迹），供回溯"当时为什么这样想"。它不构成当前需求、API、数据库或实现的权威来源。

## 禁止事项（MAIN 门禁）

- **禁止**从 `taskone/` 恢复旧 scoring / evidence / report API、旧 `evaluation_runs` 模型、旧迁移、旧测试、`RQ-B-3-70` 编号或任何兼容路由。
- **禁止**把 `taskone/` 的报告结论当作需求/验收依据引用进 `requirements/`、`architecture/`、`database/`。
- `tools/reporting/build_doc.py`（34 KB 旧报告生成脚本）同样属于旧材料，`.gitignore` 已排除 `tools/reporting/`，`pyproject.toml` 的 ruff `extend-exclude` 已排除该目录；**不是**运行时代码。

## 与当前文档的关系

当前 B3 需求、架构、接口、数据库、测试全部以 `MAIN` 契约与 `rq-catalog.md` 为唯一权威（见 [../README.md](../README.md) 权威表）。本目录只负责说明"旧材料是什么、为何保留、不得从其中恢复什么"。
