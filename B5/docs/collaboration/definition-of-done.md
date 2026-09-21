# 完成定义（Definition of Done）

一项工作算"完成"，需要下面**每一条**都有可核验动作与结果，而不是"看起来好了"。

## 1. 逐条检查清单

| # | 条件 | 可核验动作 |
|---|---|---|
| 1 | 代码存在且只做声明过的事 | `rg -n "open\(|write_text|sqlalchemy|psycopg|nats|alembic" B5/src` → 无写入/无库/无事件命中 |
| 2 | 静态检查通过 | `cd B5 && uv run ruff check .` → `All checks passed!` |
| 3 | 一致性套件通过且**没有 skip** | `uv run pytest -q` → 全部 passed，`-ra` 输出里没有 skip 行 |
| 4 | 负例仍然会红 | 临时把某个负例的期望异常改错，确认套件失败，再改回；**不得**保留"只验证成功路径"的断言 |
| 5 | 响应体符合契约 | `tests/test_conformance_endpoint.py` 用 `b5.yaml` / `common.yaml` 的 schema 校验响应（不手抄字段） |
| 6 | 清单/描述符/锁三方一致 | `python -m autograder_host.checks` → 三条 ok |
| 7 | 治理门禁纳入本模块 | `checks.discovered_modules()` 含 B5；`checks.module_schema_owners()` 不含 B5 |
| 8 | 文档集合完整且链接有效 | 同一门禁的 `check_module_governance()` 与 `check_document_encoding_and_links()` 通过 |
| 9 | 追踪表与状态表同步 | [../requirements/traceability.md](../requirements/traceability.md) 的"状态"列与 [../README.md](../README.md) 状态表一致 |
| 10 | 阶段文档记录了**真实**命令与结果 | [../phases/active/phase-01-platform-conformance.md](../phases/active/phase-01-platform-conformance.md) 的"验证记录"含命令、环境与结果；未执行项写明原因 |
| 11 | 无 mock 进入交付路径 | `rg -n "mock|Mock|monkeypatch" B5/src` → 无命中（测试中的夹具服务器已按策略声明为"外部服务替换"） |
| 12 | 无兼容层、无死选项、无 TODO | `rg -n "TODO|FIXME|deprecated|legacy|fallback" B5/src B5/tests` → 无命中 |
| 13 | 安全自检 | 日志中无令牌/cookie；`detail` 不含内部绝对路径或凭据；只读公钥 |
| 14 | 破坏性变更（若有）走完流程 | 契约变更 → CHG 记录 → 摘要重算 → 五份锁同步（注意 `relock_contracts.py` 目前只覆盖四份，见 troubleshooting §4） |

## 2. 提交清单（一次改动）

1. `module.yaml` / descriptor 若字段变化 → 两处同时改，并跑一次 `checks`。
2. `docs/api/contract.md` 若提供/消费关系变化 → 同步；**不**复制字段表。
3. `docs/testing/cases.md` 若新增/删除用例 → 同步（用例 ID 与测试函数一一对应）。
4. `docs/phases/active/` 追加真实验证记录；失败与被跳过的项**必须**留下。
5. `docs/README.md` 状态表若状态变化 → 同步（不在索引中的正式文档视为治理缺陷）。

## 3. 明确不算完成的情况

- "本地跑过但没记录命令与结果" —— 阶段文档缺失时不算完成（铁律三）。
- "套件通过但负例从没红过" —— 没有失败能力的一致性套件不算证据（CHG-0010 §5.4）。
- "文档写了'应该可用'" —— 铁律二禁止的措辞；只能是 `骨架` / `待实现` / `已完成` / `阻塞`。
- "为了通过而放宽断言/加 skip" —— 见 [../runbooks/troubleshooting.md](../runbooks/troubleshooting.md) §2。
