import { useMemo, useState } from "react";
import { t } from "../../i18n";
import type { Role, UserSummary } from "../../api/types";
import { isApiError } from "../../api/problem";
import {
  Banner,
  ErrorState,
  Field,
  LoadMoreActions,
  PageTitle,
  Panel,
  Spinner,
  Table,
} from "../../ui/components";
import {
  ASSIGNABLE_ROLES,
  loadedUsers,
  useCreateUser,
  useUpdateUser,
  useUsers,
} from "./useUsers";

/**
 * 用户与角色界面（RQ-B-1-20 的角色维度）。
 *
 * 该页面只对管理员可见（`canManageUsers`），但可见性不是授权：普通角色直接调用
 * `GET/POST /api/v1/users` 会得到 403，页面把它原样展示出来。
 *
 * 列表走游标分页：账号超过一页时未到末页必须给出「加载更多」，否则管理员会把"没显示"
 * 当成"不存在"。
 */
export function UsersPage() {
  const [roleFilter, setRoleFilter] = useState<Role | "">("");
  const users = useUsers(roleFilter === "" ? null : roleFilter);
  const rows = useMemo(() => loadedUsers(users.data?.pages), [users.data]);
  const createUser = useCreateUser();
  const updateUser = useUpdateUser();

  const [form, setForm] = useState({
    displayName: "",
    username: "",
    studentNumber: "",
    role: "student" as Role,
    password: "",
  });
  const [conflictUserId, setConflictUserId] = useState<string | null>(null);

  return (
    <div className="stack">
      <PageTitle>{t("nav.users")}</PageTitle>

      <Panel title={t("users.filter")}>
        <p className="muted">{t("users.adminOnly")}</p>

        <div className="form-grid">
          <Field label={t("users.filterRole")} htmlFor="user-role-filter">
            <select
              id="user-role-filter"
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

        {users.isError ? <ErrorState error={users.error} /> : null}
      </Panel>

      <Panel title={t("users.create")}>
        <form
          className="form-grid"
          data-testid="create-user-form"
          onSubmit={(event) => {
            event.preventDefault();
            createUser.mutate({
              display_name: form.displayName,
              username: form.username,
              student_number: form.studentNumber === "" ? null : form.studentNumber,
              roles: [form.role],
              password: form.password,
            });
          }}
        >
          <Field label={t("users.displayName")} htmlFor="new-display-name">
            <input
              id="new-display-name"
              value={form.displayName}
              onChange={(event) => setForm({ ...form, displayName: event.target.value })}
            />
          </Field>
          <Field label={t("users.username")} htmlFor="new-username">
            <input
              id="new-username"
              value={form.username}
              onChange={(event) => setForm({ ...form, username: event.target.value })}
            />
          </Field>
          <Field label={t("users.studentNumber")} htmlFor="new-student-number">
            <input
              id="new-student-number"
              value={form.studentNumber}
              onChange={(event) => setForm({ ...form, studentNumber: event.target.value })}
            />
          </Field>
          <Field label={t("users.roles")} htmlFor="new-role">
            <select
              id="new-role"
              value={form.role}
              onChange={(event) => setForm({ ...form, role: event.target.value as Role })}
            >
              {ASSIGNABLE_ROLES.map((role) => (
                <option key={role} value={role}>
                  {t(`role.${role}`)}
                </option>
              ))}
            </select>
          </Field>
          <Field label={t("login.password")} htmlFor="new-password">
            <input
              id="new-password"
              type="password"
              value={form.password}
              onChange={(event) => setForm({ ...form, password: event.target.value })}
            />
          </Field>
          <div className="inline">
            <button
              type="submit"
              className="button"
              disabled={
                createUser.isPending ||
                form.displayName === "" ||
                form.username === "" ||
                form.password === ""
              }
            >
              {createUser.isPending ? t("users.creating") : t("users.create")}
            </button>
          </div>
        </form>
        {createUser.isSuccess ? <Banner tone="success">{t("users.created")}</Banner> : null}
        {createUser.isError ? <ErrorState error={createUser.error} /> : null}
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
          <Spinner />
        ) : (
          <Table<UserSummary>
            testId="users-table"
            label={t("users.countLoaded", { count: rows.length })}
            rows={rows}
            getRowKey={(row) => row.user_id}
            columns={[
              { key: "display_name", header: t("users.displayName"), render: (row) => row.display_name },
              { key: "username", header: t("users.username"), render: (row) => row.username },
              {
                key: "user_id",
                header: t("users.userId"),
                render: (row) => <span className="mono-id">{row.user_id}</span>,
              },
              {
                key: "student_number",
                header: t("users.studentNumber"),
                render: (row) => row.student_number ?? t("common.none"),
              },
              {
                key: "roles",
                header: t("users.roles"),
                render: (row) => (
                  <select
                    aria-label={`${row.display_name} ${t("users.roles")}`}
                    value={row.roles[0] ?? "student"}
                    onChange={(event) => {
                      setConflictUserId(null);
                      updateUser.mutate(
                        { userId: row.user_id, body: { roles: [event.target.value as Role] } },
                        {
                          onError: (error) => {
                            if (isApiError(error) && error.isConflict) {
                              setConflictUserId(row.user_id);
                            }
                          },
                        },
                      );
                    }}
                  >
                    {ASSIGNABLE_ROLES.map((role) => (
                      <option key={role} value={role}>
                        {t(`role.${role}`)}
                      </option>
                    ))}
                  </select>
                ),
              },
            ]}
          />
        )}
        {updateUser.isSuccess ? <Banner tone="success">{t("users.updated")}</Banner> : null}
        {conflictUserId ? (
          <Banner tone="error" testId="user-conflict">
            {t("users.conflict")}
          </Banner>
        ) : null}
        {updateUser.isError && !conflictUserId ? <ErrorState error={updateUser.error} /> : null}
      </Panel>
    </div>
  );
}
