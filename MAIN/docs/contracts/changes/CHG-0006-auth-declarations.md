# CHG-0006 契约补全鉴权声明：`securitySchemes` + 每个 operation 的 `security`

- **级别**：P2（语义）
- **状态**：已生效（2026-09-18 判定）
- **提出方**：MAIN（集成审计，见 `MAIN/docs/innovation/inventory.md` 的 E3）
- **契约版本**：0.1.0 → **0.2.0**

## 1. 问题（真实、可复现）

`docs/api/governance.md` 强制"所有业务端点通过 Bearer Ed25519 JWT 鉴权"，而契约里：

- **三份 OpenAPI 文件都没有 `components.securitySchemes`**；
- **43 个 operation 中有 42 个没有 `security`**，唯一声明的是 `getJwks` 的 `security: []`。

后果不是"文档不好看"，而是三类**真实**的误用：

1. **契约消费方无法区分受保护与公开端点。** 生成的客户端把 `createSession` 与
   `createQuestion` 当成同一种东西，运行到一半才发现需要令牌。
2. **B2 → B3 的内部调用在契约里看不出用的是什么凭据。** 它的实际失败模式已经发生过一次：
   `.env.integration` 把 `B2_B3_AUTHORIZATION` 写成裸令牌（缺 `Bearer ` 前缀），B3 回
   `401 "a bearer token is required"`，而 B2 的报错刻意不含 URL 与响应体，于是整个评测链在
   **取题包**阶段就断掉，表现为 `infrastructure_error`。契约里若能读到"该端点用
   `serviceToken`、值是完整 header"，这个错误在配置评审阶段就会被发现。
3. **审计工具无法校验鉴权覆盖。** "哪些端点漏了鉴权"此前只能靠人读。

## 2. 变更内容

### 2.1 新增两个安全方案（`openapi/common.yaml`）

| 方案 | 类型 | 用途 |
|---|---|---|
| `bearerAuth` | `http` / `bearer` / `bearerFormat: JWT` | 用户会话。B4 签发短时 Ed25519 令牌，其余模块**本地**用 JWKS 验签，不在请求路径上回调 B4 |
| `serviceToken` | `http` / `bearer` | 服务间调用（`tags: [internal]`）。没有"代表哪个用户"这回事，因此不复用用户令牌 |

### 2.2 三份文件各自加**文档级默认**

`b2.yaml` / `b3.yaml` / `b4.yaml` 根级新增 `security: [{bearerAuth: []}]`。
默认即"需要用户令牌"，例外逐条声明——这样**新增 operation 默认是受保护的**，
而不是默认公开、需要人记得加。

### 2.3 逐条声明例外

| 文件 | operation | 声明 | 理由 |
|---|---|---|---|
| `b4.yaml` | `createSession` | `security: []` | 凭据在请求体里，此时还没有可鉴权的主体 |
| `b4.yaml` | `refreshSession` | `security: []` | 由 HttpOnly 刷新 cookie 鉴权，不是 bearer 令牌 |
| `b4.yaml` | `getJwks` | `security: []`（原有） | 各模块本地验签，公钥集必须免令牌可读；它只是公钥材料 |
| `b4.yaml` | `deleteSession` | **保持默认（需要令牌）** | 实现上确实 `Depends(dependencies.principal)`；契约与现实一致 |
| `b3.yaml` | `uploadVersionFixture` | `security: [{serviceToken: []}]` | 服务间写入 fixture 字节 |
| `b3.yaml` | `getEvaluationPackage` | `security: [{serviceToken: []}]` | B2 的 runner 取包；响应含参考答案与隐藏用例 |

## 3. 是否破坏兼容

**否。** 这是**补记既有事实**：三个模块的实现一直都要求这些凭据，只是契约没写。
没有任何请求/响应结构、字段或状态码改变。因此按 MINOR 升版（0.2.0）。

> 反过来说：如果某个客户端此前**真的**匿名调用成功过，那才是缺陷——本变更会让它显形，
> 而不是让它失败。

## 4. 受影响模块与需要做的改动

| 模块 | 需要做什么 |
|---|---|
| **MAIN** | 契约与 `contracts.lock`（本变更）；`docs/api/governance.md` 指向 `securitySchemes` 而不是只写一句要求 |
| **B1** | `node scripts/generate-api-types.mjs` 重新生成类型（生成的类型会带上 `security` 元数据）。运行时行为不变 |
| **B2** | 无代码改动。**运维侧**：确认 `B2_B3_AUTHORIZATION` 的取值包含 `Bearer ` 前缀（见 §1.2 的真实故障） |
| **B3** | 无代码改动：`uploadVersionFixture` / `getEvaluationPackage` 已经在要求服务令牌 |
| **B4** | 无代码改动：三个公开端点与其余受保护端点的实现已经如此 |

## 5. 验证

```powershell
cd MAIN
.\.venv\Scripts\python.exe -m autograder_host.checks      # 三条门禁 + 计算新摘要
```

补充验证（本变更特有，因为"契约说有鉴权"必须与"实现真的要鉴权"一致）：

```powershell
# 匿名调用一个默认受保护的端点 → 401；匿名调用公开端点 → 200
curl.exe -s -o NUL -w "%{http_code}" http://127.0.0.1:18000/api/v1/modules
curl.exe -s -o NUL -w "%{http_code}" http://127.0.0.1:18000/api/v1/auth/jwks
```

实测结果见 `MAIN/docs/phases/active/phase-01-integration-runtime.md`。

## 6. 回滚方式

回退本批次提交，重新运行 `scripts/relock_contracts.py`，四份 `contracts.lock` 回到 0.1.0 与旧摘要。
**不允许**只回滚其中一部分（版本号必须三份一致）。
