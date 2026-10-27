import { useEffect, useRef, useState, useSyncExternalStore } from "react";
import { Link, Outlet, useNavigate, useRouterState } from "@tanstack/react-router";
import { useQueryClient } from "@tanstack/react-query";
import { t } from "../i18n";
import { useSession } from "../auth/sessionContext";
import {
  isSessionReady,
  signOut,
  subscribeSessionReady,
} from "../auth/sessionStore";
import {
  DEFAULT_AUTHENTICATED_PATH,
  currentLocationTarget,
  safeRedirectTarget,
} from "../auth/redirect";
import { AccountMenu } from "./AccountMenu";
import { AuthLayout } from "./AuthLayout";
import { isMainNavActive, mainNavItems, type MainNavItem } from "./navigation";
import { Drawer } from "../ui/components";

/** 不需要登录的路径：登录、注册与忘记密码是三个公开入口。 */
const PUBLIC_PATHS = new Set(["/login", "/register", "/forgot-password"]);

/**
 * 应用外壳（全站唯一）。
 *
 * 三层判断，顺序固定：
 * 1. **会话恢复完成前**不渲染任何业务内容（只显示骨架）——否则持有效 cookie 的用户会
 *    先看到一帧匿名外壳，或被误送到登录页。
 * 2. 公开页（登录/注册）使用独立认证布局；已登录用户访问公开页会被送回作业页，
 *    登录页上的 `?redirect=` 只接受 `safeRedirectTarget` 认可的站内路径。
 * 3. 受保护页：匿名用户带**原站内位置**去登录（深链不丢），登录后回到原处。
 *
 * 导航按角色过滤（见 `navigation.ts`）只是体验层；每个接口仍由服务端独立判权。
 */
export function AppShell() {
  const session = useSession();
  const location = useRouterState({ select: (state) => state.location });
  const navigate = useNavigate();
  const queryClient = useQueryClient();
  const ready = useSyncExternalStore(subscribeSessionReady, isSessionReady, isSessionReady);
  const [drawerOpen, setDrawerOpen] = useState(false);
  const [signingOut, setSigningOut] = useState(false);
  /** 主动退出后不要保留 redirect：用户要的是干净地回到登录页。 */
  const signingOutRef = useRef(false);
  const previousStatus = useRef(session.status);

  const authenticated = session.status === "authenticated";
  const isPublic = PUBLIC_PATHS.has(location.pathname);
  const redirectTarget = currentLocationTarget(location);
  const requestedRedirect = safeRedirectTarget(
    readRedirectSearch(location.search),
  );

  // 退出或会话失效后清空业务缓存：共用机器上不能把上一位用户的数据留给下一位。
  useEffect(() => {
    if (previousStatus.current === "authenticated" && session.status !== "authenticated") {
      queryClient.clear();
    }
    previousStatus.current = session.status;
  }, [session.status, queryClient]);

  useEffect(() => {
    if (isPublic) signingOutRef.current = false;
  }, [isPublic]);

  useEffect(() => {
    if (!ready) return;
    if (isPublic) {
      // 已登录用户不该停在登录/注册表单：回到原深链或作业页。
      if (authenticated) {
        const target = requestedRedirect ?? DEFAULT_AUTHENTICATED_PATH;
        void navigate({ href: target, replace: true });
      }
      return;
    }
    if (authenticated) return;
    // 主动退出：去一个干净的登录页，不把刚离开的受保护地址塞回 redirect。
    if (signingOutRef.current) {
      void navigate({ to: "/login", replace: true });
      return;
    }
    void navigate({
      to: "/login",
      search: { redirect: redirectTarget ?? undefined },
      replace: true,
    });
  }, [ready, isPublic, authenticated, requestedRedirect, redirectTarget, navigate]);

  if (!ready) return <BootScreen />;

  if (isPublic) {
    // 已登录用户在跳走的那一帧仍能看到产品标识与「返回作业」出口，而不是第二套登录表单。
    if (authenticated) {
      return (
        <AuthLayout>
          <p className="auth-redirect muted" role="status" data-testid="auth-redirecting">
            {t("nav.returningToAssignments")}
          </p>
        </AuthLayout>
      );
    }
    return (
      <AuthLayout>
        <Outlet />
      </AuthLayout>
    );
  }

  // 匿名用户：effect 正在把浏览器带到登录页；这里不渲染受保护 Outlet，避免闪一帧。
  if (!authenticated) return <BootScreen />;

  const items = mainNavItems(session);

  return (
    <div className="app-shell">
      <header className="app-header">
        <button
          type="button"
          className="icon-button app-header__menu"
          aria-label={t("nav.open")}
          aria-expanded={drawerOpen}
          aria-controls="main-nav-drawer"
          data-testid="nav-drawer-open"
          onClick={() => setDrawerOpen(true)}
        >
          <span className="menu-glyph" aria-hidden="true">
            <span />
            <span />
            <span />
          </span>
        </button>
        <Link to="/assignments" className="app-brand">
          {t("app.brand")}
        </Link>
        <div className="app-header__spacer" />
        <AccountMenu
          signingOut={signingOut}
          onSignOut={() => {
            if (signingOutRef.current) return;
            signingOutRef.current = true;
            setSigningOut(true);
            void signOut().finally(() => setSigningOut(false));
          }}
        />
      </header>

      <div className="app-body">
        <aside className="app-sidebar">
          <nav className="app-nav" aria-label={t("nav.mainLabel")}>
            <NavLinks items={items} pathname={location.pathname} testIdPrefix="nav" />
          </nav>
        </aside>
        <main className="app-main">
          <div className="app-content">
            <Outlet />
          </div>
        </main>
      </div>

      {drawerOpen ? (
        <Drawer
          title={t("app.brand")}
          testId="nav-drawer"
          onClose={() => setDrawerOpen(false)}
        >
          <nav
            className="app-nav app-nav--drawer"
            aria-label={t("nav.mainLabel")}
            id="main-nav-drawer"
          >
            <NavLinks
              items={items}
              pathname={location.pathname}
              testIdPrefix="drawer-nav"
              onSelect={() => setDrawerOpen(false)}
            />
          </nav>
        </Drawer>
      ) : null}
    </div>
  );
}

function NavLinks({
  items,
  pathname,
  testIdPrefix,
  onSelect,
}: {
  items: MainNavItem[];
  pathname: string;
  testIdPrefix: string;
  onSelect?: () => void;
}) {
  return (
    <ul className="app-nav__list">
      {items.map((item) => {
        const active = isMainNavActive(pathname, item.to);
        return (
          <li key={item.to}>
            <Link
              to={item.to}
              className={active ? "app-nav__link is-active" : "app-nav__link"}
              aria-current={active ? "page" : undefined}
              data-testid={`${testIdPrefix}-${item.to.slice(1)}`}
              onClick={onSelect}
            >
              {t(item.labelKey)}
            </Link>
          </li>
        );
      })}
    </ul>
  );
}

/** 会话恢复期间的骨架：不渲染任何导航、身份或业务内容。 */
function BootScreen() {
  return (
    <div className="boot" role="status" aria-live="polite" data-testid="boot-screen">
      <span className="boot__brand">{t("app.brand")}</span>
      <span className="boot__bar" aria-hidden="true" />
      <span className="boot__text muted">{t("boot.loading")}</span>
    </div>
  );
}

/** `location.search` 是已校验的查询对象；只取字符串 `redirect`，其余一律忽略。 */
function readRedirectSearch(search: unknown): unknown {
  if (typeof search !== "object" || search === null) return undefined;
  return (search as Record<string, unknown>).redirect;
}
