# CHG-0024 `QuestionVersionSummary.published_at` 写不出"尚未发布"

- **级别**：P2 语义（只放宽可空性，不改字段含义、不改必填性）
- **状态**：已生效（契约）；实现侧**无改动**（实现已经是对的）
- **契约版本**：0.14.0 → **0.15.0**（**修订**该批：`0.15.0` 尚未被任何模块实现消费）
- **依据**：`CHG-0022` 的验证过程（用一个独立的契约校验器校验**真实**响应）

## 1. 问题：契约声明的类型排除了实现合法产出的值

`CHG-0022` 的验证用契约校验器校验线上真实响应，第一步就炸在一处**与标签无关**的地方：

```text
QuestionPublic.versions[0].published_at: None is not of type 'string'
```

`b3.yaml` 的 `QuestionVersionSummary`（`QuestionPublic.versions[]` 的元素）：

```yaml
published_at: {$ref: './common.yaml#/components/schemas/Timestamp'}
```

`Timestamp` 是 `type: string, format: date-time`——**非空**。而一个**草稿**版本本来就没有发布时间。
实测：

```text
status=draft      rows=    7   published_at_not_null=0     ← 7 个草稿全部为 NULL
status=published  rows=  103   published_at_not_null=103
```

**同一处缺陷有两份声明**：`QuestionVersionSummary.published_at` 与 `QuestionVersionPublic.published_at`
**都是**非空 `Timestamp`，而 `QuestionVersionPublic.status` 的枚举**含 `draft`**——所以两者都能表示草稿，
两处都要改。（发现过程是先撞到前者；随后 `GET /api/v1/question-versions/{id}` 一条草稿也带 `published_at: null`，
确认后者同病。）

**7 行今天就在库里**（而且我的验证又新增了 3 个草稿）。也就是说：任何按契约校验
`GET /api/v1/questions/{id}` 的消费者，会在**每一条带草稿的题目**上失败——而带草稿是出题流程的常态。

`published_at` **不在** `required` 里，所以它的**存在**是可选的；但一旦出现，契约要求它是字符串。
实现选择"始终出现、草稿时为 `null`"，这与契约相冲突，且**错的是契约**：`null` 是这件事的正确表达，
用"字段缺席"表达"尚未发布"会让"字段缺席"同时意味着另外两种事（旧版响应、字段被省略）。

## 2. 变更内容

```yaml
published_at:
  anyOf: [{$ref: './common.yaml#/components/schemas/Timestamp'}, {type: "null"}]
  description: "When this version was published. Null for a draft: a draft has no publication time, and omitting the field would make 'absent' mean both 'not published yet' and 'this response predates the field'."
```

**只改可空性。** `required` 一字不动，字段含义不动，`status` 枚举不动。

## 3. 同族关系（为什么它值得单独记一笔）

这是本项目**第四次**遇到同一形状：**契约写不出实现合法产出的值**。

| CHG | 形状 |
|---|---|
| `CHG-0016` | 声明 `QuestionVersionPublic`，而它无法表达实现返回的 `title`/`tags` |
| `CHG-0021` §2.4 | `detail.maxLength: 500`，而数据库 CHECK 与实现都是 1000 |
| `CHG-0022` | 两个**输出**字段只写 `{type: string}`，什么都没说 |
| **`CHG-0024`** | `published_at` 非空，而草稿合法地为 `null` |

**四次的共同点**：契约是照着"理想中的形状"写的，而不是照着"系统实际会产出什么"写的。
**判据永远是后者**——一个 `maxLength` / `type` / `pattern` 是对**产出**的断言。

## 4. 为什么这次才发现

前三次都是**读代码/读表**发现的。这一次是**用契约自己的校验器校验一条真实响应**发现的——
那一步本该更早做。`CHG-0022` 的验证要求"用契约 schema 校验真实端点的一条非空响应"，
而那条断言顺手把整个 `QuestionPublic` 也带过了校验器，于是暴露了它。

> **可复用的做法**：把"用契约校验真实响应"变成常规动作，而不是只在某个 CHG 的验证项里做一次。
> 它是唯一能发现"契约与实现形状不一致"的通用手段，而且成本极低——**它抓到的是前三轮静态审查都没抓到的东西**。

## 5. 安全与隐私检查

无新字段、无放宽访问。`null` 不含任何信息。

## 6. 资源影响

无。

## 7. 需求一致性

不新增 `RQ-*`。是 `RQ-B-3-20`（按课程维护题库）的可用性补全：出题流程必然产生草稿，
而契约让"有草稿的题目"无法被任何严格消费者读取。

## 8. 是否破坏兼容

**否。** 只放宽可空性，且放宽的方向是"接受今天已经在返回的东西"。收紧才会破坏。

## 9. 验证

1. **线上真实响应必须通过契约校验**：`GET /api/v1/questions/{id}` 对一条**带草稿**的题目 →
   `QuestionPublic` 校验通过。这是一条**反向可证**的断言：修之前它必然失败（已实测
   `versions/0/published_at: None is not of type 'string'`）。
2. `published_at` 为**非法字符串**（如 `"not-a-date"`）仍须被拒——放宽可空性不得顺手放宽格式。
3. `CHG-0022` 的五条负向断言保持通过（本条不得影响它们）。

## 10. 摘要

由 `checks.py` 计算（不在此复制字面值，避免漂移）。

## 11. 回滚方式

恢复为 `{$ref: './common.yaml#/components/schemas/Timestamp'}` 并重跑 `scripts/relock_contracts.py`。
**回滚后必须同时把 §1 的读数登记回 `phase-02`**，而不是让契约重新变成"任何带草稿的题目都无法被
严格消费者读取"的状态。
