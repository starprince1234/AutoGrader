# psycopg3 + SQLAlchemy 下 `ON CONFLICT` 的 `rowcount` 恒为 -1

- 现象：inbox 去重把**每一次**投递都判成"重复"，业务处理函数永远不执行；名单写入的计数出现负数。
  实测：普通 `INSERT`、`INSERT … ON CONFLICT DO NOTHING` 两种写法在同一环境下的 `rowcount` 都是 `-1`，
  而 `UPDATE` 正常返回受影响行数（`0` 表示无匹配）。

- 重复触发条件：任何用 `result.rowcount == 1` 判断"是否真的插入了"的代码路径。本模块出现两处
  （`messaging/consumer._record_delivery`、`roster/service._upsert_enrollments`），另有 `ops/roles.py`。

- 根因：该组合下驱动不上报 INSERT 的受影响行数（返回 `-1`），与"0 行受影响"不可区分。用 `rowcount`
  判定插入成功既会误判也会漏判。

- 最终修复：改用 `RETURNING` + `fetchone()`——冲突时不返回行，据此判定：

  ```python
  statement = (
      pg_insert(inbox_events)
      .values(...)
      .on_conflict_do_nothing(index_elements=["consumer", "event_id"])
      .returning(inbox_events.c.inbox_id)
  )
  first_delivery = connection.execute(statement).fetchone() is not None
  ```

  另外：`rowcount` 在事务提交、连接关闭后再读也不可靠，判断必须在 `with engine.begin()` 内完成。

- 预防措施：
  - 统一约定：**不用 `rowcount` 判断插入**，只用 `RETURNING`。
  - 测试必须断言"业务效果次数"而不是"没报错"：
    `test_a_duplicate_delivery_produces_one_business_effect` 断言处理函数恰好被调用一次。

- 关联证据：`docs/phases/active/phase-01-platform-implementation.md` 缺陷表第 3 项、
  `docs/runbooks/troubleshooting.md` 第 8 条。
