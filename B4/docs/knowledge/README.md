# B4 知识笔记

## 收录标准

- 同一根因至少出现**两次**，且有**稳定修复**（否则留在 `runbooks/troubleshooting.md` 或 `phases/`）。
- 与代码/部署/契约相关，不收录一次性临时结论。

## 命名规则

`YYYY-MM-DD-<short-problem>.md`（如 `2026-09-15-uv-cache-denied.md`）。

## 模板

```md
# <问题短标题>

- 现象：
- 重复触发条件：
- 根因：
- 最终修复：
- 预防措施：
- 关联证据：
```

## 当前笔记索引

| 日期 | 问题 | 文件 |
|---|---|---|
| 2026-09-16 | 事务内写完后 `raise` 会把审计/撤销/终态一起回滚 | `2026-09-16-raise-inside-transaction-rolls-back-audit.md` |
| 2026-09-16 | psycopg3 + SQLAlchemy 下 `ON CONFLICT` 的 `rowcount` 恒为 -1 | `2026-09-16-on-conflict-rowcount-is-minus-one.md` |
