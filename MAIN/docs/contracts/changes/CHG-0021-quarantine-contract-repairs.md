# CHG-0021 隔离区的契约修复与两处沉默：两处如实承认、两处契约写错、一处不可判定的要求、一处只能宣布的平局

- **级别**：P2 语义（两处放宽/更正 + 两处如实承认 + 一处改写为可判定形式 + 一处把沉默改为正面声明）
- **状态**：已生效（契约）；实现侧**仅 B1** 需改用 `?resolved=false`（B2/B3/B4 无代码改动）
- **契约版本**：0.14.0 → **0.15.0**
- **依据**：`CHG-0014`（隔离区复核台）、`phase-02` §6 与 §3.14、B2 的 `ATTRIBUTABLE_REASON_CODES`

## 1. 问题

`CHG-0014` 为 `b2.intake_quarantines` 补上了读出口与手动归属。B2 在实现它时按规程停下上报，
没有自行发明语义。本 CHG 处理其中**六处**：两处是契约写了数据模型兑现不了的话，两处是契约
写了与实现相反的**事实**，一处是契约写了一个**不可判定**的要求，一处是契约对同一 operation
声明了两个可能同时成立的状态码却不说谁先。

三处（§2.1/§2.2/§2.3）来自 B2 的正式上报；两处（§2.4/§2.5）由 MAIN 在施工期核对契约与实现时发现；
§2.6 由 B3 实施 `CHG-0020` 时上报、经 MAIN 用真实端口复测确认。**没有一处是"顺手改一下"。**

## 2. 变更内容

### 2.1 `resolveIntakeQuarantine`：把"重跑相同校验"换成可判定的原因码集合

契约现在的正文是：

> **The manual path re-runs the same validation as the automatic one.** Attribution supplies the
> *identity* automatic matching failed to derive; it does not skip the envelope, digest or scope checks.

这句话**不可判定**。`b2.intake_quarantines` 只存 `mailbox`/`uidvalidity`/`uid`/`message_id`/
`reason_code`/`detail` —— **不存报文与附件字节、不存任何摘要**（这是"平台不成为第二个邮件仓库"
的直接后果），因此归属时**没有任何可重算的摘要**。要真做只有一条路：依赖 IMAP 重取该 UID 的报文
再走完整校验；但契约给该 operation 声明的响应集合是 `401/403/404/409/422`、**没有 `503`**，
这反证契约没打算让它依赖外部邮箱。实现必须猜，而猜出来的任何东西都无法被验证。

**改为**：

> 归属**只**补充自动匹配未能推出的身份。它重跑的是当前数据下**能**重跑的判定（角色与课程作用域），
> 外加一条内容类闸门：**只有 `reason_code ∈ {not_uniquely_matched, ambiguous_match}` 的记录可归属；
> 其余原因码一律 `422`。**

理由：命名一个学生修不好"附件被体积上限拒绝"这类问题，允许归属就是给校验开后门。

同时把 `intake_quarantines.reason_code` 的**完整词表**写进契约。当前契约只说
`"Machine-readable cause, e.g. that no assignment identifier could be resolved."` —— 一个
`e.g.` 的开放集合。词表来源是 B2 的 `EmailIntakeReason`（19 个），本 CHG 只做登记，不新造词：

| 类 | 原因码 | 可归属 |
|---|---|---|
| **身份未推出** | `not_uniquely_matched`、`ambiguous_match` | **是** |
| 报文结构 | `malformed_message`、`missing_message_id`、`no_attachments`、`too_many_attachments`、`message_too_large` | 否 |
| 附件策略 | `attachment_name_not_allowed`、`duplicate_attachment_name`、`unsupported_attachment_encoding`、`malformed_attachment`、`attachment_too_large`、`attachment_total_too_large`、`unsupported_attachment_type`、`nested_message_attachment` | 否 |
| 内容闸门 | `executable_attachment`、`archive_bomb`、`archive_attachment`、`not_plain_text` | 否 |

> `checkpoint_exists` **不在**此表内：它是 `email_checkpoints` 的结果，不是
> `intake_quarantines.reason_code` 的取值。两者在 B2 里是两个不同的类型，不得合并。

### 2.2 如实承认"按课程作用域逐行过滤"不可能（当前契约暗示可以）

`b2.intake_quarantines` **没有 `course_id`**，而匹配失败时课程本身就不存在（"哪门课的提交"
正是匹配要推出来的东西）。因此列表端点**无法按行判定课程作用域**。

契约正文改为如实描述：列表端点要求令牌**至少携带一个课程作用域**；**按行过滤不实现**，
并把这一层如实登记为缺口。

### 2.3 如实承认"归属后重建提交"不存在（当前契约暗示可以）

`resolve` 只拿到 `student_id`，而构造一个提交信封还需要 `course_id`/`section_id`/
`question_version_id` —— 这三个正是自动匹配规则给出的，匹配失败时根本没有。

契约正文改为：**归属只记录、不建提交、不评测。** 审计链呈现的是"人做了一次决定，随后走普通机器流程"。

> 闭环（"用已归属邮件重建提交"）是**另一个设计**，不是这个端点的补丁：它需要一条独立的路径，
> 且必须自己回答"题目版本从哪来"。本 CHG **不**引入它。

### 2.4 `IntakeQuarantineView.detail` 的上限**写错了**：`500` → `1000`

| 位置 | 值 |
|---|---|
| `openapi/b2.yaml` `IntakeQuarantineView.detail` | `maxLength: 500` |
| B2 迁移 `20260915_0002` 的 `ck_intake_quarantines_detail` | `char_length(detail) BETWEEN 1 AND 1000` |
| B2 `intake/imap.py` `MAX_QUARANTINE_DETAIL` | `1000`（`_bounded_detail()` 以 `text[:1000]` 截断） |

**判定：契约错。** 两处独立证据一致——数据库约束与写入侧的截断都是 1000。因此服务器**会**合法地
产出 501–1000 字符的 `detail`，而它会被自己声明的响应 schema 判为不合法。一个 `maxLength` 是对
"服务器会产出什么"的断言，不是对"服务器应该产出多短"的偏好；写出一个服务器自己都会违反的上限，
只会让运行期 schema 校验出现无法解释的失败。

改为 `maxLength: 1000`，描述里写明它与数据库约束同源。

### 2.5 `resolved` 查询参数的省略语义**写错了**：散文与机器语义相反

契约的 `resolved` 参数：

```yaml
- {name: resolved, in: query, required: false, schema: {type: boolean},
   description: "Filter to unresolved (the default view a teacher wants) or resolved."}
```

它**没有** `default`（同一份文件里的 `Limit`/`view` 都显式写了默认值），而描述里那句
"the default view a teacher wants" 读起来像"省略 = 只看未归属"。B2 按"无 `default` ⇒ 不过滤"
实现，并在代码注释里把这一处作为契约含糊项上报。**两种读法都说得通，这正是问题。**

**判定：散文错，机器语义（不过滤）对。** 判据不是偏好，而是**表达力**：参数类型是 `boolean`，
只有 `true`/`false` 两个取值，**无法表达"两者都要"**。若规定省略等于 `false`（只看未归属），
则"列出全部"这件事将**没有任何请求形式可以表达**，该能力被永久删除。反之，省略 = 不过滤时，
"只看未归属"仍可由 `?resolved=false` 完整表达。因此唯一不损失功能的读法是"省略 = 不过滤"。

契约改为显式声明这一点，并删掉那句会引起误读的散文。

### 2.6 `401` 与 `422` 同时成立时的先后：**声明为不受保证**，并写明两条稳定事实

**问题（由 B3 在实施覆盖度端点时按规程上报，MAIN 用真实端口复测确认）**：一个请求**既**未认证
**又**请求不合法时，契约对同一 operation 声明了 `401` 与 `422`，却从不说谁先。而两个模块给出**相反**
的答案——实测（无令牌，`127.0.0.1:18000`）：

| 模块 | 请求 | 读数 | 规则 |
|---|---|---|---|
| B2 | `GET /submissions/not-a-uuid` | `401` | 鉴权优先 |
| B2 | `GET /evaluation-runs/not-a-uuid` | `401` | 鉴权优先 |
| B2 | `GET /evaluation-runs/quality-summary?question_version_id=abc` | `401` | 鉴权优先 |
| B3 | `GET /questions/not-a-uuid` | `422` | 请求校验优先 |
| B3 | `GET /question-versions/not-a-uuid` | `422` | 请求校验优先 |
| B3 | `POST /question-versions/{x}/publish` | `422` | 请求校验优先 |
| B3 | `POST /questions`（空体） | `422` | 请求校验优先 |
| B4 | `POST /courses`（空体） | `401` | 鉴权优先 |
| B4 | `GET /users/{id}`（该路径无 GET） | `405` | **路由层先于两者** |

**根因不是实现疏漏，而是两处都自洽的刻意选择**：B2 把路径/查询参数声明为 `str` 并在服务层自行
解析（`evaluation_routes.py:100-108` 的 docstring 写明了理由——"**不加** FastAPI 的类型校验：
非法取值的 422 由本模块统一产出"），因此读令牌的函数体在参数校验之前运行；B3 用 FastAPI 的声明式
校验，再由 `api/app.py:33` 的 `RequestValidationError` 处理器产出 Problem Details。**两者产出的
错误体形状一致**，差异只在**校验发生在哪一层**。

**裁决：不强行统一，改为把"平局顺序不受保证"写进契约正文。**

理由不是"改起来麻烦"，而是**收敛的代价是一处真实的设计回退**：

- 让 B2 改用 FastAPI 的声明式校验，会使 422 由**两个**产出者生成（FastAPI 的校验器 + B2 的服务层），
  而 B2 现在刻意只有**一个** Problem Details 产出者。这会丢掉"错误体形状由一处保证"这一性质。
- 让 B3 把令牌读取提到路由级依赖，要改约 17 条路由，而换来的只是"未认证且请求不合法时先给 401"。
  这个差别对客户端没有可观察的后果：B1 的拦截器对 `401` 走刷新重试、对 `422` 走字段错误提示，
  **两条路都正确**，只是顺序不同。

**同时写明两条确实稳定的事实**（它们不是平局，消费者可以依赖）：

1. **路由存在性与方法匹配（`404` / `405`）先于 `401` 与 `422`。** 它们是路由层判定，不依赖请求内容。
   实测 `GET /users/{id}` → `405` 而非 `401`。这不构成新增泄露面：哪些方法存在于哪些路径，
   本就写在公开的 OpenAPI 契约里。
2. **`403` / `404` / `409` 永远不与 `401` 竞争**：它们是**已认证**请求的结论。`governance.md` §2.2
   的硬规则 1（`401`/`403`/`404` 不得混用）已覆盖这条，此处只是把它与平局规则区分开。

**契约正文写法**：在 `common.yaml` 的可复用响应组件附近（每个 operation 都 `$ref` 它们）加一段
说明，声明 (a) 各状态码的**语义**是硬约束；(b) 当多个状态码同时适用时，**其相对顺序不受保证**，
消费者必须各自处理；(c) 上述两条稳定事实。

> 这一条与 §2.5 是同一族（`CHG-0017`、`§6.2` 第 1 项也是）：**契约沉默被当成契约完备**。
> 区别在于 §2.5 的沉默必须被**填上**（省略语义有唯一正确解），而这里的沉默只能被**宣布**
> （因为强行定序会损失一处真实的设计性质）。把这两种处置分开写，是因为把它们混为一谈会让人以为
> "所有沉默都该填一个值"。

### 2.7 **本 CHG 的文本曾经"记为已落地、实际没写"——由 B1 发现，已补**

**必须先读这一节，再看 §2.1–§2.5。**

本 CHG 的四处 `b2.yaml` 文本在 `0.15.0` 被记为已生效之后，**实际上一个字都没有写进契约文件**：

| 位置 | 文件里的真实内容（错误状态） | 本 CHG 要求 |
|---|---|---|
| `listIntakeQuarantines` 的 `resolved` 描述 | `"Filter to unresolved (the default view a teacher wants) or resolved."`（§2.5 判定为**写错**的那句散文） | 写明"省略 = 不过滤"及其表达力理由 |
| `resolveIntakeQuarantine` 正文 | `"The manual path re-runs the same validation as the automatic one."`（§2.1 判定为**不可判定**） | 可归属原因码集合 + 两处如实承认 |
| `IntakeQuarantineView.reason_code` | `"Machine-readable cause, e.g. that no assignment identifier could be resolved."`（开放集合） | 19 项闭合词表 + 可归属标记 |
| `IntakeQuarantineView.detail` | `maxLength: 500` | `1000` |

与此同时，`contracts/CHANGELOG.md` 的 0.15.0 段把这四处**列进了"变更文件"**，本 CHG 的状态也是
`已生效（契约）`。**也就是说：变更记录声称了一处不存在的变更。**

**发现方式**：B1 实现隔离区界面时去 `b2.yaml` 里找 `CHG-0021` 的文本，**一处都没找到**，于是按规程上报
（它只按 CHG 正文实现行为，未改 MAIN 的文件）。

**根因**：0.15.0 那一批同时包含 `CHG-0019`/`0021`/`0022`/`0023`。我在同一批里改了
`common.yaml`（本 CHG §2.6）、`b3.yaml`（`CHG-0022` 与 `CHG-0023`）、`b2.yaml`（`CHG-0019` 的端点），
**却从未回头把本 CHG §2.1–§2.5 写进 `b2.yaml`**。而 CHANGELOG 的"变更文件"清单是照
**本 CHG 的计划**（§2.1–§2.5 描述的正是"要改什么"）写的，不是照**我实际改了哪些文件**写的。
**这两件事在写清单的那一刻我没有分开——而记录一条没做过的变更，比写下一条未核实的断言更严重。**

**处置**：已把 §2.1–§2.5 的四处文本补齐到 `b2.yaml`，重算锁（摘要由 `relock_contracts.py` 计算，
`29271ee2480c…`）。版本仍为 `0.15.0`（尚未被任何模块实现消费，因此仍是"一次变更"而非两次）。

**预防措施（可机械执行）**：一批变更落地后，对**每一条被声称的文本变更**取一个**独特字符串**去
`grep` 目标文件，全部存在才算落地。补完后的实测：

```text
maxLength: 1000                       PRESENT   ← §2.4
Omitting it means no filter at all     PRESENT   ← §2.5
Every other reason code is refused     PRESENT   ← §2.1
| Group | Codes | Attributable |       PRESENT   ← §2.1 词表
cannot** filter rows by course         PRESENT   ← §2.2
does not build a submission            PRESENT   ← §2.3
```

**"变更文件"清单必须由这种核对生成，不能由 CHG 的计划生成。** 详见 `phase-02 §3.19`。

## 3. 影响的契约文件

- `openapi/b2.yaml`：`listIntakeQuarantines`（`resolved` 参数描述）、
  `resolveIntakeQuarantine`（正文、`422` 语义）、`IntakeQuarantineView`（`reason_code` 词表、`detail.maxLength`）
- `openapi/common.yaml`：在可复用响应组件附近加"状态码语义是硬约束 / 多个同时适用时顺序不受保证 /
  路由层 `404`·`405` 先于两者"的说明（§2.6）。**选择 `common.yaml` 是因为每个 operation 都
  `$ref` 它的响应组件**，因此写一处即可覆盖全部四份契约文件，而不必在 54 个 operation 上各写一遍。

未改任何 schema 的**形状**，除 `detail.maxLength` 放宽之外。§2.6 **完全不改形状**——它只把
"沉默"换成"正面声明"。因此本 CHG 不触发 `check_shared_vocabularies()`：它没有新增重复词表
（`failure_category` 的重复属 `CHG-0019`）。

## 4. 影响的模块与具体改动

| 模块 | 需要做什么 |
|---|---|
| **B2** | **无代码改动。** 实现已按上述语义在跑（`ATTRIBUTABLE_REASON_CODES`、`MAX_QUARANTINE_DETAIL=1000`、`parse_resolved_filter` 返回 `None`）。须更新 `docs/api/contract.md` 中 `reason_code` 的描述，并把代码注释里"这一点作为契约含糊项已上报"改为指向本 CHG。**§2.6 不需要 B2 做任何事**——它保留现有的鉴权优先行为 |
| **B1** | 隔离区复核台需按 `?resolved=false` 表达"只看未归属"，**不得**依赖省略（省略现在是"全部"）。不可归属的记录应据此**不显示归属按钮**，而不是让教师点了才拿到 `422` |
| **B3** | **无代码改动**（这是 §2.6 裁决的直接后果：把令牌读取提到路由级依赖的代价大于收益）。须在 `docs/api/contract.md` 的错误约定一节把"本模块为请求校验优先、MAIN 已裁决顺序不受保证"改为指向本 CHG 的 §2.6，而不是留下"待 MAIN 裁决"的悬置 |
| **B4** | 无改动（其鉴权优先行为与 B2 同类，§2.6 保留） |
| **MAIN** | 本 CHG、`b2.yaml` 与 `common.yaml` 文本、五份锁与派生声明同步到 `0.15.0` |

## 5. 安全与隐私检查

- 新增内容**全部是枚举、计数与描述**。`reason_code` 词表是固定的机器可读原因，**不含**任何来件内容。
- `detail` 上限从 500 放宽到 1000：`detail` 在**写入时已被脱敏**（不含发件人地址、不含正文与附件原文），
  放宽上限不改变脱敏口径，只让契约与既有的数据库约束和截断值一致。
- 归属路径的**收紧**（内容类拒绝一律 `422`）是**缩小**攻击面：它明确关掉了"用人工归属绕过自动信封校验"
  这条路。契约正文里"does not skip the envelope checks"那句话本身就要求如此。

## 6. 资源影响

无。无新表、无新索引、无新外部调用。

## 7. 需求一致性

不新增 `RQ-*`。它是 `RQ-B-2-*`（隔离区与来件治理）的**可用性补全**：不在契约里写明可归属集合，
教师界面就无法判断哪些记录值得归属，而只能靠试错。

## 8. 是否破坏兼容

- §2.4（放宽上限）、§2.2/§2.3（删除兑现不了的暗示）、§2.5（更正散文）：**不破坏**。
- §2.1（收紧可归属集合）：该端点在 `CHG-0014` 中**首次**实现，实现采用的即时本 CHG 的语义。
  因此**不存在"已在依赖旧语义"的消费者**；没有兼容层，也没有双读双写。
- **MINOR** 升到 `0.15.0`。

## 9. 示例载荷变更

`GET /api/v1/intake/quarantines?limit=5` 的响应字段**不变**；变化只在 `detail` 可达到的长度（500 → 1000）。

`POST /api/v1/intake/quarantines/{id}`（归属）在 `reason_code = archive_bomb` 时：

```text
前置：422 + {type: "about:blank", title: "Unprocessable Entity", status: 422}
后置：422 + {..., status: 422,
        detail: "this quarantined message was rejected by the automatic path
                 (reason_code=archive_bomb); attribution supplies an identity, it does not
                 repair a rejected envelope"}
```

**状态码不变**（本来就是 `422`）——本 CHG 把这条行为写进契约，而不是改变它。

## 10. 验证（实施后执行，未完成前不得声称已通过）

1. `checks.py` 三条输出全绿；五份 `contracts.lock` 与 `module.yaml` 的 `contract_version` 一致为 `0.15.0`。
2. 用契约 schema 校验一条 `detail` 长度为 800 的隔离记录：**必须通过**（放宽前应失败）。
   这条同时是"契约确实被实现违反过"的证据。
3. `GET /intake/quarantines`（省略 `resolved`）返回的记录里**同时存在**已归属与未归属；
   `?resolved=false` 与 `?resolved=true` 各自只返回一类；三者的并集关系可核对。
4. 对 `reason_code = archive_bomb` 的记录归属：`422`，且响应 `detail` 含 `reason_code=archive_bomb`。
5. 对 `reason_code = not_uniquely_matched` 的记录归属：`200`，`resolved=true`。
6. `reason_code` 词表与 B2 的 `EmailIntakeReason` 逐字一致（19 项），且 `checkpoint_exists` **不**出现。
7. §2.6 的声明落地后复测同一批请求：**读数值不变**（B2 仍 `401`、B3 仍 `422`、B4 `405`）。
   这一条是**故意**的"不变"断言——本 CHG 声明的是"顺序不受保证"，不是"改成某个顺序"；
   若某次改动把它们统一了，那不是本 CHG 的验收通过，而是契约正文需要改写。**因此该断言必须
   连同"若统一则须改契约"一起写进测试注释**，否则下一个人会把它读成"顺序被冻结了"。

## 11. 摘要

由 `checks.py` 计算（不在此复制字面值，避免漂移）。

## 12. 回滚方式

把 `b2.yaml` 的四处文本改回原状，重新运行 `scripts/relock_contracts.py`，版本回到 `0.14.0`。
**回滚后必须同时把 §1 的五处缺口重新登记回 `phase-02`**，而不是让契约恢复成"看起来完备、
实现只能猜"的状态。
