import { Link, Outlet, useRouterState } from "@tanstack/react-router";
import { t } from "../i18n";
import { useSession } from "../auth/sessionContext";
import {
  canAuthorQuestions,
  canImportRoster,
  canManageUsers,
  canReviewQuarantine,
  canViewQuestionQuality,
  canViewRoster,
} from "../auth/visibility";
import { signOut } from "../auth/sessionStore";
import { CONTRACT_VERSION } from "./contractVersion";

type NavItem = { to: string; label: string; visible: boolean };

/** 应用外壳：导航按**角色可见性**过滤（体验层，不是授权层）。 */
export function AppShell() {
  const session = useSession();
  const pathname = useRouterState({ select: (state) => state.location.pathname });

  const items: NavItem[] = [
    { to: "/", label: t("nav.home"), visible: true },
    { to: "/assignments", label: t("nav.assignments"), visible: true },
    { to: "/questions", label: t("nav.questions"), visible: true },
    { to: "/questions/new", label: t("nav.authoring"), visible: canAuthorQuestions(session) },
    { to: "/grades", label: t("nav.grades"), visible: true },
    { to: "/roster", label: t("nav.roster"), visible: canViewRoster(session) },
    { to: "/roster/import", label: t("nav.rosterImport"), visible: canImportRoster(session) },
    {
      to: "/intake/quarantines",
      label: t("nav.quarantine"),
      visible: canReviewQuarantine(session),
    },
    { to: "/quality", label: t("nav.quality"), visible: canViewQuestionQuality(session) },
    { to: "/admin/users", label: t("nav.users"), visible: canManageUsers(session) },
  ];

  return (
    <div className="app">
      <header className="topbar">
        <span className="topbar__brand">{t("app.title")}</span>
        <nav className="topbar__nav" aria-label="主导航">
          {items
            .filter((item) => item.visible)
            .map((item) => (
              <Link
                key={item.to}
                to={item.to}
                className={pathname === item.to ? "is-active" : undefined}
              >
                {item.label}
              </Link>
            ))}
        </nav>
        <span className="topbar__spacer" />
        <span className="topbar__identity" data-testid="session-identity">
          {session.status === "authenticated" ? (
            <>
              {t("nav.signedInAs", { name: session.username ?? "-" })}
              {" · "}
              {session.rolesKnown
                ? session.roles.map((role) => t(`role.${role}`)).join("/")
                : t("common.none")}
              {" · "}
            </>
          ) : (
            <>
              {t("nav.anonymous")}
              {" · "}
            </>
          )}
          {t("app.contract", { version: CONTRACT_VERSION })}
        </span>
        {session.status === "authenticated" ? (
          <button
            type="button"
            className="button button--ghost"
            onClick={() => {
              void signOut();
            }}
          >
            {t("nav.logout")}
          </button>
        ) : null}
      </header>
      <main className="content">
        <Outlet />
      </main>
    </div>
  );
}
