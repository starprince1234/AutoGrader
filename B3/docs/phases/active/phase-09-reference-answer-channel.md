# Phase 09 参考解答的交付通道（CHG-0023）

- **状态**：**active**（未执行项见 §5）
- **范围**：实现 `CHG-0023` 的新内部端点
  `GET /api/v1/question-versions/{question_version_id}/reference-answer` 与 schema `ReferenceAnswer`。
  数据与仓库层方法都已在位（`b3.reference_answers`、`list_reference_answers()`）：**无迁移、无新表、无新依赖**。
- **触发**：`CHG-0019` 要 B2"在沙箱按该版本自己声明的适配器与比较策略执行**参考解答**"，
  而契约里**没有任何通道**把参考解答交给 B2——evaluation package 刻意不含它、公开 DTO 禁止回显它。
  B2 按规程停下上报，MAIN 裁决新增一个内部端点（而不是把参考解答塞进 evaluation package）。

---

## 1. 落点

| 层 | 位置 | 内容 |
|---|---|---|
| 错误 | `src/autograder_b3/errors.py:146` | `ReferenceAnswerMissing(Conflict)` → `409`，slug `reference-answer-missing`；两个状态共用它、用 detail 区分 |
| 内部 DTO | `src/autograder_b3/dto/internal.py:414`（`ReferenceAnswerDelivery`） | 五个字段 + `to_wire()`；**刻意不放在 `dto/public.py`**（见 §2.1） |
| 读路径 | `src/autograder_b3/domain/packages.py:82`（`load_reference_answer`）、`:32`（`ADAPTER_KINDS`） | 受保护会话读取；`404`/`409` 分开；答案正文永不进日志 |
| 路由 | `src/autograder_b3/api/router.py:602` | `tags: [internal]` + `require_service()`，与 `evaluation-package`、`attestations` 同级 |
| 测试 | `tests/test_questions_api.py:1085`–`1250`（6 例）、`tests/test_logging.py:220`（1 例） | 见 §3 |

## 2. 设计与取舍（逐条写清理由，便于复核）

### 2.1 为什么响应的投影写在 `dto/internal.py`，而不是 `dto/public.py` / `dto/wire.py`

三条独立理由，任何一条都足以决定：

1. `dto/public.py` 的边界断言要求该模块**不含任何受保护词汇**（ADR-0004；
   `test_public_dto_module_has_no_protected_identifier` 会红）。
2. 公开序列化出口 `serialize_public` 会对键名 `reference_answer` **直接抛错**
   （`assert_no_protected_keys`）——这正是保护学生的那道闸门。所以这个响应**不可能**走公开出口。
3. `dto/wire.py` 被 `test_protected_vocabulary_is_absent_from_wire_producing_modules` 逐 `ast.Dict`
   扫描受保护键名，因为 evaluation package **绝不允许**长出 `reference_answer` 键。
   本端点必须有这个键，因此投影只能落在"允许说出受保护词汇"的内部模块里——
   与 `EvaluationPackage.to_wire()` 同一做法，只是它的 wire 函数在 `wire.py`、我的在 `internal.py`。

### 2.2 摘要取"版本行上已存的值"而不是重算

`content_digest` 覆盖参考解答本身（`domain/content.py`），因此它把"执行的是哪份答案"与"存证的是哪份内容"
绑在一起。取值来源刻意选**版本行的 `content_digest`**：

- 它与 `QuestionVersionPublic.content_digest`（验证器今天读到并据以存证的那个值）**逐字相同**，
  同一版本不会出现两个摘要；
- `publish` 用重算值与存证比对，而草稿每次写入都由 `refresh_content_digest` 把该列更新为同一个函数的结果，
  所以两者在正常状态一致（§3 的真实读数即为证明）；
- 不需要额外读一遍受保护内容，也不会因内容不完整而中途抛错。

### 2.3 `409` 覆盖两个状态（CHG §8.3 只点名了一个）

契约对 `409` 的原话是"版本存在但没有参考解答……**资源在，是它的状态让验证不可能**"。据此，
以下两种状态都返回 `409`，用 detail 区分（不引入契约未声明的 `422`，也不退回 `404`）：

1. 该版本**没有**它声明语言的参考解答（或存的是空白串）；
2. 该版本**还没有 `content_digest`**（即没有用例，`refresh_content_digest` 返回 `None`）——
   `CHG-0017` 之后"有用例"不再由写路径保证，所以这是一个**可达**状态；
   此时响应里根本没有可给的摘要，而摘要不给就无法存证，返回 `200` 只会让 B2 拿一个不存在的摘要去存证。

第 2 条原是我对契约那句话的**解读**（契约没有点名它），当时按"需 MAIN 确认"登记。
**后记（已裁决）**：MAIN 判定这一点属于**契约措辞含糊**而非实现越界——同一段里第一句把 `409` 收窄到
"没有参考解答"、第二句又放宽到"状态使验证不可能"，读者必须猜哪一句是判据——并**保留** `409` 覆盖两种情形，
见 [`CHG-0026`](../../../../MAIN/docs/contracts/changes/CHG-0026-reference-answer-conflict-semantics.md)
（排入 `0.16.0`，与 `CHG-0025` 同批）。理由与本文相同：情形 ② 下验证必然无法完成，`200` 只是把失败推后。
该 CHG §5 明确 **B3 无代码改动**，只把文档的悬置项改为指向它；§8.2 另把"两种情形的 `detail`
必须可区分"写成验收项，因此本轮补了**交叉排除断言**（§3 末），使"点名"可机械验证而不是只写在措辞里。

### 2.4 鉴权与"教师也不能读"

`require_service()` 是处理函数的第一件事，发生在读任何内容之前：无令牌 `401`、
用户令牌（含教师、助教、跨课程教师）`403`。教师接触参考解答的路径是**出题写路径**
（`QuestionDraftInput.reference_answer`），不是这里；学生则任何路径都读不到。
这与 `getEvaluationPackage` 复用同一个 `serviceToken` scheme：**没有新增凭据、没有新增信任关系**。

## 3. 验证

```text
uv run ruff check .
  -> All checks passed!

uv run pytest
  -> 389 passed, 6 warnings in 47.58s        （382 -> 389，本轮 +7 例）
```

文档同步完成后再次重跑，读数不变（同一套件，只有耗时波动）：`389 passed, 6 warnings in 47.15s`；
`ruff` 仍为 `All checks passed!`。

`CHG-0023` §8 的 7 条，逐条对应：

| §8 | 测试（`tests/test_questions_api.py` 除最后一条外） |
|---|---|
| 1 `serviceToken` → `200`，`language` 等于版本声明的语言 | `test_reference_answer_is_served_to_services_for_the_declared_language`（夹具在**同一版本**上放两份答案：python + bash，再把版本改成声明 bash；只有一个答案时"按声明语言取"和"取唯一一行"看起来完全一样） |
| 2 无令牌 / 用户令牌 → `401` / `403` | `test_reference_answer_is_never_readable_by_a_user_token`（teacher/assistant/student/跨课程教师全 `403`、无令牌与未知令牌 `401`，并在被拒响应里搜答案正文；最后用服务令牌拿到 `200`，证明拒绝是授权规则而不是端点坏了） |
| 3 版本存在但无参考解答 → `409`（不是 `404`） | `test_a_version_without_a_reference_answer_is_409_not_404`（先读该版本自己的 DTO 得 `200`，证明资源确实在） |
| 4 不存在的版本 → `404` | `test_an_unknown_version_has_no_reference_answer_to_serve` |
| 5 `content_digest` 与 `CHG-0008` 存证的摘要逐字相等 | `test_the_reference_answer_digest_is_the_one_the_attestation_binds`（与公开 DTO、与 `POST /attestations` 记录的摘要三者相等；发布**前后**都相等，因为 B2 的真实顺序是验证→存证→发布） |
| 6 响应只有那五个键 | 与 §8.1 同一用例：顶层键集合**从契约文件 `b3.yaml` 的 `ReferenceAnswer` 段读出**并逐字相等；另外断言 `tests`/`cases`/`fixtures`/`expected_*`/`package_digest`/`stdin` 都不在响应里，隐藏用例名与隐藏期望输出也不出现在响应原文 |
| 7 日志与事件不出现参考解答正文 | `tests/test_logging.py::test_serving_the_reference_answer_does_not_log_it`（在原始日志流里搜答案正文；事件面**没有**——这个端点不发布任何事件，发布事件的 payload 键集合另有既有用例把守） |

外加一条 §8 没点名的状态：`test_a_version_with_no_test_cases_cannot_supply_its_answer`（§2.3 第 2 条）。

**`CHG-0026` §8 的四条**（MAIN 把 §2.3 第 2 条裁决为契约措辞含糊、保留 `409` 之后补写的断言；
B3 无代码改动，只在既有用例上加了**交叉排除断言**，所以本例数不变）：

| `CHG-0026` §8 | 断言 |
|---|---|
| 1 无参考解答 → `409`，detail 说明"该语言没有参考解答" | `test_a_version_without_a_reference_answer_is_409_not_404`：detail 同时含 `reference answer` 与语言（`python`） |
| 2 有用例但无摘要 → `409`，detail 说明"没有可绑定的摘要"；**两种 detail 必须可区分** | `test_a_version_with_no_test_cases_cannot_supply_its_answer`：detail 含 `digest` 与 `test case`；两处**互为镜像**——情形 ① 的 detail 断言**不含** `digest`/`test case`，情形 ② 的 detail 断言**不含** `no reference answer`，因此"只做了一半的点名"会让测试变红 |
| 3 不存在的版本 → `404`（不被 `409` 吞掉） | `test_an_unknown_version_has_no_reference_answer_to_serve` |
| 4 有答案且有用例 → `200`（`409` 未被扩大成一律拒绝） | `test_reference_answer_is_served_to_services_for_the_declared_language`、`test_the_reference_answer_digest_is_the_one_the_attestation_binds` |

**这两条镜像断言做过变异检查**（本项目对"绿着的守卫"有前车之鉴，见
[Phase 06](phase-06-package-digest-scope.md) §5）：把情形 ① 的 detail 改成同时提到 `digest`
（即"点名只做了一半"的失败形态）后，`test_a_version_without_a_reference_answer_is_409_not_404`
立刻变红：

```text
assert "reference answer" in detail and "python" in detail
>       assert "digest" not in detail
E       AssertionError
```

把文案改回后 7 例全绿、整套 `389 passed`。因此 `CHG-0026` §8.2 的"必须可区分"不是靠措辞声明，
而是**能被变异打破**的断言。

## 4. 真实宿主读数（composition host + 真实 JWT + 真实题库）

`CHG-0023` 的读数是**只读**的（除 §4.2 末尾那个用于观察 `409` 的探针题目）。宿主为 18001 上的
composition host（同一份 `run_host.ps1`、同一 `.env.integration`，B3 由
`uv sync --all-groups --extra modules --reinstall-package autograder-b3` 安装）。令牌：
`t.zhang`（teacher）与 `s.li`（student）各开一个真实会话。

```text
target published version: 01c50a65-33cf-4633-9340-237bbdae4a25 language=bash

service        -> 200  keys=['adapter_kind','content_digest','language',
                           'question_version_id','reference_answer']
                       question_version_id=01c50a65-…  language=bash  adapter_kind=stdin
                       content_digest 长度=64  reference_answer 长度=134（正文不打印：受保护内容）
teacher        -> 403  https://autograder.local/problems/forbidden
student        -> 403  https://autograder.local/problems/forbidden
no token       -> 401  https://autograder.local/problems/unauthorized
unknown token  -> 401  https://autograder.local/problems/unauthorized
service digest == version DTO digest: True                       ← §8.5
unknown version -> 404 https://autograder.local/problems/not-found ← §8.4
versions without an answer in the bank: []                        ← 只读查询（internal 角色）
no-answer version (draft) -> 409 https://autograder.local/problems/reference-answer-missing
                       detail='this version has no reference answer for its declared language
                                      (python); there is nothing to execute'
                       its own DTO -> 200                         ← §8.3：资源在，不是 404
```

**参考解答正文没有进入本报告、日志或响应之外的任何地方**：读数只记录长度、语言、适配器与摘要。

### 4.1 同一个宿主上顺带补做的一次复测（`CHG-0017`）

Phase 08 曾把"composition host 上的复测"记为未执行（当时新宿主因 B2 的 `module.yaml`/描述符不一致而起不来）。
本轮宿主可以启动了，于是用**同一个宿主**补测了那一条：新建一个草稿题目，然后发出那个曾被拒绝的
PATCH（请求体只有四个公开必填字段 + `tags`，**不带** `cases`/`reference_answer`）：

```text
coverage before -> 200 tagged=4 total=105
draft created: question=90001a8f-1871-4f29-ac12-4eb9b8325d15 version=30552812-633a-431c-968f-4eeffbe9a392
content_digest before = ea54c6fceb570857302bd3355912ce22d45dfb81ebcea39da2b7aa642f1fa287
PATCH without cases/reference_answer -> 200
content_digest after  = ea54c6fceb570857302bd3355912ce22d45dfb81ebcea39da2b7aa642f1fa287
protected content untouched (digest unchanged): True
same version row edited in place: True
coverage after  -> 200 tagged=5 total=106      tagged delta = 1
```

摘要逐字不变即证明**用例与参考答案都还在**（摘要覆盖两者），而版本行就是原来那一行（就地编辑）。
Phase 08 §4.1/§5 已据此更正。

### 4.2 对集成库的副作用（如实）

- 本轮为观察 `409` 新建了一个草稿题目："CHG-0023 probe: draft with no reference answer"
  （**没有**参考解答、1 个用例）。
- 为 §4.1 的复测新建了一个草稿题目："CHG-0017 probe: title after the correction"
  （带标签 `probe-chg0017`、2 个用例、1 份参考解答）。
- 两者都是 `draft`（学生不可见），题目 `status` 不影响已发布题库；契约没有 DELETE 入口，因此它们会留在库里。
  MAIN 已接受不清理，并把它们登记进 `phase-02`（它们让覆盖度从空态变为可演示：多个标签、计数不等、且有重叠）；
  同时提示了一条通用规则：**能用既有题目验证的就不要新建探针草稿**（无 DELETE 入口 ⇒ 永久留存）。
- 其余读数全部是 GET / 只读 SQL；此前的探针题目（`81e746e7` 的 `unix` 标签与草稿 v2）未再改动。
- 我启动的 18001 宿主已关闭；MAIN 原本服务的 18000 宿主在复测前后均 `ready=true` 且四模块 `healthy=true`。

## 5. 未执行 / 未验证（如实）

| 未执行 | 原因 / 归属 |
|---|---|
| B2 侧消费该端点（`B3Endpoint` 上的同源 GET、实际执行参考解答并回报存证） | 属 B2；本文件只提供 B3 已落地并可读的证据 |
| `CHG-0019` 的端到端验证（验证→存证→发布整条链） | 依赖 B2 的消费实现；B3 这侧的两端（本端点、`POST /attestations`）都已就绪并有真实读数 |
| ~~§2.3 第 2 条（"有用例但无摘要"也返回 `409`）是否应改为别的形态~~ | **已裁决并关闭**：[`CHG-0026`](../../../../MAIN/docs/contracts/changes/CHG-0026-reference-answer-conflict-semantics.md) 判定契约措辞含糊（同段内收窄/放宽并存），**保留 `409` 覆盖两种情形**、逐条点名，**B3 无代码改动**。本轮只改了文档措辞与既有测试的断言（§3 的 `CHG-0026` §8 表） |
| `CHG-0026` 本身的落地（`b3.yaml` 两处 `409` 描述改为点名两种情形、升至 `0.16.0`） | 属 MAIN，且 `0.15.0` 已冻结；**B3 不因此改动任何代码**，届时只需核验 `contracts.lock` 与 `module.yaml`/`plugin.py` 的版本同步（`test_hygiene.py` 的两条守卫会替我们检查） |
| 该端点在 106 题真实题库上的**规模**特征 | 一次单行读（`(question_version_id, language)` 唯一），无新索引；未做基准，也不需要 |
| 响应正文的**内容**核对（答案是否就是库里那一份） | 只核对了长度与摘要绑定；正文不打印、不复制到报告。B2 侧执行时才需要正文，那是它的验证项 |
| MAIN 的 `scripts/probe_host_assembly.py`（"declared but NOT served" 计数） | **没读到**：本轮读数取自一个真实启动成功的 composition host（18001），但几分钟后重跑该探测器时，装配在更早一步就失败了——`RegistryError: B2: module.yaml and the runtime descriptor disagree -- routes: … module.yaml=[… '/api/v1/reference-verifications' …]`，即 B2 正在实现它自己的 `CHG-0019` 端点、`module.yaml` 领先于已安装副本。这是**并发中的 B2 变更**，不在本包授权范围内（也没有理由去重装 B2）。B3 这条路由**已由真实宿主服务**（§4 的读数即为证明）；待 MAIN 重装 B2 后即可得到该计数 |

## 6. 文档影响

- 本文件（新增）。
- `docs/phases/active/phase-08-omission-preserves.md`：§4.1/§5 更正为"composition host 复测已补做"（读数见本文件 §4.1）。
- `docs/api/contract.md`：新增端点行 + "参考解答交付通道（CHG-0023）"小节（含 §2.1–§2.4 的理由摘要与待决项）。
- `docs/testing/cases.md`：新增 `TC-REF-01/02/03`，并按实测重列例数（389）。
- `docs/requirements/traceability.md`：`RQ-B-3-20` 行补该通道与证据；覆盖自检补一条。
- `docs/requirements/acceptance.md`：新增**验收 3-20-13**。
- `docs/README.md`：状态表新增一行、工具链读数更新、基线指向本阶段、阶段导航加入本文件。
- `docs/collaboration/onboarding.md` / `docs/testing/strategy.md` / `docs/runbooks/local-development.md`：当前分支读数 382 → 389。
- **未改**：`MAIN/contracts/**`、`B3/module.yaml`（路由前缀 `/api/v1/question-versions` 已声明，无需改 routes）、
  `B3/contracts.lock`、`B3/pyproject.toml`、`B3/uv.lock`、`B3/alembic/**`、
  `plugin.py` 的 `descriptor.contract_version`（仍 `0.15.0`）。
