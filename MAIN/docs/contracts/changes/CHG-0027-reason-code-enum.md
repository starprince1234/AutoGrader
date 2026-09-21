# CHG-0027 `reason_code` 的词表：把已闭合的集合写成机器可读的 `enum`

- **级别**：P2 语义（补上遗漏的约束；不改字段含义、不改必填性、不改类型家族）
- **状态**：已生效（契约）；**B2 无代码改动**（`EmailIntakeReason` 已是这 19 项）
- **契约版本**：0.15.0 → **0.16.0**（与 `CHG-0026`/`CHG-0028` 同批）
- **依据**：`CHG-0021` §2.1（该词表的权威来源）、`CHG-0022`（同一族）；B1 在实现隔离区界面时按规程上报

## 1. 问题：词表写在散文表格里，消费者只能手抄

`CHG-0021` §2.1 把 `b2.intake_quarantines.reason_code` 的 **19 项闭合词表**与"哪两项可归属"
写进了 `IntakeQuarantineView.reason_code` 的 `description`（一张 Markdown 表格）。
但那个字段的**类型**仍是 `{type: string, maxLength: 64}`——**没有任何机器可读的约束**。

后果：B1 只能把可归属的那对值**手抄进 TypeScript**：

```ts
const ATTRIBUTABLE_REASON_CODES = ["not_uniquely_matched", "ambiguous_match"];
```

**这正是 MAIN 硬规则 3 禁止的"复制字段表"的形态。** 它之所以成为例外，只是因为契约没给出
机器可读形式——而"没有形式"不是"允许复制"的理由。

## 2. 变更内容：只给**响应字段**加 `enum`

```yaml
reason_code:
  type: string
  enum:
    - not_uniquely_matched
    - ambiguous_match
    - malformed_message
    - missing_message_id
    - no_attachments
    - too_many_attachments
    - message_too_large
    - attachment_name_not_allowed
    - duplicate_attachment_name
    - unsupported_attachment_encoding
    - malformed_attachment
    - attachment_too_large
    - attachment_total_too_large
    - unsupported_attachment_type
    - nested_message_attachment
    - executable_attachment
    - archive_bomb
    - archive_attachment
    - not_plain_text
  description: …（保留 §2.1 的分组表与"只有前两项可归属"的说明）
```

**判据不是"写上更好看"，而是这是一个已经成立的事实**：B2 的 `EmailIntakeReason` 是
`Literal[...]`（19 项），写入路径只产出这 19 个值，`CHG-0021` §2.1 已把它们逐项登记。
与 `CHG-0022` 完全同族：**契约比实现少写，实现已经是对的。**

## 3. 刻意**不**给查询参数 `reason_code` 加 `enum`

`listIntakeQuarantines` 的 `reason_code` **查询参数**仍是 `{type: string, maxLength: 64}`。理由：

- 给它加 `enum` 会把"用一个不存在的码去筛选"从**返回空页**变成 **`422`**——那是**行为变更**，
  不是声明补全，需要 B2 改实现，也可能打断既有调用方。
- 本 CHG 只做"把已成立的事实写出来"，**不夹带行为变更**。

> **记为待决**：查询参数是否应当是闭合枚举（即未知码返回 `422` 而不是空页）。
> 两种都说得通——枚举更能在调用方出错时告诉他，空页更宽容。
> **但这需要 B2 改实现，因此不应偷偷塞进一个"补声明"的 CHG 里。** 需要单独裁决。

## 4. 可归属的那两项：**留在散文里**，并说明为什么不做成扩展

"只有 `not_uniquely_matched` 与 `ambiguous_match` 可归属"是**行为谓词**，不是字段的形状，
因此它留在 `resolveIntakeQuarantine` 的正文与 `reason_code` 的分组表里，**不**做成
`x-attributable-reason-codes` 之类的扩展。理由是**取舍的方向**：

- 一个 `x-` 扩展**看起来是机器验证过的，而实际上没有任何东西会检查它**——
  它是契约文件里的第二份副本，而权威（B2 的 Python 元组 `ATTRIBUTABLE_REASON_CODES`）不在这份契约里，
  所以 `check_shared_vocabularies()` **够不着它**。
- **一份没有任何工具核对的"机器可读"副本，比散文更危险**：散文会让人去读实现，
  而假机器可读会让人信任它。这与本项目对门禁的一贯要求一致——**会乱叫或假装被检查的东西，最终会被忽略。**

**所以 B1 手抄那一对值这件事，本 CHG 明确接受**，并把理由写下来（它是 2 个值、是行为而不是形状、
且契约的权威位置是操作正文）。若将来要让它机器可读，**正确的机制是加一道门禁**：
AST 读取 `B2/src/autograder_b2/intake/quarantine.py` 的 `ATTRIBUTABLE_REASON_CODES` 元组，
与契约里的声明比对——**那才是"两份副本 + 一个核对者"**，而不是只多写一份副本。
本 CHG 不引入该门禁（它属于新增工具，不是补声明）。

## 5. 影响的契约文件

- `openapi/b2.yaml`：`IntakeQuarantineView.reason_code` 增加 `enum`（19 项），描述保留

不改：查询参数、任何 operation、鉴权、响应形状的其他部分。

## 6. 影响的模块

| 模块 | 需要做什么 |
|---|---|
| **B2** | **无代码改动**（`EmailIntakeReason` 已是这 19 项）。可在 `docs/api/contract.md` 把 `reason_code` 的类型表述改为指向契约 |
| **B1** | 类型再生成后 `reason_code` 会变成字面量联合类型；可**删除手抄的常量**并改用生成的类型。是否保留运行时校验由 B1 判断，但**不得**继续手抄字段表（规则 3） |
| **B3 / B4** | 无改动 |
| **MAIN** | 本 CHG、`b2.yaml`、与 `CHG-0025`/`CHG-0026` 同批升至 `0.16.0` |

## 7. 安全与隐私检查

无。枚举是固定的机器可读原因，**不含**任何来件内容、发件人或附件信息。

## 8. 是否破坏兼容

**否。** 只收紧**输出**声明，且收紧到实现**已经保证**的集合——放宽才会破坏，
收紧一个已经在遵守它的实现不改变任何合法载荷。

**但有一处须写明**：严格消费者此后会**拒绝**任何这 19 项之外的值。这正是目的
（一个没被登记的原因码应当**可见地**失败，而不是被静默接受），但它是本 CHG 唯一的真实影响面。

## 9. 验证（实施后执行）

1. 用契约 schema 校验一条真实的 `GET /intake/quarantines` 响应：**必须通过**。
2. **反向**：把 `reason_code` 改成 `"something_else"` 的响应**必须被契约校验器拒绝**。
   （只证明合法值通过，等于没证明枚举存在——`{type: string}` 也会通过。）
3. `resolveIntakeQuarantine` 对 `reason_code = archive_bomb` 的记录仍返回 `422`（行为未变）。
4. 查询参数用一个不存在的码筛选：**行为与改动前逐字相同**（返回空页，**不是** `422`）
   ——这条钉住 §3 的"不夹带行为变更"。

## 10. 摘要

由 `checks.py` 计算。

## 11. 回滚方式

删除 `enum` 并重跑 `scripts/relock_contracts.py`。**回滚后必须把 §1 的"手抄字段表"重新登记回
`phase-02` 与 B1 的 `interfaces.md`**，而不是让它退回成一个没人记得的例外。
