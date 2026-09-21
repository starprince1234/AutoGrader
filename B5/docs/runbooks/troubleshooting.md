# 故障处置

只写**真实遇到过或可确定**的问题。每条按"症状 → 可能原因 → 处置 → 如何确认已恢复"。

## 1. `uv sync` 失败：`Readme file does not exist: README.md`

- **症状**：hatchling 构建 `autograder-b5` 时抛 `OSError: Readme file does not exist: README.md`。
- **原因**：`pyproject.toml` 声明了 `readme = "README.md"`，而该文件不存在（骨架尚未补文档时）。
- **处置**：补 `README.md`，或在脚手架阶段先去掉该行——**不要**把 `readme` 指向一个占位文件。
- **确认**：`uv sync` 退出码 0，`uv run python -c "import autograder_b5, importlib.metadata as m; print(m.version('autograder-b5'))"` 打印版本号。

## 2. 一致性套件拒绝收集：`MAIN/.env.integration is missing`

- **症状**：`uv run pytest` 在收集阶段直接失败，报出上面这句话与原因说明。
- **原因**：套件要组装**真实**的宿主应用（`autograder_host.main` 在导入期就调用 `create_app()`），
  需要集成环境变量。
- **处置**：按 [MAIN 集成运行手册](../../../MAIN/docs/runbooks/integration.md) 准备
  `MAIN/.env.integration`（它是 gitignored 的本地文件），或先在 MAIN 里跑通一次集成。
- **确认**：`uv run pytest -q` 完成收集并输出用例数。
- **不要**用 `pytest.skip` 绕过：被跳过的一致性检查与通过无法区分。

## 3. 门禁报 `cannot build descriptor from autograder_b5.plugin:create_module`

- **症状**：`python -m autograder_host.checks` 在 `check_module_descriptors()` 处失败，
  消息含 `No module named 'autograder_b5'`。
- **原因**：B5 没有安装进 **MAIN 的**虚拟环境。门禁按 `module.yaml` 的 `entry_point` 真实导入并调用工厂。
- **处置**：`cd MAIN && uv sync --extra modules`（该 extra 已在 CHG-0010 中加上 `autograder-b5`）。
- **确认**：`.\.venv\Scripts\python.exe -c "import importlib.metadata as m; print([e.name for e in m.entry_points(group='autograder.modules')])"` 输出含 `b5`。

## 4. 门禁报 `B5/contracts.lock: contract digest mismatch`

- **症状**：契约变更后四条 `checks` 全绿，只有 B5 的锁报摘要不一致（或反之：B5 绿、其它四条报）。
- **原因**：`MAIN/scripts/relock_contracts.py` 的模块列表仍是字面量 `("B1","B2","B3","B4")`，
  它算得出摘要却**写不到 B5 的锁**。这是已知的规范缺口（见
  [../phases/active/phase-01-platform-conformance.md](../phases/active/phase-01-platform-conformance.md) §风险与遗留项）。
- **处置**：运行 `python MAIN/scripts/relock_contracts.py`（它会重写四把锁并打印实时摘要），
  再把同一摘要写进 B5 的锁——最稳妥的做法是**复制**任意一把刚被工具写过的锁
  （五份锁的 `source`/`digest_policy`/`status` 完全相同），绝不手抄摘要数字。
- **确认**：`python -m autograder_host.checks` 三条 ok；`tests/test_governance_discovery.py::test_b5s_lock_is_the_live_contract_digest` 通过。

## 5. 带令牌仍返回 401

按 `detail` 定位（`detail` 是给运维的固定短句，不含令牌内容）：

| `detail` | 原因 | 处置 |
|---|---|---|
| `an Authorization header with a bearer token is required` | 没带 `Authorization` 头，或不是 `Bearer` 方案 | 检查调用方；B5 不接受 cookie |
| `the issuer's key set is unreachable, so the token cannot be verified` | `B4_JWKS_URL` 指向的地址不可达（B4 未起、端口不对） | 核对 `MAIN/.env.integration` 的 `B4_JWKS_URL` 与宿主端口；确认 `GET /api/v1/auth/jwks` 可访问 |
| `the issuer published no usable Ed25519 signing key` | JWKS 里没有 `kty=OKP`/`crv=Ed25519`/带 `kid` 的键 | 确认 B4 发布的键类型；B5 **只**接受 Ed25519（与 B4 实际签发的算法一致） |
| `the bearer token names a key the issuer does not publish` | 令牌的 `kid` 不在当前 JWKS 中（密钥轮换或令牌来自别的环境） | 重新登录取新令牌；确认 `B4_JWKS_URL` 指向的是签发它的那个 B4 |
| `the bearer token was rejected` | `iss`/`aud`/`exp` 不符或签名不匹配 | 核对 `JWT_ISSUER`/`JWT_AUDIENCE` 与 B4 的 `B4_JWT_ISSUER`/`B4_JWT_AUDIENCE` |

- **确认**：`curl.exe -i -H "Authorization: Bearer <token>" http://127.0.0.1:18000/api/v1/platform/conformance` 返回 `200`。
- **注意**：密钥集缓存 300 秒且**按 URL 分键**；换 URL 会立刻生效，换同一 URL 背后的键集则最多等 300 秒。

## 6. 装载失败：`module capability dependency missing: {'B5': ['b2-submissions', ...]}`

- **症状**：宿主启动失败，消息里列出 B5 缺失的能力。
- **原因**：B5 装了，但它 `requires` 的业务模块没装（B5 的 `requires` 是 host + b4-auth +
  b3-questions + b2-submissions）。这是**有意的**：缺业务模块时报告会把它们的路径全报成无人服务。
- **处置**：`cd MAIN && uv sync --extra modules`；或从组合中移除 B5。
- **确认**：宿主启动成功，`/health/ready` 的 `modules` 列出 4 个插件。

## 7. `discovered_modules()` 里没有 B5

- **症状**：门禁/脚本认为只有四个模块。
- **原因**：`B5/module.yaml` 不存在（例如被误删或改名）。发现规则是 `glob("B*/module.yaml")`。
- **处置**：恢复清单文件。**不要**在任何门禁里手写模块列表——ADR-0013 修掉的就是这种字面量。
- **确认**：`checks.discovered_modules()` 返回五个名字。
