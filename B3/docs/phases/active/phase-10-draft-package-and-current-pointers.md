# Phase 10 草稿也能取到执行输入（CHG-0028），以及两处"指向过时机制"的文案

- **状态**：**active**（未执行项见 §6）
- **范围**：实现 `CHG-0028` —— `GET .../evaluation-package` 对**草稿**也返回 `200`（编译该版本
  **当前**受保护内容、`created_at` 用内容时间戳、**跳过**摘要比对），已发布路径的摘要比对**保持不变**；
  外加两处拒绝文案的真实修正（`publish` 无存证的指引、`reference-answer` 情形 ② 的因果断言）。
  **无迁移、无新表、无新依赖、无契约改动。**
- **触发**：`CHG-0019` + `ADR-0015` 的发布闭环在真实 B3 上仍**不可达**——两条各自合理的规则合起来矛盾：
  `getEvaluationPackage` 拒绝草稿，而 `recordReferenceAttestation` 只接受草稿，`publish` 又要求先有存证。
  于是"验证草稿 → 存证 → 发布"这条链的第一步就断了。

---

## 1. 落点

| 层 | 位置 | 内容 |
|---|---|---|
| 错误 | `src/autograder_b3/errors.py:168` | `PackageContentMissing(Conflict)` → `409`，slug `package-content-missing`；**`409` 的新含义是"没有可编译的内容"，不再是"这是草稿"** |
| 内容加载 | `src/autograder_b3/domain/version_content.py:73` | `load_version_content(..., require_reference_answer=True)`；包路径传 `False`（包不含参考解答，它有自己的通道与自己的 `409`） |
| 快照 | `src/autograder_b3/domain/versions.py:67`（`load_draft_snapshot`）、`:50`（`load_published_snapshot` 不变） | 草稿快照用 `updated_at` 作为 `created_at`；`snapshot_for(created_at=…)` 的参数改名（字段名保持——该类型在冻结清单上） |
| 端点逻辑 | `src/autograder_b3/domain/packages.py:44`（`:113` = 两条路径共用的 `_compile`） | 草稿：200、不比对；已发布：200、仍比对 |
| 文案修正 ① | `src/autograder_b3/domain/attestations.py:155` | `publish` 无存证的 `detail` 改为指向**当前**补救办法（教师侧"验证并发布"／平台的 reference-verification 端点），不再点名 `MAIN/scripts/verify_question_bank.py` |
| 文案修正 ② | `src/autograder_b3/domain/packages.py:129`（`load_reference_answer`） | 情形 ② 的 `detail` 只说**缺的是摘要**，不再断言"需要一个用例"（见 §4.3 的真实数据） |
| 测试 | `tests/test_questions_api.py`（4 例新增/改写）、`tests/test_versions.py`（1 处断言改向） | §3 |

## 2. 语义（按 `CHG-0028` 原文）

| 版本状态 | 编译输入 | `created_at` | 与记录的 `package_digest` 比对 |
|---|---|---|---|
| 已发布 | 现状不变（冻结内容） | `published_at`（现状） | **是**（现状保留） |
| **草稿** | 该版本**当前**受保护内容 | 内容自身时间戳 = 行的 `updated_at`（**不是** `published_at`：实现**根本不读这一列**，草稿带不带它都不影响——DB 也不禁止草稿带它，见 §4.1；**不是** `now()`） | **否**：草稿没有已记录的摘要可比 |

- 响应**仍然**给出 `package_digest`：它是"本次编译出的内容的摘要"，诚实、只是尚未被任何记录绑定。
  `publish` 才是绑定它的那一刻。
- **`409` 的唯一含义是"没有可编译的内容"**（例如一个用例都没有），不再表示"这是草稿"。
- `recordReferenceAttestation` **仍然只接受草稿**（契约现在把它写明了），本包未动。
- **草稿包不落库**：不写摘要、不缓存，因此不会变成第二份事实源。

## 3. 验证

```text
uv run ruff check .
  -> All checks passed!

uv run pytest
  -> 393 passed, 6 warnings in 53.93s        （389 -> 393，本包 +4 例）
```

`CHG-0028` §9 的六条：

| §9 | 测试 |
|---|---|
| 1 草稿 → `200`，包内 `question_version_id` 是该草稿 | `test_evaluation_package_for_a_draft_is_served`（同时断言教师令牌仍 `403`：服务草稿不等于放宽鉴权） |
| 2 草稿 `created_at` 不是 `now()`，重复取相同 | `test_a_draft_package_reports_the_content_timestamp_and_is_stable`：**把应用时钟推进 90 秒而内容不动 → `created_at` 不变**（这正是能抓住 `now()` 实现的断言）；编辑内容 → 它跟着内容动；两次都与版本行自己的 `updated_at` **精确相等** |
| 3 已发布 → `200` 且**仍然**比对（改内容 → `409`） | `test_the_published_path_still_compares_the_recorded_package_digest`：先断言返回值等于库里记录的 `package_digest`，再用一条 SQL 把某用例的期望输出改掉（**只在那一条语句上**临时禁用不可变触发器，随后立即恢复），得到 `409` |
| 4 无用例 → `409`（新含义） | `test_a_version_with_no_test_cases_has_no_compilable_content`（`CHG-0017` 之后可达；断言 slug `package-content-missing` 与 "no compilable content"） |
| 5 端到端：验证草稿 → 存证 → `publish` 成功 | `test_a_draft_can_be_verified_and_published_without_publishing_it_first`（按 B2 的方式取**包 + 参考解答**两件输入、断言两者的摘要口径一致、存证、发布 `200`，并断言发布后取到的包与草稿时**同一个** `package_digest`） |
| 6 编辑草稿后旧存证失效 → `publish` 被拒 | 既有用例 `test_editing_the_draft_invalidates_the_attestation` 逐字覆盖（存证 → 改内容 → `422 reference-attestation-missing` → 重新验证 → 发布 `200`），本轮未重复实现 |

**§9.5 只做了一半，如实标注**：本平台没有 nsjail，所以"B2 真的执行参考解答并逐用例比对"这半**没跑**；
测试里那一步由"记录存证"代表（B3 能观察到的就是这件事）。**门槛侧**（无存证即 `422`、用户令牌 `403`、
错摘要 `409`）已由 MAIN 在真实端口取证，本轮把**草稿侧的门**打开了。

## 4. 真实宿主读数（composition host + 真实 JWT + 真实题库）

宿主：18001 上的 composition host（`run_host.ps1`、`.env.integration`，B3 由
`uv sync --all-groups --extra modules --reinstall-package autograder-b3` 安装）。令牌：`t.zhang`（教师）
与 `local-integration-service-token`（服务）。草稿 `626fc91c-9f01-4846-95e1-58595dc0aa66`、
已发布 `34d07765-49ea-4171-997b-dd25693716ff` 由 MAIN 指定。

```text
draft      626fc91c…: status=draft     published_at=NULL                    updated_at=2026-09-18 11:16:22.582  content_digest=NULL      cases=2 answers=1 package_digest=NULL
published  34d07765…: status=published published_at=2026-09-18 13:17:03.824 updated_at=同值                     content_digest=0c036fc3… cases=1 answers=1 package_digest=1e997bbf…
# 两列是两件事：content_digest 绑存证（CHG-0019），package_digest 是发布时记录的包摘要。

GET …/{626fc91c…}/evaluation-package -> 200
  question_version_id=626fc91c-…  created_at=2026-09-18T11:16:22.582Z  adapter=stdin  tests=2
  package_digest=2e77fe17270e…（本次编译算出；没有记录绑定它，见 §2）
  再取一次 -> 200，created_at identical=True，package_digest identical=True
  created_at == 该版本自身的内容时间戳（updated_at）= True          ← §9.2

GET …/{34d07765…}/evaluation-package -> 200
  package_digest=1e997bbf… == 库里该行记录的 package_digest = True    ← §9.3 的"比对仍在、且通过"
  created_at=2026-09-18T13:17:03.824Z（= 该行 published_at）

POST …/{30552812…}/publish（该草稿无存证）-> 422
  names the current remedy ('verify and publish') = True
  names the retired script = False
  keeps the digest = True                                            ← 文案修正 ①
```

**§9.4（无用例 → `409`）在集成库上取不到**：只读查询确认集成库（55432）里**没有任何版本是零用例**
（`versions with zero test cases: 0`；模块本地库 55434 有 4 个，是套件自己造出来的，见 §4.1 第 2 条），
而本轮**刻意不新建探针题目**（MAIN 在上一包明确提示：无 DELETE 入口 ⇒ 能复用既有题目就不要新建）。
因此这一条在真实宿主上只有 pytest 证据。
**§9.3 的"改内容 → `409`"也只有 pytest 证据**：那需要篡改共享集成库的受保护内容，本包不做。

### 4.1 顺带的两条观察（交 MAIN，未改代码）

1. **"草稿没有 `published_at`"不是约束保证，只是数据现状**：DB 的 CHECK 只要求"非草稿必须有"
   （`ck_question_versions_published_at_required`），所以草稿**可以**带 `published_at`。
   本实现因此**根本不读这一列**（用 `updated_at`），带与不带都一样工作。
   只读复核（app 角色按状态聚合，`has_pub = count(published_at)`）：集成库 55432 = `draft` 9 行
   `has_pub=0` / `published` 103 行 `has_pub=103`；模块库 55434 = `draft` 65 行 `has_pub=0` /
   `archived` 2 行 `has_pub=2` / `published` 83 行 `has_pub=83`。**两个库里 `published_at` 非空的草稿都是 0 行。**
   要点是"**代码保证不读它**"，不是"数据保证它没有"。
   （本文件早期版本此处曾把一个行级反例记成 `626fc91c…` 的 `updated_at == published_at`——错的是那次读数：
   `updated_at == published_at` 成立的是**已发布**的 `34d07765…`，因为发布路径一次写这两列
   （`repositories.py:335`）。聚合查询与两条行级查询均已重取，见上方的读数块。）
2. **"没有摘要"从来不是"没有用例"造成的**：集成库 55432 里 `content_digest` 为 NULL 的草稿有 4 个
   （非 NULL 的 5 个），而零用例的版本有 **0** 个——那 4 条 NULL 摘要的行**都带着用例**
   （只读复核用 internal 角色做**只计数**的联合分布查询：`(状态, 摘要为 NULL, 有用例)` →
   `(draft,否,是)=5`、`(draft,是,是)=4`、`(published,否,是)=103`）。`refresh_content_digest`
   在每次 API 写入时都会算，这些行只是从未经过写路径。这条数据直接推翻了我情形 ② 文案里的因果断言，见 §4.3。
   （模块本地库 55434 是测试库，它**有**零用例的草稿——3 条 NULL 摘要 + 1 条有摘要——那正是 §9.4 能在 pytest
   里被覆盖的原因；集成库没有，所以 §9.4 在真实宿主上取不到，见 §6。）

### 4.2 `CHG-0026` 的复核（同一次宿主，两个 `409` 并排）

```text
① 63d2e052… -> 409 reference-answer-missing
   detail: 'this version has no reference answer for its declared language (python);
            there is nothing to execute'
② 38e1c05d… -> 409 reference-answer-missing
   detail: 'this version has no content digest yet, so an answer executed against it could not
            be attested: the digest is what binds the answer to the content it was run against,
            and this version does not have one'
distinguishable: True
```

情形 ② 的那一行（`38e1c05d…`）在库里是 `cases=1 answers=1 content_digest=NULL` ——**它有用例**。

### 4.3 文案修正 ②（本轮自己发现的，与 ① 同源）

修 ① 时我读了同类文案，发现 `reference-answer` 情形 ② 的 `detail` 结尾是
"a version needs at least one test case first"——它把一个**因果**写进了拒绝信息，而在集成库（55432）里
**每一条**能触发情形 ② 的行都不缺用例（§4.1 第 2 条：4 个 NULL 摘要的草稿，0 个零用例版本）。
这正是 MAIN 刚让我修的同一类病：**在用户被卡住的那一刻，告诉他一个不成立的原因**。
现在该 `detail` 只点名缺的是**摘要**（以及它对存证的意义），不再断言原因；
两条 `detail` 的可区分性由**镜像断言**保住（情形 ① 断言不含 `digest`/`test case`，情形 ② 断言不含
`no reference answer`），且"`test case`"这个词条现在被断言**不得**出现在情形 ② 里——文案若退回旧写法，测试会红。

## 5. 副作用

- 本轮**没有新建任何题目或版本**：真实读数全部是 `GET` + 只读 SQL，唯一的写调用是本该被拒绝的
  `POST …/publish`（`422`，无副作用）。
- 我启动的 18001 宿主已关闭；MAIN 服务的 18000 宿主在复测前后均 `ready=true`、四模块 `healthy=true`。

## 6. 未执行 / 未验证（如实）

| 未执行 | 原因 / 归属 |
|---|---|
| B2 真的执行参考解答并逐用例比对（`CHG-0019` §7.2/§7.3 的"执行"那半） | 本平台无 nsjail；属 B2 与 Linux 环境。B3 这侧的两件输入（包、参考解答）与存证/发布门禁都已就绪并有真实读数 |
| §9.3 的"改一行受保护内容 → `409`"在真实库上 | 需要篡改共享集成库的受保护内容（并临时禁用不可变触发器），本包不做；pytest 用一次性触发器禁用覆盖了它 |
| §9.4 在集成库上取 `409` | 集成库里没有零用例的版本（模块测试库 55434 有，由套件构造，§4.1 第 2 条）；按 MAIN 的提示不新建探针题目（无 DELETE 入口）。pytest 覆盖 |
| `ADR-0016` 的"包产物冻结" | `CHG-0028` §4 明确本包不涉及；草稿包每次现编译，不缓存、不落库 |
| `CHG-0025`（草稿内容的读出口） | 已改排到 `0.17.0`，**不在本包**；本轮未做，也未顺手做 |
| 契约里 `ReferenceAttestationInput.verifier` 的过时示例（`MAIN/scripts/verify_question_bank.py`） | 属 MAIN 的 `0.17.0` 批次，MAIN 明确说不要动；本轮未改契约 |

## 7. 文档影响

- 本文件（新增）。
- `docs/api/contract.md`：evaluation-package 小节（草稿也服务、两类路径的差别、`409` 的新含义）+
  `404 vs 409` 行的情形 ② 措辞更新（指向本文件 §4.3 的数据）。
- `docs/testing/cases.md`：新增 `TC-PKG-01/02/03`，并把 `TC-VER-06` 的行补上"拒绝文案指向当前补救办法"；
  例数按实测重列（393）。
- `docs/requirements/traceability.md`：`RQ-B-3-20` 行补草稿取包与闭环；基线指向本阶段；覆盖自检补一条。
- `docs/requirements/acceptance.md`：新增**验收 3-20-14**（草稿可取执行输入 + 闭环），并更新 3-20-13 里
  情形 ② 的 `detail` 描述。
- `docs/README.md`：状态表新增一行、工具链读数、基线、阶段导航、"新出口在真实宿主"一行。
- `docs/collaboration/onboarding.md` / `docs/testing/strategy.md` / `docs/runbooks/local-development.md`：
  当前分支读数 389 → 393。
- **复核后的记录修正（只改文档，未动代码）**：MAIN 独立复核指出两处读数记错，均已按只读 SQL 重取后改准——
  ① §2/§4 的 `published_at` 口径统一为"**代码保证不读它**"（删去"某个草稿的 `updated_at == published_at`"
  这个行级反例：成立的是已发布行，见 §4.1 第 1 条）；② §4 读数块把 `0c036fc3…` 标为 `content_digest`、
  并补上该行的 `package_digest=1e997bbf…`。`api/contract.md` 里复述的同一句、以及 `acceptance.md` 里
  "零用例版本"的范围与失败判定理由同步收窄/改准。
- **未改**：`MAIN/contracts/**`、`B3/module.yaml`、`B3/contracts.lock`、`B3/pyproject.toml`、`B3/uv.lock`、
  `B3/alembic/**`、`plugin.py` 的 `descriptor.contract_version`（仍 `0.16.0`）。
