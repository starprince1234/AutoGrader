# 冷启动：30 分钟接手 B5

目标：读完三个文件 → 装依赖 → 跑通门禁 → 说出契约版本 → 认领一个工作包。
每一步都给出**成功标志**；没有成功标志的一步不写在这里。

## 第 1 步：读三个文件（10 分钟）

1. [../../README.md](../../README.md) —— 这个模块是什么、不做什么、已知限制。
2. [../requirements/scope.md](../requirements/scope.md) —— 范围与非目标（**非目标那节最重要**）。
3. [../../module.yaml](../../module.yaml) —— 机器可读的自我声明（能力、依赖、路由、schema）。

**成功标志**：你能用一句话说出"B5 比较哪三个视图"，并说出它**不做**的两件事
（不做运行期注册表、不写任何数据）。

## 第 2 步：确认契约现状（2 分钟）

打开 [../../contracts.lock](../../contracts.lock)，记下 `contract_version` 与 `digest`；
契约文本在 `../../../MAIN/contracts/openapi/b5.yaml`。

**成功标志**：你说得出契约版本（本文件不写死它——版本会随契约流程前进，写死必然过期），
并知道端点是 `GET /api/v1/platform/conformance`。

## 第 3 步：装依赖并跑通门禁（8 分钟）

```powershell
cd B5
uv sync
uv run ruff check .
uv run pytest -q
```

**成功标志**：`ruff` 输出 `All checks passed!`；`pytest` 全部通过且**没有 skip**。
若 pytest 在收集期报 `MAIN/.env.integration is missing`，按
[../runbooks/troubleshooting.md](../runbooks/troubleshooting.md) §2 处理——这是环境问题，不是代码问题。

## 第 4 步：看一次真实的报告（5 分钟）

```powershell
cd ..\MAIN
.\.venv\Scripts\python.exe -c "import sys; sys.path.insert(0,'host/src'); from autograder_host import checks; print(checks.discovered_modules()); print(checks.module_schema_owners())"
```

**成功标志**：第一行含 `B5`，第二行**不含** `B5`。这两行就是 CHG-0010 §5.1/§5.2 要求的证据。

## 第 5 步：认领一个工作包（5 分钟）

打开 [workpackages.md](workpackages.md)，从 `WP-B5-A/B/C` 里挑一个，在认领表里写上你的名字。
每个工作包都写了"并行前提"——先确认前提已冻结，再动手。

**成功标志**：认领表里有一行是你的，且你能说出该工作包覆盖哪几条验收（`acceptance.md` 的编号）。

## 常见误区（写给第一次接手的人）

| 误区 | 事实 |
|---|---|
| "报告说 `consistent: true`，所以平台没问题" | 报告说的是**契约与运行组合一致**，不是"平台正确"。业务正确性不在这份报告的范围内 |
| "B5 应该顺便把不一致修掉" | 报告是观测面。改契约走 CHG，改模块是模块自己的事 |
| "B5 没有数据库，所以它是骨架" | 无数据库是 [ADR-0001](../adr/ADR-0001-read-only-conformance-auditor.md) 的**结论**，不是缺失 |
| "加个 `pytest.skip` 让套件在缺环境时也能绿" | 静默跳过的一致性检查与通过无法区分——这条已被明确否决 |
