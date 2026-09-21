# 独立复核记录：L1 大类「提交接收与存储」（WP-B2-A，RQ-B-2-10/20/30）

复核者：独立复核 Agent（非实现者）。日期：本次会话。
环境：Windows 10 + PowerShell，Python 3.12.3，真实 PostgreSQL 17（Docker `autograder-b2-postgres`，127.0.0.1:55433），
测试临时目录 `B2/.pytest-tmp/`。
所有命令前置：`$env:B2_TEST_DATABASE_URL="postgresql+psycopg://autograder_platform:change-me-local-only@127.0.0.1:55433/autograder"`

契约权威未被偷改（自证）：`contract_digest()` == `contracts.lock.digest` ==
`de8f683496da78438a5cd1e6a12fc2aa30b871c63573ff49e5e0abe5cc4b617`（`contract_digest_matches_lock()` → True）。
迁移三者一致：`alembic heads` → `20260915_0008 (head)`；`module.yaml:13 migration_head: "20260915_0008"`；
`db/schema.py:19 EXPECTED_SCHEMA_REVISION = "20260915_0008"`。

### 被复核的代码修订（重要：并行工作项在本复核期间仍在改文件）

本仓库同时有其他工作项在写文件。复核结束时（2026-09-16 11:59）实测：
`intake/service.py`（11:28:55）、`db/schema.py`（11:28:25）、`events/payload.py`（11:45:46）、
`integration/adapters.py`（11:24:20）在本会话期间被改动过（行号已按**当前**内容校正）；
`storage/paths.py`（09-15 23:54:12）、`storage/cas.py`（09-16 00:02:52）、`domain/envelope.py`（09-15 23:40:25）、
`intake/api.py`（00:34:13）、`intake/query.py`（00:33:56）、`auth/authorization.py`（00:33:51）、
`auth/tokens.py`（00:34:04）在整个复核期间**未被改动**。
所有决定性命令（标准 1 的逐类运行、289 条参考运行、52 条探针）都在**最后一次相关文件改动之后**重跑，
因此结论对应的是下面这个修订（2026-09-16 12:02 实测）：

```
paths.py       F9364A0F5A7D734A2603631E6DFB1D23025A9B2C6DBF1894F2418817C1029E1C   (09-15 23:54，复核期间未动)
cas.py         A0F1F5A331C1B98F25C5E865A544B4DC7FD6D7D79575D588EF6B8FBC5DADB57F   (09-16 00:02，未动)
envelope.py    28EAF8FBCFDB077B8ECDBA11E85EBD2358F63EC7FC0CBC57E1CE95DF08AB9CD3   (09-15 23:40，未动)
authorization.py 1F08964B47757AFC3629A373306D35C2AE161BE6F98D9E462848A01E84E33D52  (09-16 00:33，未动)
service.py     6FB1D63EE9DC3F57ABE8976AB41CBEB5CDF4B992642E8AE4E458D9BAAF258B23   (11:59:19，内容与 11:45 版一致)
payload.py     8C3F4B92A70BCB2B604448053AB2331B3E1EEBD4C0BC155AA6D97D5A1284DCEC   (11:45:46)
```

### 复核期间的并发改写与一次**瞬时红灯**（重要，须在验收前重跑）

11:59:59 / 12:00:45 前后，并行工作项在改 `intake/attachments.py` 与 `tests/test_email_intake.py`，
期间我实测到一条**瞬时红灯**：

```
uv run pytest tests/test_intake_api.py tests/test_submission_query.py tests/test_email_intake.py \
  tests/test_email_intake_acceptance.py tests/test_storage_cas.py -q -rs
→ 2 failed, 215 passed in 20.98s
   FAILED tests/test_email_intake.py::test_container_signature_is_detected_at_a_non_zero_offset[arj]
   FAILED tests/test_email_intake.py::test_container_signature_is_detected_at_a_non_zero_offset[bzip2]
   AssertionError: arj 未识别: ContentVerdict(kind='binary', label='NUL byte at byte offset 26', …)
```

`test_email_intake.py` 在 12:01:33 再次被改动后该红灯自行消失（12:02 重跑 → `289 passed, 1 xfailed`）。
该用例属**实现者自加的容器嗅探分类硬化测试**，不在 `MAIN/docs/workstreams/B2.md` 第 3 节为
`RQ-B-2-10/20/30` 列出的验收项内（见下），因此不构成三条判据的不成立；但它说明本组文件在本复核期间
**仍在被并行改写**：**验收/封板前必须重跑本组测试**，不要用我这里的数字代替最后那一刻的运行。

### RQ-B-2-10/20/30 的**权威**验收项（`MAIN/docs/workstreams/B2.md` 第 3 节原文）

| RQ | 权威验收项（原文摘要） | 我的验证 |
|---|---|---|
| RQ-B-2-10 | Schema 校验、摘要核对、幂等、作用域 403、大小上限、**事件不含源码** | 全部实测通过（见标准 1/3 各节；行注入尝试全 422） |
| RQ-B-2-20 | 唯一匹配失败则隔离不评分；检查点幂等；重复投递不重复提交 | 全部实测通过（含 `evaluation_runs==0`、`second.fetched==0`、跨 UIDVALIDITY 的 Message-ID 去重） |
| RQ-B-2-30 | 按 SHA-256 去重；服务端生成路径；读权限；**孤儿内容可回收** | 去重/路径/读权限实测通过；孤儿回收由 `tests/test_storage_gc.py`（12 条，含 `metadata_write_failure_leaves_recoverable_orphan_content`）在当前修订全绿 |

（WP-B2-A 的验收要求是"三条 RQ 的**全部**验收项"。用户判据额外点名的"路径穿越"不在上表原文中，
但我也单独验证并通过 —— 属于超集，不影响判定。）

---

## 标准 1 — 判定：**PASS**

> 原文：RQ-B-2-10/20/30 的验收场景（含路径穿越、摘要不符、超限、幂等重放、跨学生越权读 403、
> 邮件唯一匹配失败隔离、checkpoint 幂等）全部由真实测试覆盖并通过。

### 我跑的命令与真实输出

**(a) 独立复现参考运行（8 个文件，含真实库）**

```
uv run pytest tests/test_migrations.py tests/test_storage_cas.py tests/test_storage_gc.py \
  tests/test_intake_api.py tests/test_submission_query.py tests/test_email_intake.py \
  tests/test_email_intake_acceptance.py tests/test_submission_loader.py -q -p no:warnings
→ 261 passed, 1 xfailed in 53.01s          # 与实现者自报 261 passed, 1 xfailed 一致
```

**(b) 加上事件侧文件 + `-rs`（确认没有任何 skip 被当成通过）**

```
# 11:45 版修订：
... 同样 8 个文件 + tests/test_events_outbox.py → 282 passed, 1 xfailed in 54.40s   # 无 SKIPPED 行
# 12:02 版修订（最终，附件嗅探改动落地后）：
uv run pytest tests/test_migrations.py tests/test_storage_cas.py tests/test_storage_gc.py \
  tests/test_intake_api.py tests/test_submission_query.py tests/test_email_intake.py \
  tests/test_email_intake_acceptance.py tests/test_submission_loader.py tests/test_events_outbox.py \
  -q -p no:warnings --tb=no -rf
→ 289 passed, 1 xfailed in 54.37s        # 0 failed、0 skipped
```

**(c) 不设 DB URL 时的对照（证明 skip 不等于通过）**

```
uv run pytest tests/test_intake_api.py tests/test_storage_cas.py -q -rs
→ 72 skipped in 0.48s      # 全部 SKIPPED：未设 URL 时这些用例一条都没跑
```

**(d) 逐类定向执行（`-v -rs`，逐条给出节点名，零 skip）**

类 1–3（路径穿越 / 摘要不符 / 超限）：

```
uv run pytest -v -rs \
 "tests/test_intake_api.py::test_illegal_path_is_422_and_writes_nothing" \
 "tests/test_intake_api.py::test_path_traversal_specifically_reports_path_not_allowed" \
 "tests/test_storage_cas.py::test_storage_rejects_illegal_client_paths" \
 "tests/test_email_intake.py::test_attachment_name_traversal_is_rejected" \
 "tests/test_intake_api.py::test_digest_mismatch_is_422_and_writes_nothing" \
 "tests/test_intake_api.py::test_too_many_files_is_422" \
 "tests/test_intake_api.py::test_oversized_single_file_is_413" \
 "tests/test_intake_api.py::test_oversized_total_is_413" \
 "tests/test_intake_api.py::test_declared_content_length_over_cap_is_413" \
 "tests/test_email_intake.py::test_oversized_attachment_is_rejected_before_decoding" \
 "tests/test_email_intake.py::test_zip_bomb_is_rejected_by_its_declared_size" \
 "tests/test_email_intake.py::test_gzip_bomb_and_plain_gzip_are_both_rejected"
→ 28 passed in 8.35s        （含 6 个路径穿越参数 + 12 个非法路径参数，全部 PASSED，0 SKIPPED）
```

类 4–7（幂等重放 / 跨学生越权读 403 / 邮件唯一匹配失败隔离 / checkpoint 幂等）：

```
uv run pytest -v -rs \
 "tests/test_intake_api.py::test_repeated_idempotency_key_returns_same_submission_without_second_write" \
 "tests/test_intake_api.py::test_idempotent_replay_does_not_enqueue_second_event" \
 "tests/test_intake_api.py::test_different_idempotency_keys_create_two_submissions" \
 "tests/test_intake_api.py::test_reused_submission_id_with_different_key_is_rejected" \
 "tests/test_submission_query.py::test_student_reading_another_students_submission_is_403_not_404" \
 "tests/test_submission_query.py::test_student_in_another_course_is_403" \
 "tests/test_submission_query.py::test_teacher_from_another_course_is_403" \
 "tests/test_submission_query.py::test_owner_can_read_own_submission" \
 "tests/test_email_intake.py::test_message_that_matches_nothing_is_quarantined_and_queryable" \
 "tests/test_email_intake.py::test_ambiguous_message_is_quarantined_without_guessing" \
 "tests/test_email_intake_acceptance.py::test_unmatched_sender_is_quarantined_queryable_and_never_evaluated" \
 "tests/test_email_intake_acceptance.py::test_missing_message_id_is_quarantined_never_a_submission" \
 "tests/test_email_intake.py::test_same_uid_and_uidvalidity_are_pulled_once" \
 "tests/test_email_intake.py::test_same_message_id_under_a_changed_uidvalidity_is_skipped" \
 "tests/test_email_intake.py::test_concurrent_duplicate_is_skipped_instead_of_crashing" \
 "tests/test_email_intake.py::test_conflicting_checkpoint_rolls_back_the_whole_transaction" \
 "tests/test_email_intake.py::test_email_checkpoint_is_written_in_the_submission_transaction" \
 "tests/test_email_intake_acceptance.py::test_repeated_pull_of_the_same_uid_does_not_create_a_second_submission" \
 "tests/test_email_intake_acceptance.py::test_same_message_id_under_a_new_uid_is_also_not_a_second_submission"
→ 19 passed in 8.18s        （全部 PASSED，0 SKIPPED）
```

### 逐类"真的在测那件事"核查（不是恒真断言、没被 skip 掩盖、没用替身）

| 类 | 真实测试（节选） | 断言是否打到要害 |
|---|---|---|
| 路径穿越 | `test_illegal_path_is_422_and_writes_nothing`（6 参数）、`test_path_traversal_specifically_reports_path_not_allowed`、`test_storage_rejects_illegal_client_paths`（12 参数）、`test_attachment_name_traversal_is_rejected` | 断言 422 **且**理由码（`path-not-allowed`）+ `_counts(engine)==(0,0,0,0)`（submissions/artifacts/files/outbox）——不是"只要不是 202 就算" |
| 摘要不符 | `test_digest_mismatch_is_422_and_writes_nothing` | 伪造 `content_sha256` → 422 + `digest-mismatch` + 四表零行 |
| 超限 | `test_too_many_files_is_422`(422+`too-many-files`)、`test_oversized_single_file_is_413`(413)、`test_oversized_total_is_413`(413+`total-too-large`)、`test_declared_content_length_over_cap_is_413`（用极小上限使早退路径可实测）、`test_oversized_attachment_is_rejected_before_decoding`（monkeypatch 断言解码函数**一次都没被调用**） | 状态码 + 理由码 + 零落库；邮件侧还断言"解码器未被调用"（非恒真） |
| 幂等重放 | `test_repeated_idempotency_key_returns_same_submission_without_second_write`（同 submission_id、replay 标志、`accepted_at` 相同、计数 `(1,1,1,1)`）、`test_idempotent_replay_does_not_enqueue_second_event`、`test_different_idempotency_keys_create_two_submissions`（`(2,1,2,2)`）、`test_reused_submission_id_with_different_key_is_rejected` | 断言"第二次没有第二次写入"的证据是四表计数，而不是只看响应体 |
| 跨学生越权读 403 | `test_student_reading_another_students_submission_is_403_not_404` | `==403` **且显式** `!=404 and !=200` **且** `type` 以 `forbidden` 结尾；另有 `test_owner_can_read_own_submission`/`unknown_submission_is_404` 作对照 |
| 邮件唯一匹配失败隔离 | `test_message_that_matches_nothing_is_quarantined_and_queryable`（隔离行可查询、`reason_code=not_uniquely_matched`、零提交/零 artifacts/零事件、明细不含发件人地址、再拉一次不产生第二条）、`test_ambiguous_message_is_quarantined_without_guessing`（两条规则指向不同身份 ⇒ `ambiguous_match`；指向同一身份不算歧义）、验收文件另有一份独立装配的 `test_unmatched_sender_...never_evaluated`（断言 `evaluation_runs==0`） | 断言隔离**记录内容**与"零评测"，不是只看返回状态字符串 |
| checkpoint 幂等 | `test_same_uid_and_uidvalidity_are_pulled_once`（第二次 `fetched==0`、`skipped==1`、`connections==2`——显式排除"其实没连第二次"的恒真）、`test_same_message_id_under_a_changed_uidvalidity_is_skipped`（换 UIDVALIDITY/UID/内容，仅 Message-ID 相同）、`test_concurrent_duplicate_...`（真实唯一约束 + 真 `IntegrityError` 分支）、`test_email_checkpoint_is_written_in_the_submission_transaction`、`test_non_checkpoint_integrity_errors_are_not_swallowed`（真 CHECK 违反必须响亮失败） | 断言 checkpoint 行内容、提交计数、以及"其它完整性错误不得被吞"的反向保护 |

### 我自己额外做的（不复用实现者断言的）独立探针

- `tests/test_group_acceptance_probe.py::test_probe_cross_student_read_is_403_and_leaks_nothing` —— **PASS**：
  越权读 403 且响应体不含源码/base64；本人读 200；不存在的 id 仍 404；跨课程读自己的提交 403。
- `...::test_probe_idempotent_replay_writes_nothing_new_on_disk` —— **PASS**：重放（且第二次带**不同内容**）
  仍返回 202 + `idempotent_replay=true` + 同 submission_id，四表与 CAS 树零新增。

### 结论

7 类场景**全部**有真实测试、在真实 PostgreSQL 上**真的执行**（0 skip）、**真的通过**，且断言打到要害。
未发现任何一类只有"看起来像"的测试。在**最终修订**（12:02）上重跑这 47 条：`47 passed in 14.78s`。
第 1 条**成立**。

补注（不影响判定，但父代理必须知道）：本组文件在复核期间仍在被并行改写，并在 11:59:59–12:01:33
出现过一次**瞬时红灯**（2 条容器嗅探分类用例，均不在 RQ-B-2-10/20/30 的权威验收项内），
12:01:33 修复后消失。封板前请重跑，勿以本记录的数字代替最后那一刻的运行。

---

## 标准 2 — 判定：**PASS**

> 原文：相同文件内容按 SHA-256 只存一份，存储路径由服务端派生，客户端指定路径
> （含 `..`/绝对路径/反斜杠）不可达。

### 我自己构造的证伪尝试及结果

**(a) 契约是否放行 `..`（确认防线归属）** —— `.pytest-tmp/probe_contract.py` 实测：

```
'../a.py'            contract_ok=True   whitelist_ok=False
'a/../b.py'          contract_ok=True   whitelist_ok=False
'..'                 contract_ok=True   whitelist_ok=False
'.'                  contract_ok=True   whitelist_ok=False
'../../etc/passwd'   contract_ok=True   whitelist_ok=False
'main.py\n'          contract_ok=True   whitelist_ok=False     ← Python re 的 `$` 容忍尾随换行
'..\\a.py' / '/etc/passwd' / 'a//b.py' / 'C:\\x.py'  contract_ok=False
```

由 `tests/test_group_acceptance_probe.py::test_probe_whitelist_is_the_effective_defense_not_the_contract`
锁定（**PASS**）：契约放行的 `['../main.py','a/../b.py','..','.','../../etc/passwd','main.py\n']`
被 `domain/envelope.is_allowed_relative_path` 全部拒绝。→ 路径穿越的防线确实是 B2 自己的白名单。

**(b) 31 种敌意客户端路径走真实 HTTP 端点（端到端 + 磁盘）**

```
uv run pytest tests/test_group_acceptance_probe.py -k hostile -q -s → 31 passed
```
每条断言：状态 422；`b2.submissions`/`artifacts`/`submission_files`/`outbox_events` 四表零新增；
**CAS 根目录下零新增文件**；**CAS 根之外（父目录）零新增条目**（实现者测试只查表，不查磁盘）。
覆盖：`../`、多级 `../../`、`a/../b`、`a/b/../../../..`、`..`、`.`、`a/..`、`/etc/passwd`、`/`、`//etc/passwd`、
`..\`、`a\b.py`、`..\..\windows\system32\cmd.exe`、`C:\Windows\win.ini`、`\\server\share\x.py`、`a//b`、
`a/./b`、`....//main.py`、`%2e%2e/main.py`、`..%2fmain.py`、`main.py\n`、`main.py\r\n`、`main.py\x00`、
`a\x00b.py`、全角 `．．/`、9 段路径、超长 300 字符、空串 —— **全部 422 且零痕迹**。

**(c) DB 层第二道防线真的存在**（`test_probe_database_check_is_a_second_line_for_paths` —— **PASS**）：
直接 SQL 插 `submission_files.path='../escape.py'` → `IntegrityError`；
插 `artifacts.storage_path='../../etc/passwd'` → `IntegrityError`（`20260915_0002` 的 `_PATH_CHECK` /
`ck_artifacts_storage_path`）。

**(d) 去重与路径派生（真实库 + 真实 CAS 根）**

- 一次提交内两份**相同字节**、不同路径：`submissions +1 / artifacts +1 / submission_files +2`，
  该摘要在整棵 CAS 树里**恰好一个**文件且路径 == `ab/cd/<digest>`，磁盘字节与源一致（PASS）。
- 跨学生两次提交同内容：两条提交、两行 files、**一行 artifacts**、一个文件；
  `artifacts.storage_path == ab/cd/<digest>`；**`<root>/main.py`、`<root>/pkg` 不存在**（客户端路径在 CAS 里没有落点）（PASS）。
- 反向对照：两份**不同**内容 → 两行 artifacts、两个文件（去重没有退化成"只存第一份"）（PASS）。
- API 返回的提交视图不含 `storage_ref`/`storage_path`/`content_base64`（PASS）。

**(e) 并发写入同一内容（我自己加的证伪尝试）**

```
[probe] 并发 12 路同一内容: 成功=7 唯一 artifact_id=1 artifacts行=1 文件存在=True 异常类={'PermissionError': 5}
[probe] 并发 12 路同一内容: 成功=2 唯一 artifact_id=1 artifacts行=1 文件存在=True 异常类={'InvalidDigestError': 8, 'PermissionError': 2}
```
**"只存一份"在并发下依然成立**（恰好 1 行 artifacts、1 个文件、成功者拿到同一个 `artifact_id`），
但并发路径上 `ContentAddressedStore.put` 会抛异常 —— 见不一致清单 #1（标准满足，实现有问题）。

### 结论

三条子句我都用**真实 PostgreSQL + 真实 CAS 目录**正面验证过，并试图用 31 种敌意路径 + 契约对比 +
直接 SQL 破坏 + 并发写入去证伪，未找到破坏"一份内容 / 服务端派生路径 / 客户端路径不可达"的路径。
第 2 条**成立**（并发异常是另一个缺陷，见清单 #1，不改变判据本身的成立）。

---

## 标准 3 — 判定：**PASS**

> 原文：非法请求不落库（`b2.submission`/`b2.artifact` 无新行），`submission.accepted` 事件 payload
> 不含源码。

（命名对照：判据里的 `b2.submission`/`b2.artifact` 对应实际表 `b2.submissions` / `b2.artifacts`；
下面同时统计 `submission_files` 与 `outbox_events`。）

### 我自己跑的命令与真实输出

```
uv run pytest tests/test_group_acceptance_probe.py -q -s --tb=line -rf
→ 51 passed, 1 failed     # 唯一失败 = 我故意写下的并发缺陷探针（见清单 #1），不是这三条判据
```

### 非法请求零落库（含磁盘维度）

每条断言四表零新增 + CAS 根零新增文件 + CAS 根之外零新增条目，并且**配一条正向对照**
（合法提交会让四个计数各 +1、CAS 树 +1 文件），排除"计数恒为 0"这类恒真断言：

| 非法请求 | 实测 | 结论 |
|---|---|---|
| 31 种敌意路径（标准 2 (b)） | 全部 422，零痕迹 | 成立 |
| 摘要不符 | 422 `digest-mismatch`，零痕迹 | 成立 |
| 单文件超限 1572865B / 总量超限 4.2MB | 413 / 413 `total-too-large`，零痕迹 | 成立 |
| 101 个文件 / 非法 base64 | 422 `too-many-files` / 422，零痕迹 | 成立 |
| 未知字段（`source_code=<marker>`） | 422，零痕迹 | 成立 |
| 跨课程作用域 / 冒名提交 / 未认证 | 403 / 403 / 401，零痕迹 | 成立 |
| 同一 submission_id 配不同幂等键（且带**新内容**） | 422，**artifacts 与 CAS 零新增**（检查顺序在 `stage_contents` 之前） | 成立 |
| 幂等重放（第二次带不同内容） | 202 replay，四表与 CAS 零新增 | 成立 |
| 邮件侧：可执行附件 / 不唯一匹配 / 缺 Message-ID | `rejected` / `quarantined` / `rejected`；submissions、artifacts、outbox 均零新增，**CAS 文件零新增** | 成立 |

（`test_probe_email_rejections_leave_no_submission_artifact_or_file` 的实测打印：
`email executable attachment: status=rejected reason=executable_attachment`、
`email unmatched sender: status=quarantined reason=not_uniquely_matched`、
`email missing message-id: status=rejected reason=missing_message_id`。）

**我为了找"非法请求也会落 artifacts"专门找过两个候选口子，都没打通**：
1. 契约 `idempotency_key.maxLength=200` 与 DB `ck_submissions_idempotency_key (1..200)` 完全一致
   → 构造不出"过了契约却违反 CHECK"的超长键（我原本怀疑这里能让 `stage_contents` 先落 artifacts 再 500）。
2. `format: uuid` **确实被强制执行**（`contracts._validator` 带 `FormatChecker`；直接实测
   `student_id='print(1)'` → `"student_id: 'print(1)' is not a 'uuid'"`），且 5 个标识字段分别塞源码
   走真实端点全部 422 且零落库 → 构造不出"非 UUID 触发 ValueError、但内容已落盘"的口子。

### `submission.accepted` payload 不含源码

`test_probe_submission_accepted_payload_has_no_source_anywhere_in_the_row`（**PASS**）在真实提交后：

- payload 键集合**恰好** `{submission_id, student_id, course_id, section_id, question_version_id, intake}`，
  5 个标识都能被 `uuid.UUID()` 解析，`intake == "api"`；
- 把 **outbox 整行所有列**序列化后逐个搜索：源码标记 `PROBE_MARKER_a1b2c3`、`print(`、
  源码的 base64、源码的 SHA-256、`content_base64`、`storage_path`、`main.py` —— **全部不出现**。

结构上也没有注入面：payload 的 6 个值全部来自「契约强制 uuid 的标识」+「服务端固定字面量 `intake`」，
payload 白名单在 `events/payload.py` 只允许 UUID/摘要/计数/分数/封闭枚举，**没有自由文本键**。

### 一个我核查过的次序事实（可达性结论：不构成判据违反）

`test_probe_tampered_envelope_is_reachable_only_from_library_callers`（**PASS**）实测：

```
[probe] tampered envelope: reason=digest_mismatch artifacts 0->1 files 5->6
```
`persist` 先 `stage_contents`（内容落 CAS + `artifacts` 行，独立事务），再因
`envelope_files != envelope.files` 抛 `EnvelopeRejected("digest_mismatch")`，**已写内容不回滚**。
但两个入口（`accept` 与邮件 `process_raw`）都在同一次调用里用**同一份字节**构造信封，
结构上不可能产生不一致信封；该分支只对"绕过入口手搓 `SubmissionEnvelope` 再调 `persist`"的库内调用者可达。
因此它不构成"非法**请求**落库"。已列入清单 #4。

### 结论

非法请求（两个入口、十余类、含磁盘与根外维度）**不落库**；`submission.accepted` payload **不含源码**
（键集合锁定 + 整行扫描 + 结构性无自由文本键 + 标识字段注入尝试全被 422）。第 3 条**成立**。

---

## 不一致清单

### #1 【标准满足但实现有问题 / 严重】并发写入同一内容时 `ContentAddressedStore.put` 抛异常，其中一个是**假阳性**存储守卫

- **现象**：并发写同一份内容时，`put()` 抛两类异常：
  1. `InvalidDigestError: derived storage path escaped the artifact root`（**假阳性**：派生路径
     `ab/cd/<digest>` 结构上不可能越出根目录）；
  2. `PermissionError [WinError 5]` 来自 `cas._write_atomic` 的 `os.replace`（多线程同时替换同一目标）。
- **最小复现**（真实库 + 真实 CAS 根）：

```
uv run pytest tests/test_group_acceptance_probe.py::test_probe_defect_concurrent_identical_put_is_not_concurrency_safe -q -s
→ AssertionError: 并发 put 抛异常（存储层缺陷）: {'InvalidDigestError: derived storage path escaped the artifact root': 5}
   8 线程并发一次，5~6 路抛异常；不同运行下 8 路中 4~10 路抛异常（两类混合）

uv run python .pytest-tmp/diag4.py     # 抓取失败当次的 frame locals
→ EXC: InvalidDigestError('derived storage path escaped the artifact root')
    resolved  : WindowsPath('D:/VScodeProjects/AutoGrader/B2/.pytest-tmp/probe-diag4/artifacts')
    candidate : WindowsPath('//?/D:/VScodeProjects/AutoGrader/B2/.pytest-tmp/probe-diag4/artifacts/34/cf/34cf36…e43c')
    rr._tail  : ['VScodeProjects','AutoGrader','B2','.pytest-tmp','probe-diag4','artifacts']
    c prefix  : ['VScodeProjects','AutoGrader','B2','.pytest-tmp','probe-diag4','artifacts']   ← 完全相同
    contains  : False
```
- **根因**：`candidate` 带 `\\?\` 扩展长度前缀而 `resolved_root` 不带，于是
  `paths.py:38` 的**字符串级**包含判定 `resolved_root not in candidate.parents` 为假。
  `ntpath.realpath` 只在 `_getfinalpathname(spath) == path` 或 `ex.winerror == initial_winerror` 时才剥前缀
  （`F:\Anaconda\Lib\ntpath.py` 中 `realpath` 的收尾：`if not had_prefix and path.startswith(prefix): … try:
  if _getfinalpathname(spath) == path: path = spath / except OSError as ex: if ex.winerror == initial_winerror: path = spath`）。
  并发 `mkdir` 让两次探测的 winerror 从 `ERROR_PATH_NOT_FOUND(3)` 变成 `ERROR_FILE_NOT_FOUND(2)`，
  前缀因此不被剥掉 → 包含判定命中假阴性 → 抛"越出根目录"。**只在竞态下出现**，故顺序测试全绿。
- **涉及文件/行**：`src/autograder_b2/storage/paths.py:31-39`（尤其 `:38` 的包含判定）、
  `src/autograder_b2/storage/cas.py:82`（调用点）、`src/autograder_b2/storage/cas.py:217-229`（`_write_atomic` / `os.replace`）。
- **影响**：`InvalidDigestError` 是 `ValueError` 子类，**不被** `intake/api.py` 的 `except SQLAlchemyError`
  捕获，会冒泡成 500；于是"两个学生同时提交相同内容"可能一边 500。去重不变量本身未被破坏
  （实测仍为 1 行 artifacts + 1 个文件）。
- **判断**：**标准满足但实现有问题**。标准 2 的三条子句字面上仍成立（内容仍只存一份、路径仍由服务端派生、
  客户端路径仍不可达），但这个守卫在自身注释宣称的场景（"第二重保险"）之外的场景里**误报**，
  且是部署环境（Windows 开发/测试机）上可复现的 500 来源。建议修法：守卫改为**结构化**判定
  （比较 `candidate.relative_to(resolved_root)` 是否成功、或先 `os.path.normcase(str(...))` 处理后比较、
  或直接判定 `storage_path_for(digest)` 的形态而不是比较 resolve 结果），并给 `_write_atomic` 的目标替换加
  "目标已存在则跳过"或重试。

### #2 【标准满足但实现有问题】幂等作用域用的是客户端**原始拼写**，同一（学生, 题目, 幂等键）能产生第二份提交

- **现象**：契约 `Uuid` 是 `format: uuid`，**大写拼写合法**（实测 `'AAAAAAAA-AAAA-4AAA-8AAA-AAAAAAAAAAAA' → OK`），
  而 `intake/service.py:448-450` 的 `_idempotency_scope` 直接拼接**未规范化**的
  `student_id`/`question_version_id`，唯一约束 `(idempotency_scope, idempotency_key)` 因此按拼写分桶。
- **最小复现**：

```
uv run pytest "tests/test_group_acceptance_probe.py::test_probe_finding_idempotency_scope_uses_the_raw_client_spelling" -q -s
→ [probe] spelling: first=202 second=202 submissions=2
   scopes=['d7dc2ac1-…-3a4add403a0a:0264c779-…-1315998c8020',
           'D7DC2AC1-…-3A4ADD403A0A:0264c779-…-1315998c8020']      ← 只差大小写
```
- **涉及文件/行**：`src/autograder_b2/intake/service.py:452-454`（`_idempotency_scope`，`:261` 处取用）、
  输入源自 `src/autograder_b2/intake/service.py:439,449`（`_read_envelope` 不规范化）；
  对比 `src/autograder_b2/auth/tokens.py:172-184`（令牌侧**显式规范化**，注释明确列出大写/花括号/urn/无连字符都是合法写法）。
- **判断**：**标准满足但实现有问题**。标准 1 要求的"幂等重放"场景确已被真实测试覆盖并通过；
  这里是一个真实存在但未被覆盖的幂等漏洞（客户端可用大写拼写绕过幂等键 → 重复提交 + 第二条
  `submission.accepted` 事件）。修法：派生 scope 前对 5 个标识统一 `str(uuid.UUID(x))` 规范化（与令牌侧同源）。

### #3 【标准满足但实现有问题】写入口的授权比较不做规范化，契约合法请求被误拒 403

- **现象**：学生用**自己 UUID 的大写拼写**提交（契约合法）→ 403。
- **最小复现**：

```
uv run pytest "tests/test_group_acceptance_probe.py::test_probe_finding_uuid_spelling_is_not_canonicalised_on_the_write_path" -q -s
→ [probe] 大写 student_id + 本人令牌实测: 403
   {"type":"…/problems/forbidden","title":"Forbidden","status":403,"detail":"The caller is not permitted to perform this operation."}
```
- **涉及文件/行**：`src/autograder_b2/auth/authorization.py:36`（`student_id != principal.user_id` 原始字符串比较）
  ← 输入来自 `src/autograder_b2/intake/service.py:439,449`（`_read_envelope` 只做 `str()`，不规范化）。
  同模块读路径已有 `test_uppercase_uuid_spellings_in_token_are_canonicalised` 覆盖，**写路径没有**。
- **判断**：**标准满足但实现有问题**（fail-closed 的误拒，不是安全漏洞；错误码语义上"合法请求被 403"与
  `identifiers.md` 的 403=无权 语义不符）。修法同 #2：入口处统一规范化。

### #4 【标准满足但实现有问题】`persist` 在"内容已落盘"之后才判信封一致性

- **现象/复现**：见标准 3 末尾，`tampered envelope: reason=digest_mismatch artifacts 0->1 files 5->6`。
- **涉及文件/行**：`src/autograder_b2/intake/service.py:268-274`（`stage_contents` 之后才比较）、
  `src/autograder_b2/storage/cas.py:78-114`（`put` 用独立事务）。- **判断**：**标准满足但实现有问题**（不可从任何请求路径到达；属"库内调用者 API 契约"层面的次序问题）。
  同一根因的**已记录取舍**是"先内容后元数据 ⇒ 回滚只留可回收孤儿"（`tests/test_email_intake.py:735`
  显式钉住 `count(artifacts) == 2`），该取舍本身有 GC 回收（`test_storage_gc.py` 全绿），不属缺陷。

### #5 【仅环境/跨组观察，且不属于本判据】白名单允许 Windows 设备名与尾随点号路径

实测 `is_allowed_relative_path` 对 `CON`/`NUL`/`AUX`/`COM1`/`LPT1`/`main.py.`/`...` 均返回 **True**
（契约模式同样放行：它们都是 `[A-Za-z0-9_.-]+`）。这些**不是穿越**（B2 自己的存储从不按客户端路径落盘，
`storage_path` 恒为 `ab/cd/<digest>`），因此**不违反**标准 2 的字面判据。但 `integration/adapters.py:233-242`
（判定在 `:234`，写入在 `:242`）
的物化器会把 `path` 拼到工作区目录再 `write_bytes`：在 Windows 上 `main.py` 与 `main.py.` 会落到同一个文件、
`CON`/`NUL` 指向设备。属评测/物化（非 WP-B2-A 存储层）范围，建议由该小类负责人复议。

### #6 【仅文档/注释问题】`storage/paths.py` 的注释把该守卫描述为可靠的"第二重保险"

`paths.py:34` 的 docstring 称"越出根目录也会当场抛错"，`cas.py:5-11` 称"路径不可注入"。实测该守卫在
并发下会**对根目录内的路径**抛错（清单 #1）。建议注释随修法一并更新，避免"注释宣称的性质"与实测不符。

### #7 【已由测试显式留痕，超出三条判据】`alembic downgrade base` 必然失败

`tests/test_migrations.py:317` 以 `strict=True` 的 xfail 记录基线迁移 `20260915_0001` 的
`DROP SCHEMA IF EXISTS b2`（无 CASCADE）与 `b2.alembic_version_b2` 冲突（`DependentObjectsStillExist`）。
与本 L1 三条判据无关，且 strict-xfail 不会被静默当作通过；仅登记。

### #8 【复核期间观察，非标准不符】本组文件在复核期间被并行改写，出现过一次瞬时红灯

详见上文"复核期间的并发改写与一次瞬时红灯"。11:59:59–12:01:33 期间
`tests/test_email_intake.py::test_container_signature_is_detected_at_a_non_zero_offset[arj|bzip2]`
实测失败（`kind='binary'` 而非 `archive`），涉及 `src/autograder_b2/intake/attachments.py`（11:59:59）
与 `tests/test_email_intake.py`（12:00:45 → 12:01:33）。12:02 重跑全绿。
**判断：非标准不符**（不在 RQ-B-2-10/20/30 权威验收项内，且已修复）；但它是"封板前必须重跑"的实证理由。
若该红灯在验收时刻仍存在，则它属于"本组测试不通过"，父代理应当据此阻断封板。

---

## 我明确**无法验证**的部分

1. **真实 IMAP/TLS 服务器**：邮件入口的端到端拉取只用到仓库内自写的进程内 TCP 夹具
   （`tests/imap_fixture.py::ImapFixtureServer`，`_Writer`/`_Session` 手写协议，无 TLS）。我没有对真实
   Dovecot/Exchange + `STARTTLS`/`SSL` 验证过；`use_ssl=True` 分支未执行。
2. **Linux / 容器内行为**：清单 #1 的并发缺陷只在 Windows 复现（`Path.resolve()` 与 `ntpath.realpath`
   的行为差异），我没有 Linux 环境去确认它在部署目标上是否也出现（预期不出现，但**未验证**，不能写成"安全"）。
3. **多进程 / 多副本并发**：我的并发探针只用了**同进程多线程 + 同一 Engine**；跨进程、跨容器副本对同一
   CAS 根的并发未验证。
4. **真实 NATS 派发**（outbox → broker → 消费侧）：本组判据不覆盖，我只验证了 outbox 行的写入与 payload 内容。
5. **Windows 上的物化行为**（清单 #5）：需要真实 Windows 沙箱/工作区写入路径才能定论，我只验证了白名单判定。
6. **B3 取包 / B1 GUI / B4 令牌签发**：不在本组范围，全部按契约与冻结接口假定，未端到端验证。
7. 契约 `content_base64` 的 `contentEncoding: base64` 在 jsonschema 里**是注解而非断言**——这不是缺陷
   （B2 自己用 `base64.b64decode(validate=True)` 解码并在失败时 422，我已实测），仅记录以免被误读为"契约在把关"。

---

## 总结

```
标准1=PASS 标准2=PASS 标准3=PASS
```

复核产物：`tests/test_group_acceptance_probe.py`（52 条探针：51 PASS + 1 条**故意**失败的并发缺陷探针）、
`.pytest-tmp/probe_contract.py`、`.pytest-tmp/probe_uuid2.py`、`.pytest-tmp/diag4.py`（缺陷根因最小复现）。
未修改任何 `src/**` 或既有测试文件。
