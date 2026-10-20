import { useEffect, useSyncExternalStore } from "react";
import { Link, Outlet, useNavigate, useRouterState } from "@tanstack/react-router";
import { t } from "../i18n";
import { useSession } from "../auth/sessionContext";
import {
  isSessionReady,
  signOut,
  subscribeSessionReady,
} from "../auth/sessionStore";
import {
  canAuthorQuestions,
  canImportRoster,
  canManageUsers,
  canReviewQuarantine,
  canViewQuestionQuality,
  canViewRoster,
} from "../auth/visibility";
import { CONTRACT_VERSION } from "./contractVersion";

type NavItem = { to: string; label: string; visible: boolean };

/** 不需要登录的路径：登录页与注册页是唯一的两个公开入口。 */
const PUBLIC_PATHS = new Set(["/login", "/register"]);

/**
 * 应用外壳：导航按**角色可见性**过滤（体验层，不是授权层）。
 *
 * 未登录**强制**跳转 `/login`（需求：只能从登录界面进入功能界面）。三条纪律：
 * 1. 只在会话恢复结束后判断，避免把持有有效 cookie 的用户误判为匿名；
 * 2. 跳转时带上 `redirect`，登录成功后回到原页，深链不丢失；
 * 3. 跳转期间**不渲染** `Outlet`，否则未授权页面会闪一帧。
 *
 * 这仍然不是安全边界：每个页面背后的 API 都会独立返回 401/403。
 */
export function AppShell() {
  const session = useSession();
  const pathname = useRouterState({ select: (state) => state.location.pathname });
  const navigate = useNavigate();
  const ready = useSyncExternalStore(subscribeSessionReady, isSessionReady, isSessionReady);

  const isPublic = PUBLIC_PATHS.has(pathname);
  const blocked = !isPublic && ready && session.status !== "authenticated";

  useEffect(() => {
    if (!blocked) return;
    void navigate({ to: "/login", search: { redirect: pathname }, replace: true });
  }, [blocked, pathname, navigate]);

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
    {
      to: "/admin/registration-allowlist",
      label: t("nav.registrationAllowlist"),
      visible: canManageUsers(session),
    },
  ];

  return (
    <div className="app">
      <header className="topbar">
        <span className="topbar__brand">{t("app.title")}</span>
        {isPublic ? null : (
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
        )}
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
        {blocked ? (
          <section className="panel" role="alert" data-testid="guard-redirecting">
            <h2 className="panel__title">{t("login.requiredTitle")}</h2>
            <p className="muted">{t("login.redirecting")}</p>
          </section>
        ) : (
          <Outlet />
        )}
      </main>
    </div>
  );
}
