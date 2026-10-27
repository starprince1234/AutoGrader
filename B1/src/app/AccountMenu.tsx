import { useEffect, useRef, useState } from "react";
import { t } from "../i18n";
import { useSession } from "../auth/sessionContext";
import type { SessionState } from "../auth/sessionStore";

/**
 * 右上角账户菜单。
 *
 * 它是身份区，不是第二个导航：业务入口（作业/题库/成绩/课程，管理员另有用户管理）都在侧栏，
 * 这里只保留身份与**账号级动作**——「退出登录」。契约版本、课程作用域 UUID、欢迎语都不属于这里。
 *
 * 这是一个 disclosure（按钮 + 面板），不是 ARIA menu：菜单项是普通按钮，
 * 因此 Tab 顺序天然正确，不需要为两三项再实现一套方向键语义。
 */
export function AccountMenu({
  onSignOut,
  signingOut,
}: {
  onSignOut: () => void;
  signingOut: boolean;
}) {
  const session = useSession();
  const [open, setOpen] = useState(false);
  const rootRef = useRef<HTMLDivElement>(null);
  const buttonRef = useRef<HTMLButtonElement>(null);

  useEffect(() => {
    if (!open) return;
    const onPointerDown = (event: PointerEvent) => {
      if (!rootRef.current?.contains(event.target as Node)) setOpen(false);
    };
    const onKeyDown = (event: KeyboardEvent) => {
      if (event.key !== "Escape") return;
      setOpen(false);
      buttonRef.current?.focus();
    };
    document.addEventListener("pointerdown", onPointerDown);
    document.addEventListener("keydown", onKeyDown);
    return () => {
      document.removeEventListener("pointerdown", onPointerDown);
      document.removeEventListener("keydown", onKeyDown);
    };
  }, [open]);

  return (
    <div className="account" ref={rootRef}>
      <button
        type="button"
        ref={buttonRef}
        className="account__button"
        aria-haspopup="true"
        aria-expanded={open}
        aria-controls="account-menu"
        aria-label={t("nav.account")}
        data-testid="account-menu-button"
        onClick={() => setOpen((current) => !current)}
      >
        <span className="account__name" data-testid="session-identity">
          {session.username ?? t("nav.accountLabel")}
        </span>
        <span className="account__role">{roleText(session)}</span>
      </button>
      {open ? (
        <div className="account__menu" id="account-menu" data-testid="account-menu">
          <div className="account__identity">
            <span className="account__identity-name">
              {session.username ?? t("nav.accountLabel")}
            </span>
            <span className="account__identity-role">{roleText(session)}</span>
          </div>
          <button
            type="button"
            className="account__item"
            data-testid="account-logout"
            disabled={signingOut}
            onClick={() => {
              setOpen(false);
              onSignOut();
            }}
          >
            {signingOut ? t("nav.loggingOut") : t("nav.logout")}
          </button>
        </div>
      ) : null}
    </div>
  );
}

/** 角色是解释"我为什么看到这些菜单"的唯一信息；未知角色如实说未知，不猜。 */
function roleText(session: SessionState): string {
  if (!session.rolesKnown || session.roles.length === 0) return t("nav.roleUnknown");
  return session.roles.map((role) => t(`role.${role}`)).join(" / ");
}
