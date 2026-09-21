# Phase 08 草稿写入的省略语义：省略即保留（CHG-0017 落地）

- **状态**：**active**（未执行项见 §5）
- **范围**：实现 `CHG-0017` —— `QuestionDraftInput` 的 `cases` 由"必填"改为**可选**，
  `replace_cases` 只在字段**被提供**时调用；补齐分支版本的继承语义；测试与文档。
  纯代码改动：无契约变更、无迁移、无新依赖。
- **触发**：该 CHG 早已 `已生效`（契约 0.12.0），但 B3 **从未实现**——全模块对 `CHG-0017` 零命中。
  后果不是抽象的：**任何**编辑（包括只改标题、只改标签）都会因 `cases` 必填被 `422` 拒绝，
  于是每次编辑都必须先把隐藏用例与参考答案读出来再发回去——正是本 CHG §1 要消除的那次往返。
  B1 依其 §4 第 64 行按住了出题表单，所以真实系统里"给题打标签"这条写路径完全走不通。

---

## 1. 落点

| 层 | 位置 | 内容 |
|---|---|---|
| 输入 DTO | `src/autograder_b3/dto/internal.py:153` | `cases: tuple[TestCaseInputModel, ...] = Field(default=(), min_length=1)`：省略即 `()`，`[]` 违 `minItems`，`null` 违类型 |
| 省略判定 | `src/autograder_b3/domain/questions.py:79`（`_cases_supplied`） | 与 `revision_note_supplied` 同一套：由传输层读 `model_fields_set` 传下来 |
| 继承 | `:141`（`_reissue_case`）、`:275`（`_write_protected_content` 的 `inherited` 参数） | 分支版本把上一版的内容**重新签发**成自己的行（新 id、同内容） |
| 差异 | `:151`（`_revision_diff_against`）、`:190`（`_submitted_content`） | 省略的字段在 delta 里按"沿用上一版"计算，与写路径同一规则（含**空白**参考答案） |
| 写路径 | `:339`（`create_question`）、`:417`（`update_question`） | 新增 `cases_supplied` 参数；分支路径做一次受保护读，供 delta 与继承共用 |
| HTTP | `src/autograder_b3/api/router.py:202`（POST）、`:286`（PATCH） | `cases_supplied="cases" in payload.model_fields_set` |
| 测试 | `tests/test_versions.py:948`–`1188`（6 例）、`tests/test_questions_api.py:1983`–`2090`（3 例） | 见 §3 |

## 2. 语义（按 CHG-0017 原文，不按转述）

1. **省略 = 保留**。`cases` 缺席时写路径**不删、不改、也不加载**该版本的用例行
   （`_write_protected_content` 在该分支直接返回）。`reference_answer` 缺席**或空白**同样保留，
   空白串不是删除它的办法。
2. **提供 = 整体替换**。`replace_cases` 先删该 suite 的全部行再插入提交的集合，
   所以永远是替换而不是叠加。
3. **没有"清空全部用例"的路径**。`minItems: 1` 未动，且"被提供"与"空"在模型层互斥：
   `[]` 违最小值、`null` 违类型。领域层还有一条内部矛盾守卫（`_cases_supplied`）：
   "声明省略却带着用例"或"声明提供却是空集"都**报错而不是猜**——两种猜法都是关于受保护内容的静默错答。
4. **分支版本继承**。`as_new_version=true` 时新版本的行属于新版本，上一版的行已冻结，
   所以省略**不能**只表示"别动这些行"——否则"改个标题"会把这道题的用例与参考答案全部丢掉。
   实现按 CHG-0017 的字面（"the version keeps the cases it already has"）把上一版的内容
   **重新签发**到新版本：同内容、新行、新的 owner 生成 id；同时 `revision_diff` 报告
   "用例集未变、参考答案摘要未变"，与它描述的那一行一致。
   - 反过来，**就地编辑草稿**（同一版本）时不复制任何东西——这是更小的操作，
     而且内容摘要（连同绑定在它上面的凭证）在一次改名后原样存活。
5. **首次写入省略 `cases`** 会产生一个没有用例的草稿：这是"没有可继承内容"的唯一入口，
   是起始状态而不是转移。它**不可发布**（`publish` 的完整性门禁要求 ≥1 用例，
   且这种草稿连 `content_digest` 都是 `None`，所以也无法被存证）。

## 3. 验证

```text
uv run ruff check .
  -> All checks passed!

uv run pytest
  -> 382 passed, 6 warnings in 47.46s        （373 -> 382，本轮 +9 例）
```

`CHG-0017` §5 的五条，逐条对应：

| §5 | 测试 |
|---|---|
| 1 省略 `cases` 的 PATCH 返回 200，且用例集合逐字节不变 | `test_a_correction_that_omits_both_protected_fields_is_accepted`（HTTP 200）、`test_omitting_cases_keeps_the_stored_case_set_unchanged`（就地编辑：含 `case_id` 的逐字段相等 + 内容摘要不变）、`test_a_branched_version_that_omits_cases_records_no_case_change`（分支：内容逐字段相等、id 必然新签发且与旧集合不相交） |
| 2 省略时受保护内容"没有被读取过" | `test_omitting_cases_keeps_the_stored_case_set_unchanged`（先改标题，再断言用例仍在）、`test_a_title_only_edit_leaves_the_reference_answer_row_untouched`（参考答案行连 id 一起不变） |
| 3 提供 `cases` 时整体替换 | `test_supplying_cases_replaces_the_whole_set_rather_than_adding_to_it`（旧行按 id 与按名字都不再存在） |
| 4 `cases: []` 被拒绝，且发生在写任何东西之前 | `test_null_and_empty_case_sets_are_refused_without_writing_anything`（`[]` 与 `null` 在 PATCH 与 POST 上都 `422`；PATCH 之后版本号未推进、受保护内容逐字段不变） |
| 5 只改标题不改变参考答案 | `test_a_title_only_edit_leaves_the_reference_answer_row_untouched`（含"空白串也不是删除手段"的第二个分支） |

补的三条（本轮自己加的边界）：

- `test_a_version_created_without_cases_cannot_be_published`：首次省略产生的无用例草稿
  `content_digest is None`，`publish` 抛 `ProtectedContentMissing`（用 `attestation=False`，
  确保拒绝来自发布门禁本身而不是测试助手）。
- `test_declaring_cases_omitted_while_sending_them_is_refused`：内部矛盾守卫两个方向都报错，且什么都没写。
- `test_a_body_that_omits_the_required_public_fields_is_still_refused`：**记录 CHG-0017 没有移动的边界**——
  `{"tags": [...]}` 单独一个字段仍然 `422`，因为 `title`/`stem_markdown`/`language`/`adapter`
  在契约里依旧是必填（它们是公开内容，不是受保护内容）。拒绝信息里**不再出现 `body.cases`**，
  这正是本 CHG 生效的样子。

### 3.1 一处必须说清的措辞

`CHG-0017` §5.2 的原话是"省略 `cases` 时受保护内容**没有被读取过**"，并自己给出了证明方式
（"用'先改标题、再断言用例与参考答案仍在'来证明，而不是靠'没有报错'"）。本轮按该证明方式执行。
但如实说明：B3 内部**仍会读**该版本自身的内容——`refresh_content_digest` 必须哈希它才能维持
"存证绑定内容"这条链。所以能被证明的是**内容没有经调用方往返**（B1 不必再把它送出去再送回来），
而不是"进程内从未读取"。分支路径则确实会额外读一次上一版的内容，因为省略的字段要从那里继承。

## 4. 真实栈复测（原始读数）

宿主 `127.0.0.1:18000`（MAIN 的 composition host）全程在跑，教师令牌来自
`POST /api/v1/auth/session`（`t.zhang`，`roles=assistant,teacher`，course `8cf8259c-…`）。

**修复前（18000 上正在跑的安装副本）** —— 请求体只带四个公开必填字段 + `tags`，**不带 `cases`/`reference_answer`**：

```text
GET  /api/v1/questions/coverage?course_id=8cf8259c-…
  -> 200  {"total_questions":102,"tagged_questions":0,"untagged_questions":102,"tags":[]}

PATCH /api/v1/questions/81e746e7-4e5d-446d-bdb3-98cacbe6448e?as_new_version=true
  body keys: ['adapter','language','stem_markdown','tags','title']
  -> 422  {"detail": "request body failed validation: body.cases", "status": 422, ...}
```

（对比：`{"tags":["unix"]}` 单独一个字段会报 5 个缺失字段——`body.adapter, body.cases,
body.language, body.stem_markdown, body.title`。其中**只有 `body.cases` 是 CHG-0017 的范围**，
其余四个是契约仍然要求的公开字段。见 §6.3。）

**修复后** —— 同一道题、同一个请求体，服务的是安装进 MAIN venv 的修复版 B3：

```text
PATCH /api/v1/questions/81e746e7-…?as_new_version=true
  -> 200   tags: ["unix"]，新版本 a9a62fb8-00c5-4989-b7a6-a2ed3570fbe1（version 2, draft）
  revision_diff:
    case_count_before = case_count_after = 4
    case_set_changed   = false
    weights_changed    = false
    fixtures_changed   = false
    stem_length_before = stem_length_after = 264
    reference_answer_digest_before == reference_answer_digest_after
      = ccca4a3d0e01a6b5d31640e0b8344a3a5036600a83f030643ca2acf6bec03d27
  revision_note: "supersedes 01c50a65-33cf-4633-9340-237bbdae4a25: no content change was recorded"

GET  /api/v1/questions/coverage?course_id=8cf8259c-…
  -> 200  {"total_questions":102,"tagged_questions":1,"untagged_questions":101,
           "tags":[{"tag":"unix","question_count":1}]}
  tagged 0 -> 1，untagged 102 -> 101，total 不变
```

`reference_answer_digest_before == after` 是关键读数：参考答案连同隐藏用例一起原样随新版本继承，
而调用方**一次都没有发送过它们**。`revision_note` 也如实说"没有内容变化"。

### 4.1 为什么读数来自一个独立进程，而不是 18000 那个宿主

两个与本次改动**无关**的环境事实，都会让"重启 18000 宿主"变成不可逆操作：

1. **现在起不来新的 composition host**：宿主启动时 B4 的 `validate_registry()` 校验各模块
   `module.yaml` 与运行期描述符一致，当前报

   ```text
   autograder_b4.registry.RegistryError: B2: module.yaml and the runtime descriptor disagree
     -- descriptor.contract_version='0.14.0' but module.yaml declares '0.15.0'
   ```

   即 MAIN/B2 正在做契约版本跃迁而 B2 的安装副本尚未同步。这是别人正在进行的工作，
   B3 没有理由去动 B2 的安装（`--reinstall-package autograder-b2` 不在本工作包授权范围内）。
   因此**重启 18000 宿主会让它起不来**，我没有那么做。
2. 所以修复后的读数取自一个**由本轮启动、结束后立即关闭**的真实 uvicorn 进程（端口 18001），
   它服务的是同一个 `autograder_b3.api.app:create_app`、用**同一个 `.env.integration`**、
   连**同一个集成库**（55432）、并用 `B4_JWKS_URL` 指向**正在跑的 18000** 去验真实的 Ed25519 令牌。
   它不是 composition host（少了其它模块的挂载），但它是真实的 HTTP + 真实 JWT + 真实题库。

   启动时踩到一个值得记录的点：宿主是**按模块命名空间**把 settings 交给各模块的
   （`B3_DATABASE_URL` → `DATABASE_URL`），所以独立启动必须在进程环境里自己做这个映射，
   否则 `Settings.from_env()` 会落到默认值——我第一次就落到了 B3 的**测试库 55434**，
   于是 PATCH 因为查不到题而返回 `404`（而列表 200，因为列表用的是另一个角色/另一个库）。这不是本轮改动的问题，
   但如果将来有人想脱离宿主单跑 B3，这个映射是必须知道的一条。

**后记（同日，Phase 09 期间补做）**：B2 的描述符同步之后，新宿主可以启动了，于是在 **composition host**
上补测了同一条，读数如下（完整上下文见 [Phase 09](phase-09-reference-answer-channel.md) §4.1）：

```text
draft created: question=90001a8f-1871-4f29-ac12-4eb9b8325d15 version=30552812-633a-431c-968f-4eeffbe9a392
content_digest before = ea54c6fceb570857302bd3355912ce22d45dfb81ebcea39da2b7aa642f1fa287
PATCH body key set: ['adapter', 'language', 'stem_markdown', 'tags', 'title']     ← 无 cases / 无 reference_answer
PATCH without cases/reference_answer -> 200
content_digest after  = ea54c6fceb570857302bd3355912ce22d45dfb81ebcea39da2b7aa642f1fa287
protected content untouched (digest unchanged): True
same version row edited in place: True
coverage (published_only=false): tagged 4 -> 5, total 105 -> 106, tagged delta = 1
```

摘要逐字不变就是"用例与参考答案都还在"的证明（摘要覆盖两者），而版本行仍是原来那一行。
本轮用的是**新建的草稿题目**，没有动任何既有题库题目；副作用见 Phase 09 §4.2。

### 4.2 对集成库的副作用（如实）

- 题目 `81e746e7-4e5d-446d-bdb3-98cacbe6448e` 现在带标签 `unix`，
  并多了一个**草稿**版本 `a9a62fb8-00c5-4989-b7a6-a2ed3570fbe1`（version 2）。
- 它原来的已发布版本 `01c50a65-33cf-4633-9340-237bbdae4a25` 未被触碰（不可变触发器保证）。
- 题目 `status` 仍是 `published`，所以覆盖度统计里它仍被计入（`tagged+untagged==total` 成立）。
- 宿主 18000 在复测前后均 `ready=true`，四个模块 `healthy=true`；我的临时进程已关闭，18001 不再监听。
- 该草稿版本**没有** HTTP 删除入口（契约未声明 DELETE），因此它会留在库里，供 MAIN 处置。
- 补做那次（§4.1）另建了一个草稿题目 `90001a8f-1871-4f29-ac12-4eb9b8325d15`（带标签 `probe-chg0017`）。

## 5. 未执行 / 未验证（如实）

| 未执行 | 原因 |
|---|---|
| ~~在 composition host 上复测~~ | **已补做（同日，Phase 09 期间）**，读数见 §4.1；当时未做的原因与其后的变化也记在那里 |
| B1 出题表单按 CHG-0017 §4 第 64 行"放开只改标题/标签的保存" | 属 B1；本文件只提供 B3 已落地的证据（§3 的 9 例 + §4 的真实读数）。**注意**：B1 仍必须发送 `title`/`stem_markdown`/`language`/`adapter`（契约要求），只有 `cases`/`reference_answer` 可以省略 |
| 三个 adapter 示例题目在真实宿主上的发布链路 | 与本轮语义无关；本轮只碰草稿写入路径 |
| `fixtures` 是否应随分支继承 | **未做**：`fixtures` 不是 `QuestionDraftInput` 的字段（fixture 由单独端点上传），CHG-0017 也不涉及它。现状是分支版本不继承 fixture，而 `revision_diff.fixtures_changed` 会如实报 `true`（对带 fixture 的题目）。见 §6.2 |

## 6. 观察与移交（本轮不改，交给 MAIN / 相关模块判断）

1. **`adapter` 写必填但读不回来**。契约 `QuestionDraftInput.required` 含 `adapter`，
   而 `QuestionVersionPublic` 只暴露 `adapter_kind` / `max_files` / `max_total_bytes`——
   `args`/`stdin`/`expect_exit_code`/`comparison`/`files`/`callable` **没有任何读出口**。
   于是纯 API 客户端无法为一次"只改标签"的编辑重建请求体（我在 §4 的复测里是从
   `b3.question_versions.adapter_manifest` 直读才凑出合法请求体的）。这不在 CHG-0017 范围内
   （那个 CHG 只处理两个受保护字段），但它是同一条"写得进去、读不出来"的老问题，
   若要真正让 B1 之外的客户端也能改标签，需要 MAIN 决策一个新出口。
2. **分支版本不继承 fixture**（上文 §5 末行）。带 fixture 的题目一旦 `as_new_version=true`，
   新版本声明了 `adapter.files` 却没有存储的 fixture 字节，`publish` 会在摘要比对处拒绝它。
   本轮未改（超出 CHG-0017），但值得单独评估。
3. **"只带 `tags` 的 PATCH 仍然 `422`"是契约要求，不是本轮缺陷**。
   如果目标真的是"允许只提交变动字段"，那是给 `title`/`stem_markdown`/`language`/`adapter`
   也赋予省略语义的**另一个契约变更**（`language`/`adapter` 还参与内容摘要），不能顺手做。

## 7. 文档影响

- 本文件（新增）。
- `docs/api/contract.md`：新增"草稿写入的省略语义（CHG-0017）"小节（含四个公开字段仍然必填、
  `adapter` 无读出口的待决问题）。
- `docs/testing/cases.md`：新增 `TC-DRF-01`（省略即保留：逐字段不变 / 整体替换 / `[]`&`null` 被拒 /
  无清空路径 / 无用例草稿不可发布）、`TC-DRF-02`（只改标题或标签不触碰受保护内容），
  并更新"当前测试实现"表的例数（实测 382）。
- `docs/requirements/traceability.md`：`RQ-B-3-20` 行补省略语义与证据。
- `docs/requirements/acceptance.md`：新增**验收 3-20-12**（草稿写入省略语义，`CHG-0017`）。
- `docs/README.md`：状态表新增一行；工具链读数更新为实测值；基线指向本阶段。
- **未改**：`MAIN/contracts/**`、`B3/module.yaml`、`B3/contracts.lock`、`B3/pyproject.toml`、
  `B3/uv.lock`、`B3/alembic/**`、`plugin.py` 的 `descriptor.contract_version`。
