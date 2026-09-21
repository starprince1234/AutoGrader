# Phase 08 既有失败清零（`4 failed` → `0 failed`）

- **起因**：MAIN 新增第四道门禁 `check_test_evidence()` —— 每个模块的实测记录必须**与当前契约一致**，
  且**该次运行是通过的**。B2 的记录因此恒为红。MAIN 的裁决是**修**，不是"容忍"也不是"豁免"：
  容忍"已登记的失败"就是把红记成绿，而一条恒红的门禁最终会被关掉（`strategy.md` §5 已就
  `check_shared_vocabularies` 记过这条）。
- **本批硬约束**：不许削弱断言、不许放宽期望值、不许加 `skip`/`xfail`/重试/条件跳过。
  **产品缺陷改产品，测试缺陷改测试**，且两种都要说清"为什么另一边是对的"。

## 结论（四条失败，全部是**测试缺陷**；产品代码只改了两处过时的**理由**，行为一行未动）

| 测试 | 根因 | 类别 | 改了什么 | 单测读数（前 → 后） |
|---|---|---|---|---|
| `test_xlsx_timestamp_truncates_like_the_api_not_rounds` | 期望值编码了**迁移 0012 之前**的数据模型 | 测试 | 拆成两条：① 库参与（先断言库里的值，再要求 JSON/CSV/XLSX 与它逐字符一致）；② 导出层不变量用**合成行**注入微秒 | `1 failed in 4.54s`（`assert '…124Z' == '…123Z'` @797）→ 两条各 `1 passed` |
| `test_cursor_pagination_is_complete_and_disjoint` | 按**插入顺序**断言，而产品按 `(created_at, submission_id)` 定序；前三行被 `timestamptz(3)` 压成并列键 ⇒ 顺序由随机 UUID 决定（≈1/6 通过） | 测试 | 新增 `_expected_order()`（从库里读回并按**声明顺序**排）作为期望；另加显式断言"并列组内必须是 `submission_id` 升序" | 旧断言单独跑 `2 failed in 4.22s`（见"修复前读数"）→ `1 passed`；**12 次连跑 12 passed** |
| `test_pagination_is_keyset_not_offset` | 同一根因：删的是"插入的第一行"，不是"第一页那一行" | 测试 | 用同一条 `_expected_order` 取第一页与"已翻过的行"，删 `expected[0]`；keyset 性质不变（OFFSET 会返回 `expected[3:5]`，被抓住） | 同上（旧断言 `2 failed`）→ `1 passed`；12 次连跑含在其中 |
| `test_every_timestamp_column_has_millisecond_precision` | `from alembic.versions import …` 是**错的路径假设** | 测试 | 新增 `_load_migration_module()`：按**文件路径** `importlib.util.spec_from_file_location` 加载 0012，只取 `MILLISECOND_COLUMNS` | `1 failed in 3.65s`（`ModuleNotFoundError: No module named 'alembic.versions'` @579）→ `1 passed` |

## 1. 时间戳那条：为什么`.124` 是对的、`.123` 是错的

`submissions.created_at` 是 `timestamptz(3)`（迁移 0012 的表标准），而 Postgres 在**写入时**就把
亚毫秒**四舍五入**到毫秒。本机实测（同一实例，只读事务里也就够了）：

```sql
INSERT INTO t VALUES (timestamptz '2026-09-15 08:00:00.123999+00'),
                     (timestamptz '2026-09-15 08:00:00.123499+00');
SELECT to_char(x, 'YYYY-MM-DD HH24:MI:SS.US') FROM t;
-- 2026-09-15 08:00:00.124000
-- 2026-09-15 08:00:00.123000
```

因此种子 `.123999` 存进去就是 `.124`，此后**任何一层都再也看不到 `.123999`**：JSON / CSV / XLSX
一致报 `.124Z`。旧期望 `.123` 成立的前提是"列保留微秒、对外格式化时截断"——那在 0012 之后不再存在。
**产品行为是对的**：对外时间是"库里那个毫秒"，不是"调用方原始微秒的截断"。

那条用例真正要守的不变量是**三个面逐字符一致**（XLSX 曾让 openpyxl 的序列号四舍五入，导致同一时刻
在 CSV 是 `.123`、在 XLSX 是 `.124`）。库列收窄之后这条不变量**在库里已经无法被触发**，所以新的第二条
用 `_synthetic_row()` 直接注入微秒，把"导出层先截断"钉住——这不是放宽，而是换到唯一还能验证它的入口。

**产品侧只改了两处过时的理由**（行为一行未动）：`grades/query.py::encode_cursor` 与
`grades/export.py::_xlsx_moment` 的 docstring 都写着"库里是微秒精度"，那在 0012 之后是错的。
游标仍承载 ISO 全精度、XLSX 仍在写盘前截断——理由改成"不低于列精度"与"守导出层不变量"。

## 2. 分页那两条：产品**有**确定性 tie-breaker，是测试假设了插入顺序

MAIN 的初判是"并列排序键下没有确定性 tie-breaker ⇒ 分页会漏/重"。**核实结果：不成立**，
证据是产品代码本身（`grades/query.py::_statement`，两处同源）：

```python
.order_by(submissions.c.created_at.asc(), submissions.c.submission_id.asc())   # 全序
# 游标谓词与它逐项对应，因此 keyset 判定有唯一解：
sa.or_(submissions.c.created_at > moment,
       sa.and_(submissions.c.created_at == moment, submissions.c.submission_id > identifier))
```

`created_at` 允许并列（批量导入、同一毫秒），补上主键之后是**全序**；游标用 ISO 全精度
（**不**再截断一次，`encode_cursor` 的 docstring 写明了理由），所以不漏不重。
错的是测试：它按**插入顺序**断言，而插入顺序从来不是契约承诺；前三行被 `timestamptz(3)` 压成并列键后，
相对顺序由**随机 UUID** 决定——这正是那对用例 flaky（≈1/6 通过）的机制。

### 修复前读数（旧断言原样跑）

把两条旧断言原样写成临时用例跑一次（跑完即删，不留在交付里）：

```text
>       assert [row.submission_id for row in whole.items] == [str(item) for item in identifiers]
E       AssertionError: assert ['29e85bf6-9e…900b37f', …] == ['e1c66c41-05…900b37f', …]
E         At index 0 diff: '29e85bf6-9e20-44cd-81c7-e17aa99c931b' != 'e1c66c41-05e2-486b-a029-fec6b042709b'
>       assert [row.submission_id for row in first.items] == [str(item) for item in identifiers[:2]]
E       AssertionError: assert ['209d102e-cc…536db1887ff0'] == ['d78fd7a3-24…749854b778c0']
E         At index 0 diff: '209d102e-cc67-4beb-8aa8-749854b778c0' != 'd78fd7a3-24bf-43de-81cb-982faa1a3e9d'
FAILED …::test_old_expectation_of_complete_and_disjoint
FAILED …::test_old_expectation_of_keyset_not_offset
2 failed in 4.22s
```

即：返回的是**声明顺序**（同毫秒的三行按 `submission_id` 升序），不是插入顺序。产品没漏也没重。

### 修复后读数（确定性）

```text
单跑两条：        2 passed
连跑 12 次：      12 passed / 0 failed
```

若并列键上**没有**确定性 tie-breaker，12 次连续通过的几率是 `(1/6)^12 ≈ 4.6e-10`——因此这个读数是
"tie-breaker 存在且是主键"的直接证据，而不只是"这次运气好"。新断言里另有一条**显式**钉住它：
`assert [row.submission_id for row in whole.items[:3]] == sorted(expected[:3])`。

## 3. `alembic.versions` 那条：**不是收集期错误**，只影响 1 条

按要求先判性质，读数如下：

```text
pytest tests/test_migrations.py --collect-only -q   -> 23 tests collected in 0.05s   # 收集正常
pytest …::test_every_timestamp_column_has_millisecond_precision -q
  -> ModuleNotFoundError: No module named 'alembic.versions'  @tests/test_migrations.py:579
python -c "import alembic; print(alembic.__file__)"
  -> D:\VScodeProjects\AutoGrader\B2\.venv\Lib\site-packages\alembic\__init__.py
python -c "import importlib.util; print(importlib.util.find_spec('alembic.versions'))"
  -> None
```

导入在**测试体内**，所以它只让这 1 条失败，**没有**隐藏一整族未被收集的用例。
根因是路径假设：`B2/alembic/` 在本仓库是 Alembic 的**脚本目录**（没有 `__init__.py`），而
site-packages 里的 `alembic` 是**常规包**——按 PEP 420，命名空间目录只是"找不到常规包时的兜底"，
常规包优先，于是 `alembic.versions` 永远解析不到本仓库的那份。

**没有**用"给 `B2/alembic/` 加 `__init__.py`"这条看似最直接的路：那会让 `import alembic` 在测试进程里
解析到本仓库的脚本目录、把真正的 Alembic 遮蔽掉（迁移文件里的 `from alembic import op` 立刻失效），
等于把一个测试缺陷升级成产品缺陷。改成按文件路径加载那个迁移模块，只取它声明的常量。

## 4. 完整套件与门禁现状

```text
uv run ruff check .        -> All checks passed!
uv run pytest              -> 1298 passed, 9 skipped, 2 xfailed, 2 warnings in 386.22s (0:06:26)   [exit 0]
uv run pytest（本批末次复跑）-> 1298 passed, 9 skipped, 2 xfailed, 2 warnings in 360.74s (0:06:00)   [exit 0]
（收集 1309 条 = 1298 + 9 + 2；本批把那条时间戳用例拆成两条，故比上一批的 1308 多 1）
```

两次全量的**计数逐项相同**、耗时不同（同一实例上的正常抖动），因此本批的读数不是"一次运气好"。

`test_grades_query_export.py` + `test_migrations.py` 两个模块单独跑：`62 passed, 1 xfailed`。

**MAIN 门禁**：本批**没有**碰 MAIN 的 venv、**没有**重写 `MAIN/.runtime/evidence/B2.json`
（MAIN 要求保留那份诚实的 `exit=1` 记录，重录由 MAIN 在重启窗口后统一做）。
因此"test evidence"这条门禁要等 MAIN 重录之后才会看到本批的绿色读数——**本批给出的是它重录时应当得到的读数**。

## 5. 未做 / 未验证（如实列出）

- **没有**重录 `MAIN/.runtime/evidence/B2.json`（MAIN 明确要求不要动）。
- **没有**碰 MAIN 的 venv、没有重装任何模块（MAIN 明确要求）。
- 本批的绿色读数是在 **B2 自己的 venv** + 测试实例（PostgreSQL `55433`）上得到的；
  契约版本/摘要随仓库实时变化，因此记录与门禁比对以 MAIN 重录时的时刻为准。
- 那对分页用例的"12 次连跑"是确定性的**统计证据**，不是形式化证明；形式化部分由新断言
  （并列组内 `submission_id` 升序）直接钉住。

## 6. 文档同步（本批改动）

| 文档 | 改了什么 |
|---|---|
| [README.md](../../README.md) | 基线段落由"`3 failed, 1294 passed…` + 与 MAIN 新门禁冲突"改写为 **`1298 passed, 9 skipped, 2 xfailed`（收集 1309，exit 0）**；删掉已解决的"门禁不可能转绿"告警，改为"套件侧阻塞已消失、记录仍留给 MAIN 重录"；保留历史读数并订正"不是缺 tie-breaker" |
| [phase-05](phase-05-quarantine-and-quality-surfaces.md) | "未执行/未验收"第 1 条加"后续（已解决）"注，并**订正**其中"排序退化/缺 tie-breaker"的表述 |
| [phase-06](phase-06-ai-advisory-score.md) | 两处加"后续（已解决）"注；环境读数表第 1 条同样订正 |
| [phase-07](phase-07-reference-verification.md) | 两处加"后续（已解决）"注（含"B2 未改写 `B2.json`"的重申） |
| [cases.md](../../testing/cases.md) | `B2-GRD-02/03` 补"按声明全序重算期望 + 并列组显式断言"；新增 `B2-GRD-13`（导出层时间不变量，两条用例）；新增 `T-DB-14`（毫秒精度用例的登记与 phase-08 修法） |
| 本文件 | 新增 |

改完文档后重跑（**同一会话内实测**）：

```text
uv run ruff check .                                          -> All checks passed!
MAIN/.venv/Scripts/python -c "checks.check_module_governance(); \
  checks.check_document_encoding_and_links(); checks.check_no_replacement_characters()"
                                                             -> governance+links+encoding+replacement-characters: ok
```

**仍未做**：其它篇章里的历史读数（phase-05/06/07 的旧 summary 行）**按原样保留**，它们是当时的事实，
只在上面并列了"后续（已解决）"；`docs/testing/strategy.md`、`docs/collaboration/onboarding.md`、
`docs/runbooks/local-development.md` 里更早的 `1134/1145 passed` 读数**本批未改**（属历史遗留的过期读数，
与四条失败无关，已在交付说明里指出）。
