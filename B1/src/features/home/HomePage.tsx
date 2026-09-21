import { Link } from "@tanstack/react-router";
import { t } from "../../i18n";
import { useSession } from "../../auth/sessionContext";
import {
  canExportGrades,
  canImportRoster,
  canManageUsers,
  canSubmitAnswer,
  canViewRoster,
} from "../../auth/visibility";
import { Panel } from "../../ui/components";

/**
 * 首页。
 *
 * 结构是「hero + 行动磁贴 + 会话信息」三块：
 * - hero 只放定位与身份，不放操作，避免用户在一堆按钮里找入口；
 * - 磁贴按**角色可见性**过滤（体验层；服务端仍独立判定每次调用，前端隐藏不是授权）；
 * - 会话作用域单独放一张卡，因为它是最容易被误读的信息（"我看得到什么"）。
 */

type Entry = { to: string; label: string; hint: string; visible: boolean };

export function HomePage() {
  const session = useSession();
  const authenticated = session.status === "authenticated";

  const entries: Entry[] = [
    {
      to: "/assignments",
      label: t("nav.assignments"),
      hint: t("home.tile.assignments"),
      visible: true,
    },
    {
      to: "/questions",
      label: t("nav.questions"),
      hint: t("home.tile.questions"),
      visible: true,
    },
    {
      to: "/grades",
      label: t("nav.grades"),
      hint: t("home.tile.grades"),
      visible: true,
    },
    {
      to: "/roster",
      label: t("nav.roster"),
      hint: t("home.tile.roster"),
      visible: canViewRoster(session),
    },
    {
      to: "/roster/import",
      label: t("nav.rosterImport"),
      hint: t("home.tile.rosterImport"),
      visible: canImportRoster(session),
    },
    {
      to: "/admin/users",
      label: t("nav.users"),
      hint: t("home.tile.users"),
      visible: canManageUsers(session),
    },
  ];

  return (
    <div className="stack">
      <section className="hero">
        <p className="eyebrow">{t("app.subtitle")}</p>
        <h1 className="hero__title" data-testid="home-title">
          {authenticated ? t("home.welcome", { name: session.username ?? "-" }) : t("app.title")}
        </h1>
        <p className="hero__lead muted">
          {!authenticated
            ? t("home.signInHint")
            : session.rolesKnown
              ? t("home.roleHint", {
                  roles: session.roles.map((role) => t(`role.${role}`)).join(" / "),
                })
              : t("common.forbidden")}
        </p>
        {!authenticated ? (
          <div className="hero__actions">
            <Link className="button" to="/login">
              {t("login.submit")}
            </Link>
          </div>
        ) : null}
      </section>

      {authenticated ? (
        <section aria-label={t("nav.home")}>
          <div className="tiles">
            {entries
              .filter((entry) => entry.visible)
              .map((entry) => (
                <Link key={entry.to} to={entry.to} className="tile">
                  <span className="tile__label">{entry.label}</span>
                  <span className="tile__hint">{entry.hint}</span>
                  <span className="tile__chevron" aria-hidden="true">
                    ›
                  </span>
                </Link>
              ))}
          </div>
        </section>
      ) : null}

      <Panel title={t("home.sessionTitle")}>
        <dl className="facts">
          <div className="facts__row">
            <dt>{t("home.fact.identity")}</dt>
            <dd>
              {authenticated ? (session.username ?? "-") : t("common.none")}
            </dd>
          </div>
          <div className="facts__row">
            <dt>{t("home.fact.roles")}</dt>
            <dd>
              {session.rolesKnown && session.roles.length > 0
                ? session.roles.map((role) => t(`role.${role}`)).join(" / ")
                : t("common.none")}
            </dd>
          </div>
          <div className="facts__row">
            <dt>{t("home.fact.scope")}</dt>
            <dd>
              {session.scope.length === 0 ? (
                t("home.noScope")
              ) : (
                // 作用域是授权声明里的原始 UUID，不是给人读的标签：保留全文（可复制核对），
                // 但用标识符样式降低视觉权重。
                <span className="mono-id">
                  {session.scope
                    .map((item) =>
                      item.section_id
                        ? `${item.course_id} / ${item.section_id}`
                        : item.course_id,
                    )
                    .join("；")}
                </span>
              )}
            </dd>
          </div>
        </dl>
        {canSubmitAnswer(session) ? (
          <p className="muted">{t("home.submitHint")}</p>
        ) : null}
        {canExportGrades(session) ? <p className="muted">{t("grades.exportNoRebuild")}</p> : null}
      </Panel>
    </div>
  );
}
