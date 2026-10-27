import type { ReactNode } from "react";
import { Link } from "@tanstack/react-router";
import { t } from "../i18n";
import { useSession } from "../auth/sessionContext";

/**
 * 认证页外壳（登录/注册）。
 *
 * 公开页面不渲染应用的侧栏、账户菜单与退出按钮：一个已经登录的管理员在注册页看到
 * 自己的名字和"退出登录"，既泄露了身份也说不清这是谁的会话（线上曾如此）。
 * 页顶只有产品标识；「返回作业」只在确实已登录时出现（正常情况下 AppShell 会先
 * 把已登录用户送回作业页，这个链接是那一刻的出口）。
 */
export function AuthLayout({ children }: { children: ReactNode }) {
  const session = useSession();
  return (
    <div className="auth-page">
      <header className="auth-page__head">
        <span className="auth-brand">{t("app.brand")}</span>
        {session.status === "authenticated" ? (
          <Link to="/assignments" className="auth-page__back" data-testid="auth-back">
            {t("nav.returnToAssignments")}
          </Link>
        ) : null}
      </header>
      <main className="auth-page__main">{children}</main>
    </div>
  );
}
