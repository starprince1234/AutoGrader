# 复核记录（第二轮·定点复裁）：B2「事件 outbox 与 inbox」punch list 处置

- 复核者：独立复核者（只读实现；未改 `src/**`、`docs/**`、`module.yaml`、`contracts.lock`、`alembic/**`、`MAIN/contracts/**`）
- 前置记录：`.pytest-tmp/REVIEW_EVENTS_RECHECK.md`（第一轮，已由 B2 原文归档到 `docs/phases/active/reviews/REVIEW_EVENTS_RECHECK.md`）
- 本轮探针：`.pytest-tmp/reviewer_probe_round2.py`（36 项，**36/36 PASS**）、`.pytest-tmp/reviewer_marks_audit.py`
- 真实依赖：PostgreSQL `localhost:55433`、NATS JetStream `localhost:54223`（未启停容器）

## 0. 本轮实跑命令与结果（可复算）

```powershell
$env:PYTHONIOENCODING="utf-8"
$env:B2_TEST_DATABASE_URL="postgresql+psycopg://autograder_platform:change-me-local-only@localhost:55433/autograder"
$env:B2_TEST_NATS_URL="nats://localhost:54223"

uv run ruff check .                                   # All checks passed!
uv run pytest -q -p no:warnings                       # 1101 passed, 3 skipped, 1 xfailed in 156.06s
uv run pytest tests/test_events_outbox.py tests/test_events_probe_review.py -q
                                                      # 38 passed in 35.41s   （用户报 37，见 §3 备注）
uv run python .pytest-tmp\reviewer_probe_round2.py    # 36/36 checks passed

# 降级 A：无 DB、无 broker
Remove-Item Env:B2_TEST_DATABASE_URL; $env:B2_TEST_NATS_URL="nats://127.0.0.1:1"
uv run pytest tests/test_events_outbox.py -q          # 8 passed, 15 skipped
uv run pytest tests/test_events_probe_review.py -q    # 3 passed, 12 skipped
uv run pytest tests/test_events_outbox.py tests/test_events_probe_review.py -q
                                                      # 11 passed, 27 skipped

# 降级 B：有 DB、broker 不可达
$env:B2_TEST_DATABASE_URL="postgresql+psycopg://...@localhost:55433/autograder"
uv run pytest tests/test_events_outbox.py -q
    # 1 failed, 11 passed, 11 skipped   <-- 与用户/文档声称的 "11 passed, 11 skipped" 不符（见 N11）
    # FAILED tests/test_events_outbox.py::test_consumer_failure_is_logged_even_without_an_error_hook
    #   EventBusUnavailable: connect failed on nats://127.0.0.1:1: NoServersError
```

## 1. 逐项裁决（对应提问 1–5）

### Q1 上轮 #5 的三项 —— **① PASS ② PASS（有一处假数字必须改）③ PASS**

- **① `docs/README.md:33/34` 已解除**：`:35` 指向 `tests/test_intake_api.py`（422/401/403/202 与幂等）+ `tests/test_submission_loader.py`，`:36` 指向 `tests/test_b2_routes.py`（断言 `text/event-stream` 与断线重连）；两行均保留 `待实现` 并逐行标注"状态词待 L1-5 刷新"；横幅 `:29` 改为"三行明确错误"；其余 6 行（`:37-42,43,45,46`）不再有空 `—`。被引用的 3 个测试文件均真实存在（`Test-Path` 全 True）。**上轮的"与现实矛盾"已消除。**
- **② 铁律三落位已解除**：`docs/phases/active/phase-01-implementation.md`（151 行）含目标/输入/完成条件/实际变更对照表/**验证记录**（真实命令+结果+"未执行项及原因"：真实 IMAP/TLS、跨进程并发上限、宿主 worker 各一行）/**风险与遗留项 G-EVT-1..8 + N4 + N6 + N7 + 沙箱平台限制 + 无调度器**（每条含"已确认事实 / 原因 / 恢复验证"）/文档影响检查；复核记录原文归档到 `docs/phases/active/reviews/`（5 份，含我的第一轮记录），并已在 `docs/README.md:57` 文档地图登记。
  **但 `phase-01-implementation.md:71` 是一条与现实不符的"通过"声明**：写"有 DB、无 broker → 事件模块 `11 passed, 11 skipped`"，实测为 **`1 failed, 11 passed, 11 skipped`**（见 N11）；`:70` 的 outbox 数字 `8 passed, 14 skipped` 也已过期（实测 `8 passed, 15 skipped`，因新增 T-EVT-020）。→ 必须按实测改。
- **③ 探针逐用例标注已解除**：AST 提取为 **8×`@db @broker`、3×`@db`、1×`@broker`、3×纯逻辑**，无模块级 `pytestmark`（R3.1–R3.2）；两条"断言有没有牙"的证伪探针（`test_review_a_schema_check_has_teeth`、`test_review_a_payload_whitelist_has_teeth`）确为纯逻辑（R3.3），无依赖时**真跑**：`3 passed, 12 skipped` ✓（与用户声称一致）。

### Q2 N1/N2/N3 的 CHG 是否算"已提交" —— **PASS（载体齐备），点名 1 处 MAIN 侧残留**

- `MAIN/docs/contracts/changes/` 下确有 `CHG-0001..0004`（3685/3077/4098/3440 B，mtime 2026-09-16 15:37），索引 `README.md` 已把四份登记为 **"提议（待 MAIN 判定）"** 并注明"未改 `MAIN/contracts/**`、未改 `contracts.lock`、未自行改实现对齐"。
- 自证未越权（mtime 实测）：`MAIN/contracts/**` 最新 mtime = 2026-09-15 22:09（早于本轮）；`B2/contracts.lock` mtime = 2026-09-15 22:09；`B2/module.yaml` = 2026-09-16 11:28（本轮之前）。**契约与锁均未被改动。**
- CHG-0001 内容逐项核对：含 级别/提出人+日期/状态/动机/影响的契约文件（四行，含"若判 v4 / 若判 v5"两分支）/影响的模块（B2/B3/B1/B4）/是否破坏兼容/示例载荷 diff/回滚方式 —— 与 `change-protocol.md` 第 1 步要求的字段表**逐项齐备**。
- 级别判定可接受：P2（N1）/P4（#3，只改 `MAIN/docs` 措辞）/P1（N2）/P2（N3）；协议 `:18`"判定有疑问时按更高级别处理"，四份均未低报。
- **点名（MAIN 侧，低）**：`MAIN/docs/contracts/change-protocol.md:146-148` 的"变更记录当前目录"表**仍写 `（暂无）`**，而实际已有 4 份提议 → 该表已过期且有误导性；需 MAIN（或提案方）同步。
- 至于"是否还需 MAIN 侧签收"：按协议第 2/3 步，影响分析与改契约**本就属 MAIN**，B2 无可做；状态写"提议，待 MAIN 判定"是正确的，不构成缺口。

### Q3 N2 的 B2 侧加固是否成立、会不会误拒合法 payload —— **成立（不误拒当前生产 payload），但有一处口径裂缝，必须堵**

- 成立部分（R1.1–R1.8）：`REQUIRED_KEYS` 覆盖 5 个 subject 且**等于准许键全集**（R1.2/R1.3）；逐键各缺一次都被拒、原因**点名该键**（R1.4 全 subject 通过）；原因**不回显取值**（R1.6/R1.7）；未知键仍拒（R1.5）。
- **不误拒**（R1.8）：6 种生产形状全过门 —— `submission.accepted(6)`、`evaluation.started(7)`、`progress 带计数(5)`、`completed(5)`、`failed(6)`；`submission.accepted` 在本仓库**只有一条写入路径**（`intake/service.py:316` 用 6 键构造器），故不存在漏键写入端。
- **口径裂缝（R1.9–R1.15，与 #1 同型）**：写入端 `orchestrator.py:1059-1062` **仍条件省略** `cases_total`/`cases_done`，而 `_enter_stage` 的签名允许 `cases_total=None, cases_done=None` 且 `emit_event: bool = True`（`:998-1000`）。我用**真实生产方法**造出该形状：
  ```
  EvaluationOrchestrator._enqueue_progress(... frame=ProgressFrame(cases_total=None, cases_done=None))
  → 落库 payload keys = ['evaluation_run_id','sequence','stage']
  → envelope_bytes → PayloadRejected: autograder.v1.evaluation.progress: missing key cases_done; missing key cases_total
  → dispatch = DispatchReport(selected=1, published=0, failed=1, dead_lettered=0)
     行：attempts=1, published_at=None
  ```
  当前**不可达**（`evaluation/ports.py:143` 的 `def cases_total(self) -> int` 保证整数，现有 6 个入队点都传 `view.cases_total`），所以现在不会误拒；但**任何新增一个不带计数的阶段调用**（签名默认值就允许）都会静默产出"永不投递、重试到上限进 DLQ"的事件——正是 #1 被修掉的那类缺陷换个位置复发。
- **必改（三选一，一行级）**：(a) 写入端恒带计数（把 1059-1062 的条件去掉）；或 (b) `evaluation.progress` 的 `REQUIRED_KEYS` 收窄为恒在的 3 键（`evaluation_run_id`/`stage`/`sequence`），并用另一条结构规则覆盖计数；或 (c) `_enter_stage` 在 `emit_event=True` 时显式断言计数非 None（fail-fast 而不是静默进 DLQ）。
- **覆盖盲区**：两处探针都测不到这条缝 —— `test_events_probe_review.py:201-207` 的 `canonical(EVALUATION_PROGRESS)` **硬编码**全 5 键，`test_review_a_payload_whitelist_has_teeth` 又断言 `required_keys == canonical`，而生产写入端的**条件省略**只在源码里。建议补一条"从写入端源码派生 shape"的断言（我在第一轮 S2 用 AST 做过：`_enqueue_progress` 的局部字典 = 3 键 + 2 个条件键）。

### Q4 探针两处加严 + `REQUIRED_KEYS` 探针改写 —— **更严，未见放宽**

- `r1.published >= 1` → **`r1.published == 1` 且 `r2.published == 0`**；流内匹配补 **`payload.failure_category == "internal_error"`**（静态确认 R3.4/R3.5；该字段在真实流上确实为 `internal_error`，我第一轮 S3.7 已独立复现，故加严不会误报）。
- `test_review_a_payload_whitelist_has_teeth` 由"记录缺键被放行"改为**钉住修复**：逐 subject × 逐键缺失必须被点名拒绝、`dropped_but_accepted == []`、`required_keys == canonical`（R3.6 + 全文阅读）⇒ 断言方向为**收紧**。全文未见"删断言 / 改成恒真"的新痕迹。
- **唯一保留**：这轮改写的**覆盖范围**仍有上述盲区（不是放宽，而是测不到 counts-less 形状）。

### Q5 新增依赖故障日志是否满足 §6/§6.4、是否引入敏感信息 —— **满足，但有一处误分类必须修**

- **dispatcher（R2a.1–R2a.5，真实不可达 broker，URL 带凭据）**：恰好 **1 条** 日志
  `{"level":"ERROR","event":"messaging.broker_unavailable","producer":"B2","logger":"autograder_b2.events.outbox","dependency":"message_broker","status":"unavailable","error_type":"EventBusUnavailable","stage":"dispatch","reason":"EventBusUnavailable: connect failed on nats://<redacted-host> NoServersError: ...","event_id":"<uuid>","attempts":1}`
  → 字段齐全且均在日志白名单内；**无** `sekretpw` / `127.0.0.1` / 令牌 / `Traceback` / 盘符；**未**把 `str(error)` 抄进日志（只有 `error_type` + 已脱敏 `reason`）；控制流未变（`failed=1`、行保留未发布）。
- **consumer（R2c.1–R2c.4、R2d.1）**：无钩子 → 恰好 1 条 `events.inbox.handler_failed`（`ERROR`、`outcome=failed`、reason 已脱敏为 `<redacted-path>`/`<redacted-credential>`、无 envelope 内容）；**有钩子 → 0 条日志且钩子恰好 1 次**（无重复记录）；`process()` 仍 `nak` 且原样重抛（未吞、未 ack）；未识别类型的既有日志回归正常（R2e.1）。
- **误分类（必改，低-中）**：**门/schema 拒绝也走 `messaging.broker_unavailable`**（R2b.1/R2b.2）：
  `{"event":"messaging.broker_unavailable","dependency":"message_broker","status":"unavailable","error_type":"PayloadRejected","reason":"PayloadRejected: ... key failure_category: expected one of: ..."}`
  一个内部 payload 缺陷被报成"消息中间件不可用"⇒ 按 broker 告警的运维会被误触发、且真正的 broker 故障被噪声稀释。改法：按异常类型分派事件名（依赖类 → `messaging.broker_unavailable`；`PayloadRejected`/`EventEnvelopeInvalid` → 例 `events.outbox.payload_rejected`），或至少在 `dependency`/`status` 上区分。
- 另（低，非必改）：失败日志**无节流/去重**，每行每轮各一条；1000 行 × 5 次上限 ⇒ 最多 5000 条 ERROR。DLQ 场景下这是可接受的，但值得在 runbook 里写明。

## 2. 本轮新发现（按严重度）

**N11（中；必改）`tests/test_events_outbox.py:731` 的 T-EVT-020 只标 `@db`，实际需要 broker ⇒ "有 DB 无 broker"下 FAIL 而非 skip**
证据：AST 提取 `732 test_consumer_failure_is_logged_even_without_an_error_hook db`；降级 B 实测
`FAILED ... EventBusUnavailable: connect failed on nats://127.0.0.1:1`，`1 failed, 11 passed, 11 skipped`；
测试体 `:750-754` 用 `connected_bus()` + `ephemeral_stream(bus)` + `consumer.run(bus, ...)`（**真实 broker**）。
讽刺的是 `docs/testing/cases.md:151` 给 T-EVT-020 的"前置"列写的是"真实 PostgreSQL + JetStream"，与标注**自相矛盾**。
这正是第一轮我点名、你已在 `test_events_probe_review.py` 修好的**同一类错标在新用例上复发**，并使 `phase-01:71` 的"通过"声明变成假。
改法：`tests/test_events_outbox.py:731` 补一行 `@broker`（放在 `@db` 上方）。改完后各配置应为：全依赖 `38 passed`；有 DB 无 broker `11 passed, 12 skipped`；无 DB 无 broker `8 passed, 15 skipped`（outbox）+ `3 passed, 12 skipped`（探针）。

**N12（低）`tests/linux/README.md` 不存在，却被 4 处引用**
`docs/README.md:40`、`docs/testing/cases.md:154`、`docs/phases/active/phase-01-implementation.md:74` 与 `:88`。
实测 `tests/linux/` 下只有 `test_nsjail_acceptance.py` 与 `__init__.py`；该文件里确有 xfail 项
`"5a2 cgroup memory.max 杀进程（环境限制）"`（`:756-800`、`_LIMITATION_ITEMS :1108`），且文件共 14 个 `def test_`（13 + 1 xfail）⇒ **"矩阵 13/13、5a2 xfail"这个说法本身自洽**，坏掉的只是指向。改法：把四处链接改为 `tests/linux/test_nsjail_acceptance.py`，或补一份真 README。

**N13（低）`MAIN/docs/contracts/change-protocol.md:148` 的 CHG 表仍写"（暂无）"**，与已提交的 4 份提案不符（见 Q2）。

**N14（低）用户报告的两处数字与实测不符**（不影响代码）：两模块全依赖实测 **38 passed**（报 37）；降级 B 实测 **1 failed, 11 passed, 11 skipped**（报 11 passed/11 skipped）。

## 3. 复算备注

- 我本轮的探针曾两次因**自身缺陷**误报：① `REQUIRED_KEYS` 覆盖检查误用字面串 `"missing key"`（该测试用 `dropped in reason`）；② R2 段被 R1 段故意留下的一条不可投递行污染（已改为段间 `TRUNCATE` 隔离）。两者均已修正并重跑，最终 **36/36 PASS** —— 上面每条结论都基于修正后的输出。
- 未跑：Linux/nsjail 验收（Windows 主机不可验，且属其他工作项）；未启动或停止任何容器。
