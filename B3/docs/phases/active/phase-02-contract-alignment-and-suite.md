# Phase 02 契约演进后的复核：EdDSA、版本题干 DTO 与套件实跑（当前阶段）

## 目标与范围

Phase 01 完成后，集成启动暴露了三处**B3 与契约/宿主不一致**的地方，以及四处因契约演进
（版本 DTO 增加字段、`QuestionPublic` 增加 `versions`）而过期的断言。本阶段逐条修复并把
B3 套件真实跑一遍。

范围外：B3 的业务能力（Phase 01 已实现）、契约改动本身（由 MAIN 判定并落地）。

## 输入与决策

| # | 输入 / 现象 | 决策 |
|---|---|---|
| 1 | 宿主把 `B4_JWKS_URL` 注入 B3 的设置视图，而 B3 读的是 `B3_JWKS_URL` → `token_verifier=None`，所有令牌被拒 | 环境变量名以**宿主注入的名字**为准（`B4_JWKS_URL`）：JWKS 由 B4 提供，变量名跟着提供方，B3 不自造别名 |
| 2 | B3 的验证器写死 RS256，而 B4 签发的是 **Ed25519/EdDSA**（ADR-0006）→ 一切令牌校验失败 | `auth.py` 改为 EdDSA：JWKS `kty: OKP` / `crv: Ed25519`，`_verify_rs256` 换成 `_verify_eddsa` |
| 3 | 令牌的角色声明是 B4 的 `role` **列表**，B3 只接受字符串 → 角色恒为空 | `_roles_claim()` 同时接受列表与字符串 |
| 4 | `QuestionVersionPublic` 不包含题干，B1 只能把**题目当前题干**渲染在版本标题下 | 契约增加 `stem_markdown` / `stem_assets`（`stem_markdown` 必需）；B3 的 DTO/序列化/路由同步。契约缺口 2 关闭 |
| 5 | `QuestionPublic` 没有版本列表，客户端无法寻址某个版本 | 契约增加 `QuestionVersionSummary` + `QuestionPublic.versions`；B3 提供 `_question_extras()` |
| 6 | `descriptor.events` 只列 publishes，而 B3 实际订阅 `evaluation.completed` | 与 B2 的既有约定对齐：扁平描述符承载**两个方向**，`module.yaml` 仍是带方向的权威 |
| 7 | B3 的领域异常在宿主里变成 500（`GET /api/v1/questions` 无令牌返回 500 而非 401） | 走 SDK 的 `PlatformModule.configure_app` 钩子安装异常处理器与请求日志；钩子已写进 `MAIN/sdk` 的协议说明 |

## 完成条件

1. B3 能校验 B4 真实签发的 Ed25519 令牌，并按角色/课程作用域判定。
2. 版本 DTO 能独立渲染历史版本题干；题目 DTO 能列出全部版本。
3. 未认证请求得到 401 而不是 500。
4. 套件真实转绿。

## 验证（真实执行）

```powershell
docker compose -p b3 up -d          # 模块本地库 55434（若未起）
uv run pytest -q
```

修复前：

```text
4 failed, 278 passed, 6 warnings in 20.80s   exit 1
FAILED tests/test_dto_boundary.py::test_serialize_public_projects_only_dto_fields
FAILED tests/test_plugin.py::test_descriptor_events_match_the_module_declaration_for_publishes
FAILED tests/test_questions_api.py::test_question_detail_has_no_protected_field
FAILED tests/test_questions_api.py::test_question_version_dto_is_public_only
```

修复后：

```text
282 passed, 6 warnings in 22.98s   exit 0
```

四条失败全部是**断言未跟上契约**，不是实现缺陷：

- `test_serialize_public_projects_only_dto_fields` 构造 `QuestionVersionPublic` 时缺必需的
  `stem_markdown`（补上并注明原因）。
- 另外三条是响应键集合：分别补 `versions`、`versions`、`stem_markdown` + `stem_assets`。
- `test_descriptor_events_match_the_module_declaration_for_publishes` 改为断言
  publishes ∪ subscribes（`module.yaml` 仍是唯一权威，测试直接从它读两个列表）。

## 未完成 / 阻塞

- 无 `阻塞` 项。
- 跨模块未联调部分按 B3 `AGENTS.md` 的要求**继续保持未完成表述**：B2 runner 端到端、
  NATS 真实消费（测试期 mock）、B4 真实 JWKS 的**线上轮换**、性能基准、Linux 冷启动。
  本阶段只证明"B3 能按 B4 的算法与声明校验令牌"，不证明轮换运维。
- `MAIN/contracts/**` 与 `contracts.lock` 由 MAIN 修改与重新锁定；B3 未改这两个位置。
