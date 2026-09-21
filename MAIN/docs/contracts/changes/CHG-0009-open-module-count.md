# CHG-0009 模块数量开放：`manifest.schema.json` 的模块名 pattern

- **级别**：P2（语义，放宽约束）
- **状态**：已生效（2026-09-18 判定）
- **契约版本**：0.3.0 → **0.4.0**
- **依据**：[ADR-0013](../../adr/ADR-0013-open-module-count.md)

## 1. 问题

PRD §8.6 把项目的头条目标写成一条**可测**判据：

> 新增一个遵守规范的模块，**无需修改 MAIN 代码**即可装载。

实测（9 个合成场景跑真实 `_load_modules()`）后，装载期确实是结构性强制的，但"新增一个模块"这条路
在契约层就被封死：

```json
"module": {"type": "string", "pattern": "^B[1-4]$"}
```

于是第五个模块**无法通过清单校验**。同一个 pattern 还被复制到
`B4/src/autograder_b4/registry.py` 与 `api/modules.py`（三处各写一份），
而 `MAIN/pyproject.toml` 又把三个业务模块声明为硬依赖（`uv.lock` 里是
`source = { directory = "../B2" }`）——**没有那三个目录，MAIN 连自己的 venv 都装不出来**。

另一半同样重要：治理门禁里模块列表是字面量 `("B1", "B2", "B3", "B4")`（3 处），
而契约/路由校验用的是 `glob("B*/module.yaml")`（通用）。于是新模块会**被装载却逃过治理检查**。
"静默不受治理"比"响亮被拒"危险得多。

## 2. 变更内容

| 位置 | 之前 | 之后 |
|---|---|---|
| `manifest.schema.json` → `module.pattern` | `^B[1-4]$` | **`^B[0-9]{1,3}$`** |
| `manifest.schema.json` → `database.schema` | `enum: ["none","b2","b3","b4"]` | **`pattern: ^(none\|b[0-9]{1,3})$`** |
| `b4.yaml` → `GET /api/v1/modules/{module}/health` 的 path 参数 | `^B[1-4]$` | **`^B[0-9]{1,3}$`** |
| `b4.yaml` → `RegisteredModule.module` | `^B[1-4]$` | **`^B[0-9]{1,3}$`** |

保持 `B<n>` 这一课程既有约定（它同时是 `glob("B*/module.yaml")` 的发现规则），只是不再把 `n`
钉死在 1–4。`$comment` 里写明了这条约束与 ADR-0013 的关系，避免下一次有人"顺手"改回去。

**pattern 只在这一个位置定义。** registry 侧不得再各写一份（ADR-0003：设计期权威归 MAIN）。

### 2.1 补记：首次落地漏改了后两行

第一轮只改了 `manifest.schema.json`，**`b4.yaml` 里的两处漏掉了**。后果很具体：第五个模块能通过
`module.yaml` 校验，**却在注册表响应里违反 OpenAPI 声明**——"能装载、但一读注册表就不合规"。
是 B4 在实现注册表时发现的（模块不改契约，按规程上报）。

> 这个漏改本身就是本 ADR 论点的证据：同一个 pattern 被复制到三处，于是第一轮只修好了一处。
> registry 侧的实现已改为按 pattern 断言，不再保留第三份口径。

## 3. 是否破坏兼容

**否，纯放宽。** 现有四个清单 `B1`–`B4` 仍然满足新 pattern；没有任何已发布形状被改变。
按 MINOR 升到 0.4.0。

> 注意区分：本变更**只**放宽模块命名。`module.yaml` 的其余约束（路由唯一、schema 归属唯一、
> 契约摘要一致、`entry_point` 形状、能力可满足）一条都没有放松。

## 4. 受影响模块与需要做的改动

| 模块 | 改动 |
|---|---|
| **MAIN** | 本变更；`checks.py` 的 3 处模块列表字面量改为 `discovered_modules()`；`pyproject.toml` 把业务模块从核心依赖移入可选依赖组（见 ADR-0013 决策 4）；host 装载期校验 `module.yaml` 并对同名 entry point 响亮失败（ADR-0013 决策 5/6） |
| **B4** | `registry.py` 与 `api/modules.py` 的 `MODULE_PATTERN` 从 `^B[1-4]$` 改为与契约一致（不得自造第二份口径） |
| **B1/B2/B3** | 无代码改动；四份 `contracts.lock` 与四份 `module.yaml` 的 `contract_version` 同步到 0.4.0 |

## 5. 验证

```powershell
cd MAIN
.\.venv\Scripts\python.exe -m autograder_host.checks
```

本变更特有的验证（必须在合成模块落地时一并完成，见 ADR-0013）：

1. 契约摘要与四份锁、四份 manifest 版本一致（门禁覆盖）；
2. `discovered_modules()` 返回 `['B1','B2','B3','B4']`，`module_schema_owners()` 返回
   `['B2','B3','B4']` —— 即发现式列表与原先的字面量等价，**没有把已有模块漏掉**；
3. 新增一个合成模块后，它**同时**被装载与被治理（A1 工作项交付时验证）。

第 3 条在本次变更时**尚未完成**（合成模块属独立工作项），因此不得声称已通过。

## 6. 回滚方式

回退本批次 → 重新运行 `scripts/relock_contracts.py` → 四份锁与四份 manifest 回到 0.3.0。
回滚会同时废除 ADR-0013，需要把该 ADR 状态改为"拒绝"并写明原因，而不是静默删除。
