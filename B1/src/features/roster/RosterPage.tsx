import { useMemo, useState } from "react";
import { Link } from "@tanstack/react-router";
import { t } from "../../i18n";
import type { EnrollmentPublic } from "../../api/types";
import { describeApiError, isApiError } from "../../api/problem";
import { useSession } from "../../auth/sessionContext";
import { canImportRoster } from "../../auth/visibility";
import { formatDateTime, shortenId } from "../../ui/format";
import {
  Banner,
  EmptyState,
  ErrorState,
  Field,
  LoadMoreActions,
  Modal,
  PageTitle,
  Panel,
  Spinner,
  Table,
} from "../../ui/components";
import { loadedCourses, useCourses, useSections } from "../../api/directory";
import {
  useDeleteEnrollment,
  useEnrollments,
  useUpdateEnrollment,
} from "./useEnrollments";

type EditState = {
  enrollmentId: string;
  displayName: string;
  studentNumber: string;
  expectedUpdatedAt: string;
  conflict: boolean;
};

/**
 * 名单读取与编辑（RQ-B-1-20）。
 *
 * 并发保护：契约用 `expected_updated_at` 做乐观锁，因此编辑必须基于**服务端返回的
 * `updated_at`**。服务端没给该字段时不做"尽力而为"的编辑——那等于静默覆盖他人修改，
 * 明确禁用保存并说明原因。
 *
 * 列表走游标分页：名单可能超过一页（`limit` 上限 200），面板标题显示已加载行数，
 * 未到末页时给出「加载更多」——只显示第一页会让后面的学生**静默消失**。
 */
export function RosterPage() {
  const session = useSession();
  const courses = useCourses();
  const courseOptions = loadedCourses(courses.data?.pages);
  const [courseId, setCourseId] = useState("");
  const [sectionId, setSectionId] = useState("");
  const [edit, setEdit] = useState<EditState | null>(null);
  const sections = useSections(courseId === "" ? null : courseId);
  const enrollments = useEnrollments(sectionId === "" ? null : sectionId);
  const updateEnrollment = useUpdateEnrollment(sectionId);
  const deleteEnrollment = useDeleteEnrollment(sectionId);

  const rows = useMemo(
    () => (enrollments.data?.pages ?? []).flatMap((page) => page.items),
    [enrollments.data],
  );

  return (
    <div className="stack">
      {/* 名单导入是课程页的次级动作：它不再占用主导航，但仍必须有真实入口。 */}
      <div className="inline">
        <PageTitle>{t("nav.roster")}</PageTitle>
        {canImportRoster(session) ? (
          <Link
            to="/roster/import"
            className="button button--ghost"
            data-testid="roster-import-link"
          >
            {t("import.title")}
          </Link>
        ) : null}
      </div>

      <Panel title={t("roster.filter")}>
        <div className="form-grid">
          <Field label={t("import.course")} htmlFor="roster-course">
            <select
              id="roster-course"
              value={courseId}
              onChange={(event) => {
                setCourseId(event.target.value);
                setSectionId("");
              }}
            >
              <option value="">{t("common.selectPrompt")}</option>
              {courseOptions.map((course) => (
                <option key={course.course_id} value={course.course_id}>
                  {course.course_code} · {course.title}
                </option>
              ))}
            </select>
          </Field>
          <Field label={t("import.section")} htmlFor="roster-section">
            <select
              id="roster-section"
              value={sectionId}
              onChange={(event) => setSectionId(event.target.value)}
              disabled={courseId === ""}
            >
              <option value="">{t("common.selectPrompt")}</option>
              {(sections.data ?? []).map((section) => (
                <option key={section.sectionId} value={section.sectionId}>
                  {section.label}
                </option>
              ))}
            </select>
          </Field>
        </div>
        <div className="inline">
          <span className="muted" data-testid="roster-courses-count">
            {t("common.coursesLoaded", { count: courseOptions.length })}
          </span>
          <LoadMoreActions
            loadedCount={courseOptions.length}
            hasNextPage={courses.hasNextPage}
            isFetchingNextPage={courses.isFetchingNextPage}
            onLoadMore={() => {
              void courses.fetchNextPage();
            }}
            testId="courses"
          />
        </div>

        <p className="muted">{t("roster.forbiddenHint")}</p>
        {courses.isError ? <ErrorState error={courses.error} /> : null}
        {sections.isError ? <ErrorState error={sections.error} /> : null}
      </Panel>

      {sectionId === "" ? (
        <EmptyState>{t("import.section")}</EmptyState>
      ) : enrollments.isPending ? (
        <Spinner />
      ) : enrollments.isError ? (
        <ErrorState error={enrollments.error} />
      ) : rows.length === 0 ? (
        <EmptyState />
      ) : (
        <Panel
          title={t("roster.countLoaded", { count: rows.length })}
          actions={
            <LoadMoreActions
              loadedCount={rows.length}
              hasNextPage={enrollments.hasNextPage}
              isFetchingNextPage={enrollments.isFetchingNextPage}
              onLoadMore={() => {
                void enrollments.fetchNextPage();
              }}
              testId="enrollments"
            />
          }
        >
          <Table<EnrollmentPublic>
            testId="enrollments-table"
            label={t("roster.countLoaded", { count: rows.length })}
            rows={rows}
            getRowKey={(row) => row.enrollment_id}
            columns={[
              {
                key: "student_number",
                header: t("roster.studentNumber"),
                render: (row) => row.student_number,
              },
              {
                key: "display_name",
                header: t("roster.displayName"),
                render: (row) => row.display_name,
              },
              {
                // 契约的 `EnrollmentPublic.student_id` 可空：名单里的学生可能还没绑定账号。
                // 空值如实显示"未绑定"，不拿 enrollment_id 或学号冒充用户 ID。
                key: "student_id",
                header: t("roster.studentId"),
                render: (row) =>
                  row.student_id ? (
                    <span className="mono-id" title={`学生 ID：${row.student_id}`}>
                      {shortenId(row.student_id)}
                    </span>
                  ) : (
                    <span className="muted" data-testid="student-unbound">
                      {t("roster.studentUnbound")}
                    </span>
                  ),
              },
              {
                key: "is_active",
                header: t("roster.isActive"),
                render: (row) => (row.is_active ? t("common.yes") : t("common.no")),
              },
              {
                key: "updated_at",
                header: t("roster.updatedAt"),
                render: (row) => formatDateTime(row.updated_at ?? null, t("common.none")),
              },
              {
                key: "__actions",
                header: "",
                render: (row) => {
                  const enrollmentId = row.enrollment_id;
                  const updatedAt = row.updated_at ?? null;
                  return (
                    <div className="row-actions">
                      <button
                        type="button"
                        className="button button--ghost"
                        disabled={!updatedAt}
                        title={updatedAt ? undefined : t("roster.missingUpdatedAt")}
                        onClick={() =>
                          setEdit({
                            enrollmentId,
                            displayName: row.display_name,
                            studentNumber: row.student_number,
                            expectedUpdatedAt: updatedAt ?? "",
                            conflict: false,
                          })
                        }
                      >
                        {t("roster.edit")}
                      </button>
                                            <button
                        type="button"
                        className="button button--danger"
                        disabled={deleteEnrollment.isPending}
                        onClick={() => {
                          const ok = window.confirm(
                            `${t("roster.remove")} ${row.student_number} ${row.display_name}？`,
                          );
                          if (ok) deleteEnrollment.mutate(enrollmentId);
                        }}
                      >
                        {t("roster.remove")}
                      </button>
                    </div>
                  );
                },
              },
            ]}
          />
          {!rows.every((row) => row.updated_at) ? (
            <Banner tone="warn" testId="missing-updated-at">
              {t("roster.missingUpdatedAt")}
            </Banner>
          ) : null}
          {updateEnrollment.isSuccess ? (
            <Banner tone="success">{t("roster.saved")}</Banner>
          ) : null}
          {deleteEnrollment.isSuccess ? (
            <Banner tone="success">{t("roster.removed")}</Banner>
          ) : null}
          {updateEnrollment.isError && !isApiError(updateEnrollment.error) ? (
            <ErrorState error={updateEnrollment.error} />
          ) : null}
          {deleteEnrollment.isError ? <ErrorState error={deleteEnrollment.error} /> : null}
        </Panel>
      )}

      {edit ? (
        <Modal
          title={t("roster.editTitle")}
          testId="enrollment-edit"
          onClose={() => setEdit(null)}
        >
          <form
            className="stack"
            onSubmit={(event) => {
              event.preventDefault();
              if (edit.expectedUpdatedAt === "") return;
              updateEnrollment.mutate(
                {
                  enrollmentId: edit.enrollmentId,
                  body: {
                    display_name: edit.displayName,
                    student_number: edit.studentNumber,
                    expected_updated_at: edit.expectedUpdatedAt,
                  },
                },
                {
                  onSuccess: () => setEdit(null),
                  onError: (error) => {
                    if (isApiError(error) && error.isConflict) {
                      setEdit((previous) => (previous ? { ...previous, conflict: true } : previous));
                    }
                  },
                },
              );
            }}
          >
            <Field label={t("roster.displayName")} htmlFor="edit-display-name">
              <input
                id="edit-display-name"
                value={edit.displayName}
                onChange={(event) =>
                  setEdit((previous) =>
                    previous ? { ...previous, displayName: event.target.value } : previous,
                  )
                }
              />
            </Field>
            <Field label={t("roster.studentNumber")} htmlFor="edit-student-number">
              <input
                id="edit-student-number"
                value={edit.studentNumber}
                onChange={(event) =>
                  setEdit((previous) =>
                    previous ? { ...previous, studentNumber: event.target.value } : previous,
                  )
                }
              />
            </Field>

            {edit.conflict ? (
              <Banner tone="error" testId="conflict-banner">
                <strong>{t("roster.conflict.title")}</strong>
                <p>{t("roster.conflict.body")}</p>
                <button
                  type="button"
                  className="button button--ghost"
                  onClick={() => {
                    setEdit(null);
                    void enrollments.refetch();
                  }}
                >
                  {t("roster.conflict.reload")}
                </button>
              </Banner>
            ) : null}

            {updateEnrollment.isError && !edit.conflict ? (
              <Banner tone="error">
                {describeApiError(updateEnrollment.error)}
              </Banner>
            ) : null}

            <div className="inline">
              <button
                type="submit"
                className="button"
                data-testid="save-enrollment"
                disabled={updateEnrollment.isPending || edit.expectedUpdatedAt === ""}
              >
                {t("common.save")}
              </button>
              <button
                type="button"
                className="button button--ghost"
                onClick={() => setEdit(null)}
              >
                {t("common.cancel")}
              </button>
            </div>
          </form>
        </Modal>
      ) : null}
    </div>
  );
}
