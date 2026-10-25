import { useMemo, useState } from "react";
import { useQueryClient } from "@tanstack/react-query";
import { describeApiError, isApiError } from "../../api/problem";
import type { Role, UserSummary } from "../../api/types";
import { t } from "../../i18n";
import { signOut } from "../../auth/sessionStore";
import { Banner, ErrorState, Field, LoadMoreActions, Modal, Panel, Spinner, StatusPill, Table } from "../../ui/components";
import { formatDateTime, shortenId } from "../../ui/format";
import {
  ASSIGNABLE_ROLES,
  loadedUsers,
  orderedRoles,
  useDeactivateUser,
  useUpdateUserRoles,
  useUsers,
} from "./useUsers";

/**
 * 用户标签（P14 的一半：`GET/POST/PATCH/DELETE /api/v1/users`，仅管理员）。
 *
 * 页面只呈现事实，不替服务端断言结果：
 * - 列表里的"状态"直接来自 `UserSummary.is_active`（`DELETE` 是**软停用**，没有这个字段就只能
 *   把"点过按钮"当成"账号已停用"）；
 * - 改角色必须先比对旧/新角色并确认，且带上从该行读到的 `expected_updated_at`，让并发的另一位
 *   管理员得到 409 而不是被静默覆盖；
 * - 停用文案一律是"停用账号"：契约没有物理删除，写"删除"会让管理员以为历史成绩一起没了。
 *
 * 可见性不是授权：普通角色直接调用这些接口会得到 403，页面把它原样展示。
 */
export function UsersPage({
  onCreate,
  createdUserId = null,
}: {
  onCreate: () => void;
  /** 刚创建成功的账号 ID（由页面层传入）：用于区分"已创建"与"已创建但列表没刷出来"。 */
  createdUserId?: string | null;
}) {
  const [roleFilter, setRoleFilter] = useState<Role | "">("");
  const users = useUsers(roleFilter === "" ? null : roleFilter);
  const rows = useMemo(() => loadedUsers(users.data?.pages), [users.data]);
  const updateRoles = useUpdateUserRoles();
  const deactivate = useDeactivateUser();
  const queryClient = useQueryClient();

  const [notice, setNotice] = useState<string | null>(null);
  const [roleTargetId, setRoleTargetId] = useState<string | null>(null);
  const [nextRole, setNextRole] = useState<Role>("student");
  const [deactivateTargetId, setDeactivateTargetId] = useState<string | null>(null);
  const [detailTargetId, setDetailTargetId] = useState<string | null>(null);

  // 目标行从**当前列表数据**里取，而不是存一份快照：并发冲突后管理员点"重新加载"，
  // 弹窗里的旧/新角色与 `expected_updated_at` 必须一起变成服务端的最新值。
  const roleTarget = rows.find((row) => row.user_id === roleTargetId) ?? null;
  const deactivateTarget = rows.find((row) => row.user_id === deactivateTargetId) ?? null;
  const detailTarget = rows.find((row) => row.user_id === detailTargetId) ?? null;

  const closeRoleChange = () => {
    setRoleTargetId(null);
    updateRoles.reset();
  };

  const closeDeactivate = () => {
    setDeactivateTargetId(null);
    deactivate.reset();
  };

  const openRoleChange = (row: UserSummary) => {
    setNotice(null);
    updateRoles.reset();
    setNextRole(orderedRoles(row.roles)[0] ?? "student");
    setRoleTargetId(row.user_id);
  };

  const confirmRoleChange = () => {
    if (!roleTarget || updateRoles.isPending) return;
    const stamp = concurrencyStamp(roleTarget);
    // 没有并发字段就**不发**写请求：那会在对方已改动时静默覆盖。
    if (stamp === null) return;
    updateRoles.mutate(
      {
        userId: roleTarget.user_id,
        roles: [nextRole],
        expectedUpdatedAt: stamp,
      },
      {
        onSuccess: () => {
          setNotice(t("users.roleUpdated", { username: roleTarget.username }));
          setRoleTargetId(null);
        },
      },
    );
  };

  const confirmDeactivate = () => {
    if (!deactivateTarget || deactivate.isPending) return;
    // 未知状态不当作"可停用"：未知不是 false。
    if (accountState(deactivateTarget.is_active) !== "active") return;
    deactivate.mutate(
      { userId: deactivateTarget.user_id },
      {
        onSuccess: () => {
          setNotice(t("users.deactivated", { username: deactivateTarget.username }));
          setDeactivateTargetId(null);
        },
      },
    );
  };

  const columns = [
    {
      key: "display_name",
      header: t("users.displayName"),
      render: (row: UserSummary) => <span title={row.display_name}>{row.display_name}</span>,
    },
    {
      key: "username",
      header: t("users.username"),
      render: (row: UserSummary) => (
        <span className="mono-id" title={row.username}>
          {row.username}
        </span>
      ),
    },
    {
      key: "student_number",
      header: t("users.studentNumber"),
      render: (row: UserSummary) =>
        row.student_number === null || row.student_number === "" ? (
          <span className="muted">{t("users.studentNumberUnbound")}</span>
        ) : (
          <span className="mono-id">{row.student_number}</span>
        ),
    },
    {
      key: "roles",
      header: t("users.roles"),
      render: (row: UserSummary) => (
        <span className="inline">
          {orderedRoles(row.roles).length === 0 ? (
            <span className="muted">{t("common.none")}</span>
          ) : (
            orderedRoles(row.roles).map((role) => (
              <StatusPill key={role} tone="idle">
                {t(`role.${role}`)}
              </StatusPill>
            ))
          )}
        </span>
      ),
    },
    {
      key: "status",
      header: t("users.status"),
      render: (row: UserSummary) => {
        const state = accountState(row.is_active);
        return (
          <span data-testid={`user-status-${row.user_id}`}>
            <StatusPill tone={state === "active" ? "ok" : state === "inactive" ? "idle" : "warn"}>
              {state === "active"
                ? t("users.active")
                : state === "inactive"
                  ? t("users.inactive")
                  : t("users.statusUnknown")}
            </StatusPill>
          </span>
        );
      },
    },
    {
      key: "actions",
      header: t("users.actions"),
      render: (row: UserSummary) => {
        const state = accountState(row.is_active);
        const stamp = concurrencyStamp(row);
        return (
          <span className="row-actions">
            {stamp === null ? (
              // 缺少并发字段时不给"变更角色"按钮：发出去就是一次没有并发保护的写入。
              <span className="muted" data-testid={`role-blocked-${row.user_id}`}>
                {t("users.roleBlockedNoStamp")}
              </span>
            ) : (
              <button
                type="button"
                className="button button--ghost"
                data-testid={`change-role-${row.user_id}`}
                onClick={() => openRoleChange(row)}
              >
                {t("users.changeRole")}
              </button>
            )}
            {state === "active" ? (
              <button
                type="button"
                className="button button--ghost"
                data-testid={`deactivate-${row.user_id}`}
                onClick={() => {
                  setNotice(null);
                  deactivate.reset();
                  setDeactivateTargetId(row.user_id);
                }}
              >
                {t("users.deactivate")}
              </button>
            ) : (
              // 已停用的行不再给"停用"按钮：重复 DELETE 只会再写一条审计，不会让事实更清楚。
              // 状态未知的行同样不给：未知不代表可以停用。
              <span className="muted" data-testid={`deactivated-${row.user_id}`}>
                {state === "inactive" ? t("users.inactive") : t("users.statusUnknown")}
              </span>
            )}
            <button
              type="button"
              className="button button--ghost"
              data-testid={`detail-${row.user_id}`}
              onClick={() => setDetailTargetId(row.user_id)}
            >
              {t("users.detail")}
            </button>
          </span>
        );
      },
    },
  ];

  return (
    <div className="stack">
      <Panel title={t("users.filter")}>
        <p className="muted">{t("users.adminOnly")}</p>
        <div className="form-grid">
          <Field label={t("users.filterRole")} htmlFor="user-role-filter">
            <select
              id="user-role-filter"
              data-testid="user-role-filter"
              value={roleFilter}
              onChange={(event) => setRoleFilter(event.target.value as Role | "")}
            >
              <option value="">{t("users.allRoles")}</option>
              {ASSIGNABLE_ROLES.map((role) => (
                <option key={role} value={role}>
                  {t(`role.${role}`)}
                </option>
              ))}
            </select>
          </Field>
        </div>
        {createdUserId ? (
          users.isError ? (
            // 写请求只发生了一次（201 已返回）；读失败是另一件事，不能说成"创建失败"。
            <Banner tone="warn" testId="users-created-stale">
              {t("users.createdButListStale", { id: shortenId(createdUserId) })}
            </Banner>
          ) : (
            <Banner tone="success" testId="users-created">
              {t("users.createdWithId", { id: shortenId(createdUserId) })}
            </Banner>
          )
        ) : null}
        {notice ? (
          <Banner tone="success" testId="users-notice">
            {notice}
          </Banner>
        ) : null}
        {users.isError ? <UsersLoadError error={users.error} onRetry={() => void users.refetch()} /> : null}
      </Panel>

      <Panel
        title={t("users.countLoaded", { count: rows.length })}
        actions={
          <LoadMoreActions
            loadedCount={rows.length}
            hasNextPage={users.hasNextPage}
            isFetchingNextPage={users.isFetchingNextPage}
            onLoadMore={() => {
              void users.fetchNextPage();
            }}
            testId="users"
          />
        }
      >
        {users.isPending ? (
          <>
            <Spinner />
            {/* 首次加载只画表头骨架，并用**不同**的 testId：空表与"加载中"必须能被区分开。 */}
            <Table<UserSummary>
              testId="users-table-skeleton"
              label={t("users.countLoaded", { count: 0 })}
              rows={[]}
              getRowKey={(row) => row.user_id}
              columns={columns}
            />
          </>
        ) : users.isError && rows.length === 0 ? (
          // 读取失败已经在筛选面板里给出可读原因：这里不再画一张空表，空表会被读成"没有用户"。
          null
        ) : rows.length === 0 ? (
          roleFilter === "" ? (
            <div className="stack" data-testid="users-empty">
              <p className="muted">{t("users.empty")}</p>
              <p className="muted">{t("users.emptyHint")}</p>
              <div className="inline">
                <button type="button" className="button" onClick={onCreate}>
                  {t("users.create")}
                </button>
              </div>
            </div>
          ) : (
            <p className="muted" data-testid="users-no-results">
              {t("users.noResults")}
            </p>
          )
        ) : (
          <Table<UserSummary>
            testId="users-table"
            label={t("users.countLoaded", { count: rows.length })}
            rows={rows}
            getRowKey={(row) => row.user_id}
            columns={columns}
          />
        )}
      </Panel>

      {roleTarget ? (
        <Modal
          title={t("users.changeRoleTitle")}
          testId="role-change-modal"
          onClose={closeRoleChange}
        >
          <div className="stack">
            <p data-testid="role-change-target">
              {t("users.changeRoleBody", {
                name: roleTarget.display_name,
                username: roleTarget.username,
              })}
            </p>
            <p className="muted" data-testid="role-change-pair">
              {t("users.roleChangePair", {
                from: rolesText(roleTarget.roles),
                to: t(`role.${nextRole}`),
              })}
            </p>
            <Field label={t("users.newRole")} htmlFor="role-change-select">
              <select
                id="role-change-select"
                data-testid="role-change-select"
                value={nextRole}
                onChange={(event) => setNextRole(event.target.value as Role)}
              >
                {ASSIGNABLE_ROLES.map((role) => (
                  <option key={role} value={role}>
                    {t(`role.${role}`)}
                  </option>
                ))}
              </select>
            </Field>
            <p className="muted">{t("users.roleReplaceHint")}</p>
            <p className="muted">{t("users.concurrencyHint")}</p>

            {concurrencyStamp(roleTarget) === null ? (
              <Banner tone="error" testId="role-change-missing-stamp">
                {t("users.roleStampMissing")}
              </Banner>
            ) : null}

            {updateRoles.isError ? (
              isApiError(updateRoles.error) && updateRoles.error.isConflict ? (
                <Banner tone="warn" testId="role-change-conflict">
                  <p>{t("users.roleConflict")}</p>
                  <div className="inline">
                    <button
                      type="button"
                      className="button button--ghost"
                      data-testid="role-change-reload"
                      disabled={users.isFetching}
                      onClick={() => {
                        void queryClient.invalidateQueries({ queryKey: ["users"] });
                      }}
                    >
                      {t("users.reloadLatest")}
                    </button>
                    <span className="muted">{t("users.roleConflictReload")}</span>
                  </div>
                </Banner>
              ) : (
                <ErrorState error={updateRoles.error} testId="role-change-error" />
              )
            ) : null}

            <div className="inline">
              <button
                type="button"
                className="button"
                data-testid="role-change-confirm"
                disabled={
                  updateRoles.isPending ||
                  concurrencyStamp(roleTarget) === null ||
                  // 只在"角色集合不变"时禁用：多角色账号选一个已有角色，仍然是一次真实的收窄。
                  (orderedRoles(roleTarget.roles).length === 1 &&
                    orderedRoles(roleTarget.roles)[0] === nextRole)
                }
                onClick={confirmRoleChange}
              >
                {updateRoles.isPending ? t("users.saving") : t("users.confirmRoleChange")}
              </button>
              <button
                type="button"
                className="button button--ghost"
                data-testid="role-change-cancel"
                onClick={closeRoleChange}
              >
                {t("common.cancel")}
              </button>
            </div>
          </div>
        </Modal>
      ) : null}

      {deactivateTarget ? (
        <Modal
          title={t("users.deactivateTitle")}
          testId="deactivate-modal"
          onClose={closeDeactivate}
        >
          <div className="stack">
            <p data-testid="deactivate-target">
              {t("users.deactivateBody", {
                username: deactivateTarget.username,
                name: deactivateTarget.display_name,
              })}
            </p>
            <p className="muted">{t("users.deactivateKeepsData")}</p>
            {deactivate.isError ? (
              isApiError(deactivate.error) && deactivate.error.isConflict ? (
                <Banner tone="error" testId="deactivate-conflict">
                  <p>{t("users.deactivateConflict")}</p>
                  <p className="muted">{describeApiError(deactivate.error)}</p>
                </Banner>
              ) : (
                <ErrorState error={deactivate.error} testId="deactivate-error" />
              )
            ) : null}
            <div className="inline">
              <button
                type="button"
                className="button button--danger"
                data-testid="deactivate-confirm"
                disabled={
                  deactivate.isPending || accountState(deactivateTarget.is_active) !== "active"
                }
                onClick={confirmDeactivate}
              >
                {deactivate.isPending ? t("users.deactivating") : t("users.deactivate")}
              </button>
              <button
                type="button"
                className="button button--ghost"
                data-testid="deactivate-cancel"
                onClick={closeDeactivate}
              >
                {t("common.cancel")}
              </button>
            </div>
          </div>
        </Modal>
      ) : null}

      {detailTarget ? (
        <Modal
          title={t("users.detailTitle")}
          testId="user-detail-modal"
          onClose={() => setDetailTargetId(null)}
        >
          <div className="stack">
            <dl className="facts">
              <div className="facts__row">
                <dt>{t("users.displayName")}</dt>
                <dd>{detailTarget.display_name}</dd>
              </div>
              <div className="facts__row">
                <dt>{t("users.username")}</dt>
                <dd className="mono-id">{detailTarget.username}</dd>
              </div>
              <div className="facts__row">
                <dt>{t("users.studentNumber")}</dt>
                <dd>
                  {detailTarget.student_number ?? (
                    <span className="muted">{t("users.studentNumberUnbound")}</span>
                  )}
                </dd>
              </div>
              <div className="facts__row">
                <dt>{t("users.roles")}</dt>
                <dd>{rolesText(detailTarget.roles)}</dd>
              </div>
              <div className="facts__row">
                <dt>{t("users.status")}</dt>
                <dd>
                  {accountState(detailTarget.is_active) === "active"
                    ? t("users.active")
                    : accountState(detailTarget.is_active) === "inactive"
                      ? t("users.inactive")
                      : t("users.statusUnknown")}
                </dd>
              </div>
              <div className="facts__row">
                <dt>{t("users.createdAt")}</dt>
                <dd>{formatDateTime(detailTarget.created_at, t("common.none"))}</dd>
              </div>
              <div className="facts__row">
                <dt>{t("users.updatedAt")}</dt>
                <dd data-testid="user-updated-at">{formatDateTime(detailTarget.updated_at, t("common.none"))}</dd>
              </div>
              <div className="facts__row">
                <dt>{t("users.userId")}</dt>
                <dd className="mono-id" title={detailTarget.user_id}>
                  {shortenId(detailTarget.user_id)}
                </dd>
              </div>
            </dl>
            <p className="muted">{t("users.detailHint")}</p>
            <CopyUserId userId={detailTarget.user_id} />
          </div>
        </Modal>
      ) : null}
    </div>
  );
}

/** 角色列表的可读形式（与表格同一顺序、同一来源）。 */
function rolesText(roles: readonly Role[]): string {
  const ordered = orderedRoles(roles);
  if (ordered.length === 0) return t("common.none");
  return ordered.map((role) => t(`role.${role}`)).join(" / ");
}

/**
 * 账号状态的**三态**判定。
 *
 * `is_active` 是契约里的必需字段，但"接口没有返回它"与"账号已停用"是两件完全不同的事：
 * 前者是部署或契约不一致，后者是真实业务状态。把前者渲染成"已停用"会直接误导管理员去处理
 * 一个其实正常的账号，所以缺失时一律落到 `unknown`——未知、失败与已停用不可混淆。
 */
function accountState(value: unknown): "active" | "inactive" | "unknown" {
  if (value === true) return "active";
  if (value === false) return "inactive";
  return "unknown";
}

/**
 * 并发保护字段（`updated_at`）的可用值。
 *
 * 读不到它就**不能**发角色变更：`expected_updated_at` 是唯一防止"两位管理员互相静默覆盖"的
 * 手段，缺了它发出去的 PATCH 在对方已改动的情况下照样会成功。这里返回 `null` 让调用方
 * fail closed——与名单页"拿不到 `updated_at` 就禁止保存"是同一条规矩。
 */
function concurrencyStamp(user: UserSummary): string | null {
  const value: unknown = user.updated_at;
  return typeof value === "string" && value.trim() !== "" ? value : null;
}

/**
 * 复制完整用户 ID。
 *
 * 列表与详情默认只显示短 ID（长 UUID 会把表格撑宽）；需要核对对象时由管理员显式复制全文。
 * 剪贴板不可用（无权限、非安全上下文、jsdom）时**不假装成功**：给出可手动复制的文本框。
 */
function CopyUserId({ userId }: { userId: string }) {
  const [state, setState] = useState<"idle" | "ok" | "manual">("idle");

  const copy = () => {
    const clipboard = globalThis.navigator?.clipboard;
    if (!clipboard || typeof clipboard.writeText !== "function") {
      setState("manual");
      return;
    }
    clipboard.writeText(userId).then(
      () => setState("ok"),
      () => setState("manual"),
    );
  };

  return (
    <div className="stack">
      <div className="inline">
        <button type="button" className="button" data-testid="copy-user-id" onClick={copy}>
          {t("users.copyUserId")}
        </button>
        {state === "ok" ? (
          <span className="muted" role="status" data-testid="user-id-copied">
            {t("users.userIdCopied")}
          </span>
        ) : null}
      </div>
      {state === "manual" ? (
        <div>
          <p className="muted">{t("users.copyManual")}</p>
          <input
            className="mono-id"
            data-testid="user-id-manual"
            aria-label={t("users.copyUserId")}
            readOnly
            value={userId}
            onFocus={(event) => event.currentTarget.select()}
          />
        </div>
      ) : null}
    </div>
  );
}

/**
 * 读取失败按**语义**分开：401 是会话问题（去登录）、403 是权限问题（不是空列表）、
 * 其余保留筛选并允许明确重试。`users.isError` 之外的空表因此只可能是"真的没有数据"。
 */
function UsersLoadError({ error, onRetry }: { error: unknown; onRetry: () => void }) {
  if (isApiError(error) && error.isForbidden) {
    return (
      <Banner tone="error" testId="users-forbidden">
        {t("users.forbidden")}
      </Banner>
    );
  }
  if (isApiError(error) && error.isUnauthorized) {
    return (
      <Banner tone="error" testId="users-unauthorized">
        <p>{t("users.unauthorized")}</p>
        <div className="inline">
          <button
            type="button"
            className="button button--ghost"
            onClick={() => {
              void signOut();
            }}
          >
            {t("users.signInAgain")}
          </button>
        </div>
      </Banner>
    );
  }
  return (
    <div className="stack">
      <ErrorState error={error} testId="users-error" />
      <div className="inline">
        <button type="button" className="button" onClick={onRetry}>
          {t("common.retry")}
        </button>
      </div>
    </div>
  );
}
