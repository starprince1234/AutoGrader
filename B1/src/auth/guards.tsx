import type { ReactNode } from "react";
import { t } from "../i18n";
import type { Role } from "../api/types";
import { useSession } from "./sessionContext";
import type { SessionState } from "./sessionStore";

/**
 * 可见性守卫（体验层）。服务端授权才是判定方：被拒绝的请求必须原样展示 403，
 * 不能因为入口被隐藏就当作"没有发生"。
 *
 * 守卫刻意**不含跳转**（`RequireAuth` 不自动去登录页、`RequirePermission` 不放返回链接）：
 * - 未登录时跳转会让 `?version=` 之类的深链丢失，用户登录后回不到原处；
 * - 拒绝页不需要"下一步"动作，主导航始终在页面上方。
 * 附带的好处是守卫不依赖路由上下文，可以被单独渲染与断言。
 */

export function RequireAuth({ children }: { children: ReactNode }) {
  const session = useSession();
  if (session.status !== "authenticated") {
    return (
      <section className="panel" role="alert" data-testid="guard-unauthenticated">
        <h2 className="panel__title">{t("login.title")}</h2>
        <p className="muted">{t("login.required")}</p>
      </section>
    );
  }
  return <>{children}</>;
}

export function RequirePermission({
  allow,
  allowedRoles,
  children,
}: {
  allow: (session: SessionState) => boolean;
  /** Roles that may enter. Rendered so the refusal says what *would* be allowed. */
  allowedRoles?: Role[];
  children: ReactNode;
}) {
  const session = useSession();
  if (!allow(session)) {
    return (
      <section className="panel" role="alert" data-testid="guard-forbidden">
        <h2 className="panel__title">{t("common.forbiddenTitle")}</h2>
        <p className="muted">{t("common.forbidden")}</p>
        {allowedRoles && allowedRoles.length > 0 ? (
          <p className="muted">
            {t("common.forbiddenRoles", {
              roles: allowedRoles.map((role) => t(`role.${role}`)).join(" / "),
            })}
          </p>
        ) : null}
      </section>
    );
  }
  return <>{children}</>;
}
