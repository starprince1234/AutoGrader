# 已删除模块的字节码仍在磁盘上，导致"已清理"的判断是错的

## 现象

`RQ-B-3-70` 与 B3 的旧评分/证据/报告设计据称已从 B3 完整移除：源码文件、路由、模型、迁移与测试都被删除，文档也写明该路径不存在。

但 MAIN 的契约门禁在强化后立即报错：

```text
ValueError: B3/src/autograder_b3/__pycache__/models.cpython-312.pyc:
deprecated B3 scoring artifact resurfaced (evaluation_runs)
```

受影响范围：B3 的编译产物缓存。**运行行为不受影响**（源码已不存在，Python 不会从 `__pycache__` 导入缺失模块），因此这个缺陷不会在测试里暴露，只会误导人。

## 重复触发条件

同一根因在本项目内**在多个独立位置同时成立**（这是它被判定为可复用结论的依据，而非单次偶发）：

| 位置 | 残留内容 |
|---|---|
| `B3/src/autograder_b3/__pycache__/models.*.pyc` | `EvaluationRun`、`EvaluationTestResult`、表名 `evaluation_runs`、`evidence_hash`、`scorer` |
| `B3/src/autograder_b3/__pycache__/api.*.pyc` | 路由 `evaluation-runs/`、`submit_evidence`、`export_evaluation_report`、`report_as_csv` |
| `B3/src/autograder_b3/__pycache__/services.*.pyc` | 服务名 `evidence-scoring`（正是旧门禁禁止的字面量） |
| `B3/src/autograder_b3/__pycache__/schemas.*.pyc` | `EvaluationReportResponse`、`EvidenceSubmission`、`EvidenceResultInput` |
| `B3/src/autograder_b3/__pycache__/repositories.*.pyc` | `EvaluationRun` 仓储查询 |
| 另有 `config`/`db`/`main`/`__init__` 的 `.pyc`（Python 3.12 与 3.13 两套） | 已删除模块的残留 |
| `B3/alembic/versions/__pycache__/20260915_0001_initial_schema.*.pyc` | **已被替换的旧迁移**（现为 `..._b3_schema_baseline`） |
| `B3/tests/__pycache__/test_api.*.pyc`、`test_migrations.*.pyc`、`conftest.*.pyc` | 已删除的旧测试 |
| `B3/scripts/__pycache__/export_openapi.*.pyc` | 已删除的脚本 |

共 19 个孤儿字节码文件。触发条件很明确：**删除源码但不清理生成物**。

## 根因

两个原因叠加，缺一不可：

1. **删除动作只覆盖了源码面（`.py` / `.md` / `module.yaml`），没有覆盖产物面（`__pycache__` / `*.pyc`）**。清理任务的验收标准写的是"不存在旧评分模型、接口、迁移、测试"，被理解为"不存在这些**源文件**"，而磁盘上仍存在它们的**编译表示**。
2. **自动化检查只扫描了文档面**。原 `check_requirement_baseline()` 的实现是：

   ```python
   b3_text = "\n".join(p.read_text() for p in (ROOT.parent / "B3").rglob("*.md"))
   if any(item in b3_text for item in forbidden): raise ...
   ```

   它只收集 `*.md`。于是出现了一个危险的错配：**检查通过 = 文档干净 ≠ 磁盘干净**。检查的存在反而强化了"已清理"的错误信心——这正是本缺陷最值得记录的地方：**一个过窄的检查比没有检查更危险，因为它制造了虚假的安全感。**

## 最终修复

1. 删除全部孤儿字节码：对每个 `__pycache__/*.pyc`，若其对应的 `.py` 不存在，则判定为孤儿并删除（保留仍有源码的 22 个）。
2. 把**实现面**纳入检查：`check_deprecated_b3_artifacts()` 现在扫描 `B3/module.yaml`、`B3/src/**`、`B3/alembic/**`，对 `RQ-B-3-70`、`evidence-scoring`、`evaluation_runs` 做字符串匹配。

### 第一次修复是不完整的（必须记录）

上面第 2 步的初版实现里，扫描循环带了一个 `if "__pycache__" in path.parts or path.suffix == ".pyc": continue` —— 也就是说，**它恰好跳过了这次事故真正发生的那个面**。结果：字节码是被手工删掉的，但"下次再出现会被拦住"这件事**并不成立**。

这一点是在复核中被指出的，随后按下面的方式真正修好：`.pyc` **不再被跳过**，而是以字节方式读取并做 ASCII 字符串匹配。这样做的判据是"**被移除的设计是否仍存在于任何产物面**"，而不是"是否存在补编译产物"——因此：

- 携带 `evaluation_runs` / `evidence-scoring` 等被移除设计的字节码 → **失败**；
- 无对应源码、但不含被移除设计的孤儿字节码 → 属无害构建残留，**不报错**（避免把正常的改名/删除误判为事故）。

**已实测证明**（而不是"看代码觉得对"）：向 `B3/src/autograder_b3/__pycache__/models.cpython-312.pyc` 写入含 `evidence-scoring` 的字节后，门禁立即失败并报出完整路径与命中的字面量；删除该文件后门禁恢复通过。

为什么这能消除根因：检查的作用面从"文档面 + 源码面"扩展到"**包括编译产物在内的实现面**"，使"源码已删但产物残留"变得**不可能静默通过**。

## 预防措施

| 措施 | 现在由什么阻止复发 | 是否已实测 |
|---|---|---|
| 实现面字符串扫描（源码/清单/迁移） | `check_deprecated_b3_artifacts()` —— 源文件、`module.yaml` 或迁移中重新引入旧评分标识即失败 | 是（本次门禁已实际拦截过） |
| **编译产物扫描（本次补上）** | 同一函数对 `**/*.pyc` 做字节匹配；被移除的设计残留在字节码中即失败 | **是（已按"植入 → 失败 → 清除 → 通过"验证）** |
| 追踪矩阵只拦截**需求声明** | 同一函数的正则部分：允许叙述性记录"本项目无该需求"，禁止把它重新列成需求行 | 是（曾触发误报并据此收窄） |
| 文档面检查扩大到全仓 | `check_document_encoding_and_links()` 覆盖五个项目的 Markdown | 是（曾实际拦截 7 条死链） |
| 清理任务的验收定义 | `../collaboration/module-doc-spec.md` 与 `../testing/acceptance.md` 要求清理类任务同时验证**源码面与产物面** | 否（属流程约定，无自动化） |

**配套要求（已落入文档）**：任何"删除功能"的任务，验收时必须同时检查源码、迁移、清单、文档、测试、以及生成物（`__pycache__`、构建产物、缓存目录）。

## 关联证据

- 缺陷发现：`MAIN/host/src/autograder_host/checks.py` 的 `check_deprecated_b3_artifacts()`（强化后立即报错）
- 处置记录：[`../phases/active/phase-00-initialization.md`](../phases/active/phase-00-initialization.md) 第 4.4 节 R-01
- 相关决策：[ADR-0002](../adr/ADR-0002-current-xlsx-requirements-baseline.md)（要求旧 B3 评分路径完整移除）
- 相关门禁要求：[`../testing/acceptance.md`](../testing/acceptance.md) 的 G-07
- 相关的同类教训：[`../phases/active/phase-00-initialization.md`](../phases/active/phase-00-initialization.md) 的 R-02（`RQ-B-4-99` 在文档与检查中同时缺失——同样是"检查与文档口径不一致"这一类别）
