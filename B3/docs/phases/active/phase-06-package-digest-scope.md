# Phase 06 包摘要的作用域，以及一次被推翻的根因判断

- **状态**：**active**（未执行项见 §5）
- **范围**：修正 `package_digest` 的覆盖范围使其符合契约用词；恢复 103 个已发布版本的取包能力；
  补一条**真的能失败**的回归测试。本文件同时如实记录**我第一次给出的根因是错的**。
- **触发**：MAIN 在 `phase-02` 的真实提交端到端中发现**每一次评测都在取包阶段失败**。

---

## 1. 现象

集成库中 **103/103** 个已发布版本的 `package_digest` 与"用当前代码重新编译"的结果不符，
于是 `load_evaluation_package` 对**每一个**题目都抛 `Conflict`（409），B2 的取包失败，
评测从未成功过一次。B2 侧的表述是"评测包不可获取或不可解析"。

服务令牌核对一致，所以不是认证问题。

## 2. 我给出的第一个根因，以及它为什么是错的

**第一个判断**：`compute_package_digest` 只排除 `package_digest` 一个键，因此 `created_at`
（= `published_at`，来自 `datetime.now(UTC)` 的微秒值）参与摘要；它写进 `timestamptz(3)` 列后被
毫秒四舍五入，于是发布与取包算出的摘要不同。

**它被自己的测量推翻**（`MAIN/.runtime/verify_root_cause.py`，用修复前的规则重算并与修复前的
记录值比对）：

| 版本 | `to_wire()` 的 `created_at` | 修复前记录值 | 旧规则重算 | 匹配 |
|---|---|---|---|---|
| `53f8ac1a`（11:58 发布） | `2026-09-18T11:58:16.245Z` | `4f3b4bb8…` | `6e01896b…` | **否** |
| `0fdbb68f`（11:18 发布） | `2026-09-18T11:18:02.509Z` | `f7757794…` | `4ded35e2…` | **否** |
| `c7b989c0`（12:52 发布） | `2026-09-18T12:52:49.106Z` | `8c49bfdc…` | `8c49bfdc…` | **是** |

两点确定：

1. **`to_wire()` 把时间戳渲染到毫秒**，所以"微秒 vs 毫秒"在序列化时就消失了，对摘要**从来没有**
   影响。第一个判断在第一步就不成立。
2. 更早发布的两个版本**用任何规则都复现不出记录值**，而更晚发布的那一个**连旧规则都能复现**。
   即：**在 11:58 与 12:52 之间有一次改变了编译输出的代码变更**，它让此前发布的版本全部失效。

**还必须承认一个方法论错误**：我用"重算写回 → 取包成功"来"验证"修复。写回的值是当前代码算出的，
取包比较的也是当前代码算出的，**相符是构造上的必然**，任何规则下都会"成功"。
那组读数不构成因果证据。

## 3. 真正的结论（结构性）

`load_evaluation_package` 的设计是**重编译 + 与记录值比较**，因此 `package_digest` 实际证明的是
**"内容 + 编译代码"**，而不是"内容"。后果：

- 任何改变编译输出的代码变更都会**静默地让所有已发布版本失效**；
- 失败信息却说"**受保护内容在发布后被改动**"——把读者指向内容，而真实原因是代码演进；
- "不可变性"由一次**重算并比较**来证明，而重算依赖代码版本 —— 这与"不可变"本身矛盾。

**方向（本轮只记录，不擅自实施）**：不可变性应由**存储**保证（把编译产物或其规范字节存下来、
原样服务），而不是每次重算。这样"发布后内容被改动"仍可由 `content_digest` 检出，而编译器演进
不再使历史失效。

## 4. 本轮实际做的两件事，分开说

### 4.1 摘要作用域修正（对齐契约，**不是**本次事故的原因）

改动 `compile/canonical.py` 与 `compile/compiler.py`：
`DIGEST_EXCLUDED_KEYS = ("package_digest", "created_at")`（原先只有单键 `package_digest`）。

理由与事故无关，是两条独立证据：

- 契约对该字段的原话是 "Canonical digest of the package **content**"，`created_at` 是元数据；
- B2 的 `result_digest` 早就有显式的易变字段表
  （`VOLATILE_REPORT_FIELDS = ("generated_at", "result_digest", "evaluation_run_id")`）。

`content_digest`（存证绑定的是它）本来就不含时间戳，因此发布存证门禁未受影响。
B2 **只读取** `package_digest`、从不重算，因此无需同步。

### 4.2 数据修复（恢复服务）

`MAIN/scripts/repair_package_digests.py`：dry-run 优先、`--apply` 才写；用平台（迁移）身份
（只有它拥有表、能解除触发器）；只改 `package_digest` 一列；
`ALTER TABLE … DISABLE TRIGGER` 到重新 `ENABLE` 包在同一个 `try/finally` 里。
**刻意不是 alembic 迁移**：重算需要编译器，把编译器 import 进迁移会把今天的语义冻进一个
必须在代码演化之后仍能运行的文件里。

```text
published versions: 103
stale digests: 103
rewrote 103 package digests; immutability trigger re-enabled
re-run dry run: stale digests: 0        ← 幂等
pg_trigger.tgenabled = O                ← 不可变性已恢复

load_evaluation_package（本模块自己的取包路径，随机 6 个已发布版本）: 6/6
```

## 5. 验证与未执行

```text
ruff check .   -> All checks passed!
pytest -q      -> 366 passed（原 364，+2 条新测试）
```

**测试缺口，以及第一版守卫也是无效的**：确定性测试用同一个 `created_at` 编译两次（必然相同）；
集成夹具的 `PUBLISHED_AT` 微秒为 0。我补的第一版守卫用 `123_456` 与 `123_000`，而这两个值经
`to_wire()` 渲染后都是 `.123` —— **在旧规则下同样通过**。变异测试当场拆穿了它。

修正后的守卫改用相差**整整一毫秒**的两个值：

```text
baseline (fixed rule) : 2 passed
mutated  (old rule)   : 1 failed, 1 passed    ← 守卫真的红了
restored              : 2 passed
```

`test_publishing_with_a_microsecond_clock_survives_the_database_round_trip` 保留，但文档字符串改为
如实说法：它走"发布 → DB 往返 → 取包"这条路（曾对每个题目都 409），**但不隔离摘要规则**。

| 未执行 | 原因 |
|---|---|
| 经真实 HTTP（composition host）的取包与完整评测 | host 进程内是旧代码；重启需先重装 B2（MAIN 侧正在写入）。本模块取包路径已直接验证 6/6 |
| Linux 沙箱下的一次真实 `completed` + 分数 | 本环境 Windows，无 nsjail |
| 定位 11:58–12:52 之间那次改变编译输出的具体代码变更 | 仓库无 git 提交历史，无法做 diff；已在 §3 记录为结构性问题 |

## 6. 教训

1. **"重算并比较"不能证明不可变性。** 它证明的是"内容 + 代码"都未变，而失败信息只提到内容。
   一个会随代码演进而失效的"不可变性证明"是自相矛盾的。
2. **修复能"成功"不等于根因被找到。** 把当前代码的输出写回去，再让当前代码去核对，必然相符。
   区分这两件事的办法只有一个：**用修复前的记录值去比**（§2 那张表）。
3. **弱断言比没有断言更糟。** 我的第一版守卫在缺陷存在时仍然绿；抓到它的是变异测试，
   不是代码审查。这也再次印证本项目已记录过的同类结论。

## 7. 文档影响

- 本文件（新增）。
- `docs/README.md`：迁移 head 由 `20260915_0003` 更正为 `20260915_0004`（此前漂移）；基线日期指向本阶段。
- MAIN 侧记录：`MAIN/docs/phases/active/phase-02-ai-activation-and-integration-repair.md` §2.5 / §3.8
  （含同一份自我纠正）。
