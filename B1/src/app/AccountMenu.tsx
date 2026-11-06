import { t } from "../i18n";
import { useSession } from "../auth/sessionContext";
import { GooeyButton } from "../ui/components";

/**
 * 右上角账户区：直接显示用户名，旁边就是退出登录按钮。
 *
 * 这里不再有下拉菜单，也没有悬停才出现的入口——退出是每个角色都要用的动作，
 * 不应该藏在第二层。管理员的「用户管理」已移到侧栏主导航，和其他页面入口一致。
 * 按钮用 gooey 样式（悬停时三团液滴涌上来），与导览页的「登录」保持同一种手感。
 */
export function AccountMenu({
  onSignOut,
  signingOut,
}: {
  onSignOut: () => void;
  signingOut: boolean;
}) {
  const session = useSession();

  return (
    <div className="account" data-testid="account-bar">
      <span className="account__name" data-testid="session-identity">
        {session.username ?? t("nav.accountLabel")}
      </span>
      <GooeyButton
        className="account__logout"
        data-testid="account-logout"
        disabled={signingOut}
        onClick={onSignOut}
      >
        {signingOut ? t("nav.loggingOut") : t("nav.logout")}
      </GooeyButton>
    </div>
  );
}
