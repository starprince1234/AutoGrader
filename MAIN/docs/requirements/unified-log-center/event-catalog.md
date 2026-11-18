# 统一日志中心关键事件目录（`RQ-B-1/2/3/4-99`）

本目录定义各模块必须产生的关键运行日志事件，作为 Q5 事件补齐与 Q7 线上验收的可检验清单。
所有事件入库时统一使用 `MAIN/contracts/schemas/log-record.schema.json` 信封格式，`debug` 级别不入库。

## 1. B1（前端浏览器，`process: browser`）

| 事件名 | 级别 | 关键字段 | 触发时机 |
|---|---|---|---|
| `auth.login.succeeded` | `info` | `request_id` | 用户登录成功 |
| `auth.login.failed` | `warn` | `request_id`, `status_code` | 用户登录失败 |
| `api.request.failed` | `warn` / `error` | `request_id`, `method`, `path`, `status_code` | API 请求返回 4xx/5xx 或网络异常 |
| `submission.created` | `info` | `request_id`, `assignment_id`, `submission_id` | 学生提交代码动作 |
| `ui.error_boundary.caught` | `error` | `request_id`, `error_type` | React ErrorBoundary 捕获未处理异常 |

## 2. B2（提交与评测，`process: host | worker | intake`）

| 事件名 | 级别 | 关键字段 | 触发时机 |
|---|---|---|---|
| `submission.intake.received` | `info` | `request_id`, `correlation_id`, `course_id`, `assignment_id`, `submission_id` | API 接收提交入库 |
| `evaluation.run.started` | `info` | `correlation_id`, `submission_id`, `evaluation_run_id`, `course_id`, `io_type` | worker 开始评测 |
| `evaluation.run.completed` | `info` | `correlation_id`, `submission_id`, `evaluation_run_id`, `course_id`, `io_type`, `duration_ms`, `outcome` | worker 评测完成 |
| `evaluation.run.failed` | `error` | `correlation_id`, `submission_id`, `evaluation_run_id`, `course_id`, `error_category` | worker 评测失败 |
| `runner.sandbox_unavailable` | `error` | `correlation_id`, `submission_id`, `evaluation_run_id`, `dependency` | nsjail/cgroup 沙箱不可用 |
| `persistence.database_unavailable` | `error` | `correlation_id`, `dependency` | 数据库依赖故障 |
| `messaging.broker_unavailable` | `error` | `correlation_id`, `dependency` | NATS 消息代理不可用 |
| `intake.mail.pulled` | `info` / `warn` | `correlation_id`, `count`, `outcome` | 邮件收信拉取完成或部分失败 |

## 3. B3（题库，`process: host`）

| 事件名 | 级别 | 关键字段 | 触发时机 |
|---|---|---|---|
| `question.created` | `info` | `request_id`, `course_id`, `actor_id`, `io_type` | 创建题目 |
| `question.version.published` | `info` | `request_id`, `correlation_id`, `course_id`, `question_version_id`, `io_type` | 发布题目版本 |
| `question.compile.failed` | `warn` / `error` | `request_id`, `course_id`, `question_version_id`, `io_type`, `reason` | 题目编译/校验失败 |
| `question.reference.verified` | `info` / `warn` | `request_id`, `course_id`, `question_version_id`, `outcome` | 参考答案验证完成 |
| `question.stats.aggregated` | `info` | `correlation_id`, `question_version_id`, `count` | 统计任务汇总完成 |

## 4. B4（身份、课程与平台，`process: host | notifier`）

| 事件名 | 级别 | 关键字段 | 触发时机 |
|---|---|---|---|
| `auth.session.issued` | `info` | `request_id`, `actor_id` | 登录签发令牌 |
| `auth.session.refreshed` | `info` | `request_id`, `actor_id` | 刷新会话令牌 |
| `auth.access.denied` | `warn` | `request_id`, `actor_id`, `reason` | 登录或鉴权被拒 |
| `course.membership.changed` | `info` | `request_id`, `course_id`, `actor_id`, `action` | 用户或课程成员/角色变更 |
| `notification.assignment.sent` | `info` / `warn` | `correlation_id`, `course_id`, `assignment_id`, `count`, `outcome` | 作业发布通知发送完成 |

## 5. B5（一致性报告，`process: host`）

| 事件名 | 级别 | 关键字段 | 触发时机 |
|---|---|---|---|
| `conformance.request.unauthenticated` | `warn` | `request_id`, `reason`, `path` | 一致性端点认证被拒 |
| `conformance.report.generated` | `info` / `warn` | `request_id`, `actor_id`, `consistent`, `difference_count` | 生成一次契约一致性报告 |

## 6. MAIN（组合宿主与日志中心，`process: host`）

| 事件名 | 级别 | 关键字段 | 触发时机 |
|---|---|---|---|
| `platform.host.started` | `info` | `module_count`, `db_sink_enabled` | composition host 启动完成 |
| `logs.retention.cleaned` | `info` | `deleted_info`, `deleted_warn_error`, `deleted_capacity`, `storage_bytes` | 日志保留期与容量清理任务执行 |
| `logs.viewed` | `info` | `request_id`, `actor_id`, `course_id` | 管理员/教师查询或追踪日志（审计） |
| `logs.exported` | `info` | `request_id`, `actor_id`, `format`, `row_count` | 管理员/教师导出日志（审计） |
| `logs.queue.dropped` | `warn` | `process`, `dropped_count` | 日志内存队列满发生丢弃 |
