"""Daily summary and throttled severe-alert e-mails (counts & links only, zero log bodies)."""

from __future__ import annotations

import os
import smtplib
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from email.message import EmailMessage
from typing import Any

from sqlalchemy import and_, select, text
from sqlalchemy.engine import Engine

from .schema import log_alert_state, log_events
from .service import PRODUCERS_ORDER
from .sink import get_sink_stats

ALERT_THROTTLE_MINUTES = 30
DAILY_SUMMARY_INTERVAL_HOURS = 24
DEPENDENCY_ALERT_EVENTS = (
    "runner.sandbox_unavailable",
    "persistence.database_unavailable",
    "messaging.broker_unavailable",
)


@dataclass(frozen=True)
class MailConfig:
    enabled: bool
    host: str
    port: int
    user: str
    password: str
    sender: str
    recipients: tuple[str, ...]
    use_tls: bool
    ops_url: str


def load_mail_config() -> MailConfig:
    enabled = os.environ.get("LOGS_ALERT_ENABLED", "true").strip().lower() not in {"0", "false", "no"}
    host = os.environ.get("SMTP_HOST", "").strip()
    port = int(os.environ.get("SMTP_PORT", "587") or "587")
    user = os.environ.get("SMTP_USER", "").strip()
    password = os.environ.get("SMTP_PASS", "").strip()
    sender = os.environ.get("SMTP_FROM", "").strip() or user
    raw_recips = os.environ.get("LOGS_ALERT_RECIPIENTS", "").strip()
    recipients = tuple(r.strip() for r in raw_recips.split(",") if r.strip())
    use_tls = os.environ.get("SMTP_USE_TLS", "true").strip().lower() not in {"0", "false", "no"}
    ops_url = os.environ.get("LOGS_OPS_URL", "https://www.91idc.me/app/ops/logs").strip()
    return MailConfig(
        enabled=enabled,
        host=host,
        port=port,
        user=user,
        password=password,
        sender=sender,
        recipients=recipients,
        use_tls=use_tls,
        ops_url=ops_url,
    )


def send_plain_email(cfg: MailConfig, *, subject: str, body: str) -> None:
    if not cfg.enabled or not cfg.host or not cfg.recipients:
        return
    msg = EmailMessage()
    msg["Subject"] = subject
    msg["From"] = cfg.sender
    msg["To"] = ", ".join(cfg.recipients)
    msg.set_content(body)
    with smtplib.SMTP(cfg.host, cfg.port, timeout=10) as client:
        if cfg.use_tls:
            client.starttls()
        if cfg.user and cfg.password:
            client.login(cfg.user, cfg.password)
        client.send_message(msg)


def _was_recently_sent(ops_engine: Engine, key: str, *, window: timedelta, now: datetime) -> bool:
    with ops_engine.connect() as conn:
        row = conn.execute(
            select(log_alert_state.c.updated_at).where(log_alert_state.c.key == key)
        ).fetchone()
    if row is None or row.updated_at is None:
        return False
    last_at = row.updated_at
    if last_at.tzinfo is None:
        last_at = last_at.replace(tzinfo=UTC)
    return (now - last_at) < window


def _mark_sent(ops_engine: Engine, key: str, value: dict[str, Any], *, now: datetime) -> None:
    with ops_engine.begin() as conn:
        existing = conn.execute(
            select(log_alert_state.c.key).where(log_alert_state.c.key == key)
        ).fetchone()
        if existing is None:
            conn.execute(
                log_alert_state.insert().values(key=key, value=value, updated_at=now)
            )
        else:
            conn.execute(
                log_alert_state.update()
                .where(log_alert_state.c.key == key)
                .values(value=value, updated_at=now)
            )


def check_and_send_alerts(
    reader_engine: Engine,
    ops_engine: Engine,
    *,
    cfg: MailConfig | None = None,
    send_fn=send_plain_email,
    now: datetime | None = None,
    error_threshold_1h: int = 20,
) -> list[str]:
    """Check dependency failures and error spikes; send deduplicated alerts (no log bodies)."""
    config = cfg or load_mail_config()
    if not config.enabled or not config.recipients:
        return []
    current = now or datetime.now(UTC)
    cutoff_1h = current - timedelta(hours=1)
    sent_keys: list[str] = []

    with reader_engine.connect() as conn:
        dep_rows = conn.execute(
            select(log_events.c.event, log_events.c.producer)
            .where(
                and_(
                    log_events.c.occurred_at >= cutoff_1h,
                    log_events.c.event.in_(DEPENDENCY_ALERT_EVENTS),
                )
            )
            .group_by(log_events.c.event, log_events.c.producer)
        ).fetchall()

        err_count = int(
            conn.execute(
                select(text("COUNT(*)")).select_from(log_events).where(
                    and_(log_events.c.occurred_at >= cutoff_1h, log_events.c.level == "error")
                )
            ).scalar()
            or 0
        )

    for row in dep_rows:
        alert_key = f"alert:dep:{row.producer}:{row.event}"
        if _was_recently_sent(
            ops_engine, alert_key, window=timedelta(minutes=ALERT_THROTTLE_MINUTES), now=current
        ):
            continue
        body = (
            f"AutoGrader 严重告警\n"
            f"模块: {row.producer}\n"
            f"依赖故障事件: {row.event}\n"
            f"查看运维日志中心: {config.ops_url}\n"
        )
        send_fn(config, subject=f"[AutoGrader 告警] {row.producer} {row.event}", body=body)
        _mark_sent(ops_engine, alert_key, {"event": row.event, "producer": row.producer}, now=current)
        sent_keys.append(alert_key)

    if err_count >= error_threshold_1h:
        spike_key = "alert:error_spike"
        if not _was_recently_sent(
            ops_engine, spike_key, window=timedelta(minutes=ALERT_THROTTLE_MINUTES), now=current
        ):
            body = (
                f"AutoGrader 错误率升高告警\n"
                f"近 1 小时全平台 error 计数: {err_count}\n"
                f"查看运维日志中心: {config.ops_url}\n"
            )
            send_fn(config, subject=f"[AutoGrader 告警] 近 1 小时错误数达 {err_count}", body=body)
            _mark_sent(ops_engine, spike_key, {"error_1h": err_count}, now=current)
            sent_keys.append(spike_key)

    return sent_keys


def send_daily_summary(
    reader_engine: Engine,
    ops_engine: Engine,
    *,
    cfg: MailConfig | None = None,
    send_fn=send_plain_email,
    now: datetime | None = None,
) -> bool:
    """Send a once-per-24h summary email with counts and link (zero log bodies)."""
    config = cfg or load_mail_config()
    if not config.enabled or not config.recipients:
        return False
    current = now or datetime.now(UTC)
    summary_key = "summary:daily"
    if _was_recently_sent(
        ops_engine, summary_key, window=timedelta(hours=DAILY_SUMMARY_INTERVAL_HOURS), now=current
    ):
        return False

    cutoff_24h = current - timedelta(hours=24)
    counts: dict[str, dict[str, int]] = {p: {"warn": 0, "error": 0} for p in PRODUCERS_ORDER}
    with reader_engine.connect() as conn:
        rows = conn.execute(
            select(log_events.c.producer, log_events.c.level, text("COUNT(*) AS cnt"))
            .where(
                and_(
                    log_events.c.occurred_at >= cutoff_24h,
                    log_events.c.level.in_(("warn", "error")),
                )
            )
            .group_by(log_events.c.producer, log_events.c.level)
        ).fetchall()
    for row in rows:
        if row.producer in counts and row.level in ("warn", "error"):
            counts[row.producer][row.level] = int(row.cnt)

    sink_stats = get_sink_stats()
    lines = ["AutoGrader 每日运维日志摘要（近 24 小时，仅含计数不含正文）", ""]
    for prod in PRODUCERS_ORDER:
        c = counts[prod]
        lines.append(f"- {prod}: warn={c['warn']}, error={c['error']}")
    lines.append(f"\n队列丢弃累计: {sink_stats['dropped_count']}")
    lines.append(f"查看运维日志中心: {config.ops_url}")

    send_fn(config, subject="[AutoGrader] 每日运维日志摘要", body="\n".join(lines))
    _mark_sent(ops_engine, summary_key, {"counts": counts}, now=current)
    return True
