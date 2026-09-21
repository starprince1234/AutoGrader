# 架构总览

## 1. 在整体架构中的位置

B5 是 **MAIN composition host 里的第五个插件**，与 B2/B3/B4 同级：它不拥有业务数据，也不被任何
模块依赖（没有任何模块 `requires: b5-conformance`）。它唯一的输出是**关于平台自身的一个回答**。

```text
                 ┌──────────────────────────────────────────┐
                 │ MAIN host  (autograder_host.main)        │
                 │  entry points → _load_modules() → DAG    │
                 │  app.state.modules  (装载顺序)           │
                 └───┬──────────┬───────────┬───────────┬───┘
                     │          │           │           │
                    B4         B3          B2          B5
              身份/课程/   题库/版本/   提交/评测/   一致性报告
              注册表       测试包       成绩导出
                     ▲                                  │
                     │ JWKS（本地验签取公钥）            │ 只读
                     └──────────────────────────────────┘
                                                        ▼
                                MAIN/contracts/openapi/*.yaml（契约文件）
```

B5 与邻居的交互**只有两条**，都是只读：

1. 从宿主进程的 `app.state.modules` 读取各模块的 SDK `ModuleDescriptor`（公开协议，不是模块内部）；
2. 从 B4 发布的 `GET /api/v1/auth/jwks` 取验签公钥（契约里的公开端点）。

它**从不导入** B2/B3/B4 的代码，也不读它们的 schema（ADR-0011：跨模块只经 API/事件）。
测试里唯一一次 `import autograder_b4.registry` 是为了断言"ADR-0013 决策 5 在 B4 一侧也成立"，
已在 `tests/test_load_failures.py` 的 docstring 中注明，且不在交付路径上。

## 2. 内部分层与依赖方向

```text
api.py          HTTP 边界：路由、认证调用、Problem Details 处理器
  │
conformance.py  纯比较：三视图 → ConformanceReport（无 I/O，可用构造输入直接测）
  │        │
  │        └── contracts.py   视图 1：读 MAIN/contracts/openapi/*.yaml
  │
auth.py         验签：Bearer → JWKS → Ed25519 → 注册声明（只读网络）
  │
config.py       Settings（CONTRACTS_DIR / B4_JWKS_URL / JWT_ISSUER / JWT_AUDIENCE）
  │
errors.py       B5Error → RFC 9457 ProblemDetails
  │
plugin.py       SDK 边界：ModuleDescriptor + router(context) + health + configure_app
```

依赖方向**单向向下**，且 `conformance.py` 不依赖 FastAPI 之外的东西——所以"比对逻辑是否正确"
可以在没有任何网络、没有任何其他模块的情况下验证（`tests/test_conformance_views.py` 正是这么做的）。

## 3. 主要数据流

```text
请求 GET /api/v1/platform/conformance
  │  Authorization: Bearer <EdDSA JWT>
  ▼
api.conformance ──▶ auth.authenticate ──▶ JWKS（缓存 300s）──▶ 验签 + iss/aud/exp
  │  失败 → Unauthenticated → configure_app 装的处理器 → 401 ProblemDetails（+ 一条日志）
  ▼
build_report(contracts_dir, descriptors, application_paths)
  │
  ├─ 视图 1：contracts.contract_paths()  ← 读 MAIN/contracts/openapi/*.yaml
  ├─ 视图 2：platform_routes()           ← 应用路由中"无任何模块声明覆盖"的那些
  └─ 视图 3：descriptors[].routes        ← app.state.modules 的 SDK 描述符
  ▼
ConformanceReport（200 JSON，字段由契约定义，文档不复制）
```

## 4. 与各模块的交互方式

| 对方 | 交互 | 说明 |
|---|---|---|
| MAIN host | entry point 装载、`app.state.modules`、`/health/ready` 汇总 | 唯一的运行期契约（SDK） |
| MAIN 契约 | 只读 `MAIN/contracts/openapi/*.yaml` | 视图 1 的来源；B5 不是契约作者 |
| B4 | 只读其发布的 JWKS | 验签公钥；**不**在请求路径上回调 B4 判定身份 |
| B3 / B2 | 无直接交互 | 只在报告里出现它们的模块名与前缀 |
| B1 | 无交互 | B1 是宿主提供的静态客户端，不在 capability DAG 内（ADR-0007） |

## 5. 已知缺口

1. **B5 不认领 `RQ-B-*`**（编号空间是 `B0`–`B4`），需求权威是 ADR-0013 / CHG-0010。
   见 [../requirements/traceability.md](../requirements/traceability.md) §0。
2. **`relock_contracts.py` 写死四个模块**，无法写 B5 的锁；见
   [../phases/active/phase-01-platform-conformance.md](../phases/active/phase-01-platform-conformance.md) §风险与遗留项。
3. **验签需要 B4 的 JWKS 可达**：不可达时 fail closed（`401`），不会降级放行。
4. 报告只比对**路径/前缀**，不比对方法（`GET` vs `POST`）与请求体 schema：
   契约与 `module.yaml` 两侧都只声明路径，方法级比对没有第二个可比的权威来源。
