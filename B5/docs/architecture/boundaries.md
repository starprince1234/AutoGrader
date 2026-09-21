# 边界：信任、数据与安全

## 1. 信任边界

| 来源 | 信任程度 | 依据 |
|---|---|---|
| `MAIN/contracts/openapi/*.yaml` | **信任**（视图 1 的权威） | 契约由 MAIN 拥有（ADR-0003）；摘要由 [contracts.lock](../../contracts.lock) 锁定 |
| 宿主传入的 `ModuleDescriptor` | **信任**（视图 3 的权威） | 宿主在装载时已用 `isinstance(..., ModuleDescriptor)` 强校验，且 B4 会在启动期与 `module.yaml` 三方比对 |
| `Authorization` 头 | **完全不信任** | 外部输入；验签失败即 `401`，不解析任何未验证声明 |
| `app.state.modules` 之外的应用状态 | **不访问** | 模块只读 `app.state.modules` 与路由表，不读其他模块的私有状态 |
| B4 的 JWKS 响应 | **信任其真实性，校验其形状** | 只接受 `kty=OKP`、`crv=Ed25519`、带 `kid` 的键；无法理解的键被跳过，全部不可用则 fail closed |
| `traceparent` 头 | 不信任，仅原样写入拒绝日志 | 相关性标识，不参与任何判定 |

**我不信任任何外部输入**：本模块没有请求体、没有路径参数、没有查询参数——唯一的输入是
`Authorization` 头。这是"只读 + 无参数"带来的最小攻击面，不是省略了校验。

## 2. 数据边界

| 问题 | 答案 |
|---|---|
| 我读写哪个 schema | **没有**。`module.yaml` 的 `database.schema: none`；没有 SQLAlchemy、没有驱动、没有迁移 |
| 我读哪些文件 | 只读 `MAIN/contracts/openapi/*.yaml`（由 `CONTRACTS_DIR` 指定） |
| 我写哪些文件 | **不写任何文件** |
| 跨 schema 访问 | 不存在。`module_schema_owners()` 不含 B5，由 MAIN 门禁强制 |
| 跨模块 import | 生产代码中不存在 `autograder_b2/b3/b4/host` 的导入（ADR-0011） |
| 数据保留 | 无保留：报告按需生成，JWKS 键集缓存在内存且按 URL 分键，进程结束即消失 |

## 3. 安全边界

- **密钥来源**：只使用 B4 **公钥**（JWKS）。B5 从不接触私钥、对称密钥、刷新凭据或 cookie。
- **日志脱敏**：`401` 时记录 `reason`（固定的短句）、`traceparent`、`path`——**不记录**令牌、
  `Authorization` 头原文、cookie、完整 PII。日志中没有响应体内容。
- **导出限制**：报告只含**端点名称**与计数（模块名、路径前缀、契约文件名、路径）。不含载荷、
  代码、凭据、题目内容——这也是契约允许"任何已认证主体可读"的理由。
- **越权面**：端点不区分角色。契约明确"任何认证主体可读"，因此实现里**没有**授权分支：
  一个不执行的授权判断比没有判断更危险（它会被当成安全边界）。
- **fail closed**：验签不可完成（键集不可达、无可用键）时返回 `401`，绝不返回报告。

## 4. 依赖边界（为什么这三个依赖是必需的）

| 依赖 | 用途 | 为什么不能去掉 |
|---|---|---|
| `pyyaml` | 解析 `MAIN/contracts/openapi/*.yaml` | 视图 1 必须来自契约**文件**；用正则解析结构化契约是被禁止的脆弱做法，而换一种"从运行中应用反推契约"的做法会让比对变成自己跟自己比。PyYAML 也是 MAIN 门禁读同一批文件所用的解析器，两侧读法必须一致 |
| `pyjwt` | EdDSA 验签与注册声明校验 | 契约声明 `security: bearerAuth` 与 `401`；B2 用同一库做同一件事。手写 JWT 验签等于第三次实现安全关键代码（B3 记录过 RS256/EdDSA 混淆导致的真实事故） |
| `cryptography` | `pyjwt` 的 Ed25519 后端 | 同上；B2/B3/B4 均已在生产依赖中使用 |

**没有**数据库驱动、消息客户端、HTTP 客户端框架（JWKS 用标准库 `urllib.request`，与 B3 相同）。
`pyyaml` / `pyjwt` / `cryptography` 是本模块对项目依赖图**新增**的三项，已在
[../phases/active/phase-01-platform-conformance.md](../phases/active/phase-01-platform-conformance.md)
的"风险与遗留项"中如实记录为与任务约束的偏离及理由。

## 5. 边界自检（可核验动作）

```powershell
# 1) 生产代码没有数据库/事件/跨模块 import
rg -n "sqlalchemy|psycopg|nats|alembic|autograder_b[234]|autograder_host" B5/src

# 2) 端点没有写入路径
rg -n "open\(|write_text|publish|commit\(" B5/src/autograder_b5

# 3) B5 不被视为 schema 所有者
cd MAIN; .\.venv\Scripts\python.exe -c "import sys; sys.path.insert(0,'host/src'); from autograder_host import checks; print(checks.module_schema_owners())"
```

第一条应**无输出**（除 `plugin.py` 的 docstring 说明外不含任何命中）；第二条应无输出；
第三条应打印 `['B2', 'B3', 'B4']`。
