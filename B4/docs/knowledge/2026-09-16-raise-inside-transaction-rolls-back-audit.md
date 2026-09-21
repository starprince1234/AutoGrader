# 事务边界：写完之后抛错，等于没写

- 现象：三类安全/状态逻辑"看起来生效、实际没有"——
  1. 登录限流返回 `429`，但 `b4.login_attempts` 没有该次记录，审计里也没有 `auth.login.throttled`；
  2. 复用已轮换的刷新凭据返回 `401`，但整个 session family **没有**被撤销（失窃检测形同虚设）；
  3. 名单确认时"全部行被拒"返回 `422`，但导入状态仍是 `previewed`，没有 `roster.import.failed` 审计。

- 重复触发条件：在 `with engine.begin() as connection:` 块内，先写审计/记账/终态，**随后 `raise`**。
  三处都是"先记录，再报错"的自然写法，因此重复出现。

- 根因：`raise` 让上下文管理器走异常路径，事务**回滚**。写入与错误响应是一对不可分割的意图，但代码把它们
  放进了同一个会回滚的作用域。日志（`logger.warning`）照常输出，所以从日志看一切正常——这也是它难被
  发现的原因。

- 最终修复：把"记录"与"报错"分成两个阶段：

  ```python
  rejection: str | None = None
  with engine.begin() as connection:
      ...写审计/终态...
      rejection = "reuse"
  if rejection is not None:
      raise unauthenticated()
  ```

  修复位置：`api/auth.py`（登录限流、刷新重用）、`roster/service.py`（全拒终态）。

- 预防措施：
  - 规则化：**事务内只写不抛**；需要报错就记状态、出事务再抛。
  - 测试直接断言"副作用是否存在"，而不只断言状态码：
    `test_a_throttled_attempt_is_recorded`、`test_reusing_a_rotated_credential_revokes_the_family`、
    `test_a_fully_rejected_roster_commits_nothing_and_is_terminal`。
  - 同类陷阱：`env.py` 中的 Alembic 版本——`set_config` 开了隐式事务后 Alembic 不再提交，迁移静默整体
    回滚（见 `../runbooks/troubleshooting.md` 第 2 条）。

- 关联证据：`docs/phases/active/phase-01-platform-implementation.md` 缺陷表第 9 项。
