import { useLayoutEffect, useRef, useState } from "react";
import { t } from "../../i18n";
import { PageTitle, Tabs } from "../../ui/components";
import { CreateUserDrawer } from "../users/CreateUserDrawer";
import { UsersPage } from "../users/UsersPage";
import { RegistrationAllowlistPanel } from "./RegistrationAllowlistPanel";
import { normalizeAdminTab, type AdminTab } from "./tab";

/**
 * P14：管理员账户工作区（`/admin/users?tab=users|allowlist`）。
 *
 * 两个标签是同一件事的两半——"谁能登录"（用户与角色）与"谁可以自助注册"（注册名单）——
 * 因此共用一页、一个标题、一个管理员守卫，而不是两条彼此找不到的入口。
 *
 * 页面本身不持有任何写状态：创建 Drawer 在这里挂载（它的按钮属于页面标题行），确认框与
 * 表格都在各自的标签里。切换标签**只**切换视图：它不会提交、不会重发任何请求，
 * 也不会把未确认的文件选择变成上传。
 */
export function AdminUsersPage({
  tab,
  onTabChange,
}: {
  tab: AdminTab;
  onTabChange: (next: AdminTab) => void;
}) {
  const [createOpen, setCreateOpen] = useState(false);
  const [createdUserId, setCreatedUserId] = useState<string | null>(null);

  /**
   * 内容面板的滑动方向。
   *
   * 只看"上一次是哪个标签"：切换发生时才有方向，首次渲染不播放动画；浏览器前进/后退同样走
   * 这条判断，因此方向与真实切换一致。标签按钮本身完全不参与动画（它们的样式保持不变）。
   *   切到注册名单 → 内容自左向右滑入；切回用户 → 自右向左滑入。
   */
  const previousTab = useRef<AdminTab | null>(null);
  const slideDirection: "right" | "left" | null =
    previousTab.current === null || previousTab.current === tab
      ? null
      : tab === "allowlist"
        ? "right"
        : "left";
  useLayoutEffect(() => {
    previousTab.current = tab;
  }, [tab]);
  const panelClassName =
    slideDirection === null
      ? "admin-tab-panel"
      : `admin-tab-panel admin-tab-panel--slide-${slideDirection}`;

  const changeTab = (next: AdminTab) => {
    if (next === tab) return;
    // 切换标签丢弃"刚创建了某个账号"的临时提示：它属于用户标签的那一次操作，
    // 跟着跑到注册名单标签上会被读成名单被替换了。
    setCreatedUserId(null);
    setCreateOpen(false);
    onTabChange(next);
  };

  return (
    <div className="stack">
      <header className="panel__head">
        <PageTitle>{t("admin.usersTitle")}</PageTitle>
        {tab === "users" ? (
          <div className="panel__actions">
            <button
              type="button"
              className="button"
              data-testid="create-user"
              onClick={() => {
                setCreatedUserId(null);
                setCreateOpen(true);
              }}
            >
              {t("users.create")}
            </button>
          </div>
        ) : null}
      </header>

      <Tabs
        label={t("admin.usersTitle")}
        testId="admin-tabs"
        activeId={tab}
        onChange={(id) => changeTab(normalizeAdminTab(id))}
        tabs={[
          {
            id: "users",
            label: t("admin.tabUsers"),
            content: (
              <div key="users" className={panelClassName} data-testid="admin-tab-panel">
                <UsersPage
                  createdUserId={createdUserId}
                  onCreate={() => {
                    setCreatedUserId(null);
                    setCreateOpen(true);
                  }}
                />
              </div>
            ),
          },
          {
            id: "allowlist",
            label: t("admin.tabAllowlist"),
            content: (
              <div key="allowlist" className={panelClassName} data-testid="admin-tab-panel">
                <RegistrationAllowlistPanel />
              </div>
            ),
          },
        ]}
      />

      {createOpen ? (
        <CreateUserDrawer
          onClose={() => setCreateOpen(false)}
          onCreated={(userId) => {
            setCreateOpen(false);
            setCreatedUserId(userId);
          }}
        />
      ) : null}
    </div>
  );
}
