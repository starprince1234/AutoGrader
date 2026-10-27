import { useMemo, useState } from "react";
import { useNavigate, useSearch } from "@tanstack/react-router";
import { t } from "../../i18n";
import type { EnrollmentPublic } from "../../api/types";
import { describeApiError, isApiError } from "../../api/problem";
import { useSession } from "../../auth/sessionContext";
import { canCreateCourse, canImportRoster } from "../../auth/visibility";
import {
  Banner,
  Drawer,
  EmptyState,
  ErrorState,
  Field,
  LoadMoreActions,
  Modal,
  PageTitle,
  Panel,
  Spinner,
  StatusPill,
  Table,
  Tabs,
} from "../../ui/components";
import {
  loadedCourses,
  useCourses,
  useCreateCourse,
  useCreateSection,
  useSections,
} from "../../api/directory";
import { RosterImportPanel } from "../roster-import/RosterImportPanel";
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

type RemoveState = { enrollmentId: string; displayName: string; studentNumber: string };

/**
 * 课程与名单工作区（P12）：`/roster?view=roster|import`。
 *
 * 三件事住在 URL 里，因此刷新、前进后退、复制链接都回到同一处状态：
 * `view`（名单 / 导入名单）、`course_id` + `section_id`（当前作用域）、`import_id`（导入任务）。
 * 组件 state 只保存“还没提交的输入”（编辑草稿、待确认的移出对象、待上传的文件）。
 *
 * 并发保护：编辑必须带服务端返回的 `updated_at` 作为 `expected_updated_at`；没给就禁用编辑，
 * 不做“尽力保存”。
 */
export function RosterPage() {
  const session = useSession();
  const search = useSearch({ from: "/roster" });
  const navigate = useNavigate({ from: "/roster" });

  const view = search.view === "import" ? "import" : "roster";
  const courseId = search.course_id ?? "";
  const sectionId = search.section_id ?? "";
  const importId = search.import_id ?? null;

  const courses = useCourses();
  const courseOptions = useMemo(() => loadedCourses(courses.data?.pages), [courses.data]);
  const sections = useSections(courseId === "" ? null : courseId);
  const enrollments = useEnrollments(sectionId === "" ? null : sectionId);
  const updateEnrollment = useUpdateEnrollment(sectionId);
  const deleteEnrollment = useDeleteEnrollment(sectionId);

  const [edit, setEdit] = useState<EditState | null>(null);
  const [removing, setRemoving] = useState<RemoveState | null>(null);
  const [courseDrawer, setCourseDrawer] = useState(false);
  const [sectionDrawer, setSectionDrawer] = useState(false);

  const rows = useMemo(
    () => (enrollments.data?.pages ?? []).flatMap((page) => page.items),
    [enrollments.data],
  );

  const setScope = (next: { course_id?: string; section_id?: string }) =>
    void navigate({
      search: (previous) => ({
        ...previous,
        ...next,
        // 换课程/换课序都会使旧导入任务失效：不把 A 课序的预览显示在 B 课序下。
        import_id: undefined,
      }),
    });

  const courseLabel =
    courseOptions.find((course) => course.course_id === courseId)?.title ?? courseId;
  const sectionText = sections.data?.find((option) => option.sectionId === sectionId)?.label ?? "";

  const rosterTab = (
    <div className="stack">
      {courseId === "" ? (
        <EmptyState>{t("roster.emptyNoCourse")}</EmptyState>
      ) : sections.isPending ? (
        <Spinner />
      ) : sections.isError ? (
        <ErrorState error={sections.error} />
      ) : (sections.data ?? []).length === 0 ? (
        <EmptyState>{t("roster.emptyNoSection")}</EmptyState>
      ) : sectionId === "" ? (
        <EmptyState>{t("roster.emptyNoSectionSelected")}</EmptyState>
      ) : enrollments.isPending ? (
        <Spinner label={t("roster.loading")} />
      ) : enrollments.isError ? (
        <ErrorState error={enrollments.error} testId="roster-error" />
      ) : rows.length === 0 ? (
        <EmptyState>{t("roster.emptySection")}</EmptyState>
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
                key: "display_name",
                header: t("roster.displayName"),
                render: (row) => row.display_name,
              },
              {
                key: "student_number",
                header: t("roster.studentNumber"),
                render: (row) => row.student_number,
              },
              {
                // 账号状态与名单状态是两件事：名单里有这个人 ≠ 他已经注册了账号。
                key: "account",
                header: t("roster.accountStatus"),
                render: (row) =>
                  row.student_id ? (
                    <StatusPill tone="ok">{t("roster.accountBound")}</StatusPill>
                  ) : (
                    <span className="muted" data-testid="student-unbound">
                      {t("roster.studentUnbound")}
                    </span>
                  ),
              },
              {
                key: "roster_status",
                header: t("roster.rosterStatus"),
                render: (row) => (
                  <StatusPill tone={row.is_active ? "ok" : "idle"}>
                    {row.is_active ? t("roster.statusActive") : t("roster.statusInactive")}
                  </StatusPill>
                ),
              },
              {
                key: "__actions",
                header: t("roster.actions"),
                render: (row) => {
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
                            enrollmentId: row.enrollment_id,
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
                        data-testid={`remove-${row.enrollment_id}`}
                        onClick={() =>
                          setRemoving({
                            enrollmentId: row.enrollment_id,
                            displayName: row.display_name,
                            studentNumber: row.student_number,
                          })
                        }
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
            <Banner tone="success" testId="roster-saved">
              {t("roster.saved")}
            </Banner>
          ) : null}
          {deleteEnrollment.isSuccess ? (
            <Banner tone="success" testId="roster-removed">
              {t("roster.removed")}
            </Banner>
          ) : null}
          {deleteEnrollment.isError ? <ErrorState error={deleteEnrollment.error} /> : null}
        </Panel>
      )}
    </div>
  );

  const importTab = <RosterImportPanel
    courseId={courseId}
    sectionId={sectionId}
    importId={importId}
    onImportId={(next) =>
      void navigate({ search: (previous) => ({ ...previous, import_id: next ?? undefined }) })
    }
  />;

  return (
    <div className="stack">
      <div className="inline">
        <PageTitle>{t("roster.workspaceTitle")}</PageTitle>
        {view === "roster" && canCreateCourse(session) ? (
          <>
            <button
              type="button"
              className="button button--ghost"
              data-testid="create-course-entry"
              onClick={() => setCourseDrawer(true)}
            >
              {t("roster.createCourse.entry")}
            </button>
            <button
              type="button"
              className="button button--ghost"
              data-testid="create-section-entry"
              disabled={courseId === ""}
              onClick={() => setSectionDrawer(true)}
            >
              {t("roster.createSection.entry")}
            </button>
          </>
        ) : null}
      </div>

      <Panel title={t("roster.filter")}>
        <div className="form-grid">
          <Field label={t("import.course")} htmlFor="roster-course">
            <select
              id="roster-course"
              value={courseId}
              onChange={(event) => setScope({ course_id: event.target.value, section_id: "" })}
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
              onChange={(event) => setScope({ section_id: event.target.value })}
              disabled={courseId === ""}
            >
              <option value="">{t("common.selectPrompt")}</option>
              {(sections.data ?? []).map((option) => (
                <option key={option.sectionId} value={option.sectionId}>
                  {option.label}
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
          {courseId !== "" && sectionText !== "" ? (
            <span className="muted" data-testid="roster-scope">
              {courseLabel} · {sectionText}
            </span>
          ) : null}
        </div>
        {courses.isError ? <ErrorState error={courses.error} /> : null}
        {sections.isError ? <ErrorState error={sections.error} /> : null}
      </Panel>

      <Tabs
        label={t("roster.tabs.label")}
        testId="roster-tabs"
        activeId={view}
        onChange={(id) =>
          void navigate({ search: (previous) => ({ ...previous, view: id as "roster" | "import" }) })
        }
        tabs={[
          { id: "roster", label: t("roster.tabs.roster"), content: rosterTab },
          ...(canImportRoster(session)
            ? [{ id: "import", label: t("roster.tabs.import"), content: importTab }]
            : []),
        ]}
      />

      {courseDrawer ? (
        <CreateCourseDrawer
          onClose={() => setCourseDrawer(false)}
          onCreated={(course) => {
            setCourseDrawer(false);
            setScope({ course_id: course.course_id, section_id: "" });
          }}
        />
      ) : null}

      {sectionDrawer ? (
        <CreateSectionDrawer
          courseId={courseId}
          onClose={() => setSectionDrawer(false)}
          onCreated={(section) => {
            setSectionDrawer(false);
            setScope({ section_id: section.section_id });
          }}
        />
      ) : null}

      {removing ? (
        <Modal
          title={t("roster.removeTitle")}
          testId="remove-enrollment"
          onClose={() => setRemoving(null)}
        >
          <div className="stack">
            <p>
              {t("roster.removeConfirm", {
                name: removing.displayName,
                number: removing.studentNumber,
              })}
            </p>
            <p className="muted">{t("roster.removeHint")}</p>
            <div className="inline">
              <button
                type="button"
                className="button button--ghost"
                onClick={() => setRemoving(null)}
              >
                {t("common.cancel")}
              </button>
              <button
                type="button"
                className="button button--danger"
                data-testid="confirm-remove"
                disabled={deleteEnrollment.isPending}
                onClick={() =>
                  deleteEnrollment.mutate(removing.enrollmentId, {
                    onSuccess: () => setRemoving(null),
                  })
                }
              >
                {t("roster.remove")}
              </button>
            </div>
          </div>
        </Modal>
      ) : null}

      {edit ? (
        <Modal title={t("roster.editTitle")} testId="enrollment-edit" onClose={() => setEdit(null)}>
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
              <Banner tone="error">{describeApiError(updateEnrollment.error)}</Banner>
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
              <button type="button" className="button button--ghost" onClick={() => setEdit(null)}>
                {t("common.cancel")}
              </button>
            </div>
          </form>
        </Modal>
      ) : null}
    </div>
  );
}

function CreateCourseDrawer({
  onClose,
  onCreated,
}: {
  onClose: () => void;
  onCreated: (course: { course_id: string; course_code: string; title: string }) => void;
}) {
  const [code, setCode] = useState("");
  const [title, setTitle] = useState("");
  const [error, setError] = useState<string | null>(null);
  const create = useCreateCourse();

  return (
    <Drawer title={t("roster.createCourse.entry")} onClose={onClose} testId="create-course">
      <form
        className="stack"
        onSubmit={(event) => {
          event.preventDefault();
          if (code.trim() === "") {
            setError(t("roster.createCourse.codeRequired"));
            return;
          }
          if (title.trim() === "") {
            setError(t("roster.createCourse.titleRequired"));
            return;
          }
          setError(null);
          create.mutate(
            { course_code: code.trim(), title: title.trim() },
            {
              onSuccess: (created) => onCreated(created),
              onError: (failure) => {
                if (isApiError(failure) && failure.status === 409) {
                  setError(t("roster.createCourse.duplicate"));
                  return;
                }
                setError(t("common.requestFailed"));
              },
            },
          );
        }}
      >
        <Field label={t("roster.createCourse.code")} htmlFor="course-code">
          <input
            id="course-code"
            value={code}
            maxLength={64}
            onChange={(event) => setCode(event.target.value)}
          />
        </Field>
        <Field label={t("roster.createCourse.title")} htmlFor="course-title">
          <input
            id="course-title"
            value={title}
            maxLength={200}
            onChange={(event) => setTitle(event.target.value)}
          />
        </Field>
        {error ? (
          <Banner tone="error" testId="create-course-error">
            {error}
          </Banner>
        ) : null}
        <div className="inline">
          <button type="submit" className="button" disabled={create.isPending}>
            {create.isPending ? t("roster.createCourse.saving") : t("roster.createCourse.submit")}
          </button>
          <button type="button" className="button button--ghost" onClick={onClose}>
            {t("common.cancel")}
          </button>
        </div>
      </form>
    </Drawer>
  );
}

function CreateSectionDrawer({
  courseId,
  onClose,
  onCreated,
}: {
  courseId: string;
  onClose: () => void;
  onCreated: (section: { section_id: string }) => void;
}) {
  const [code, setCode] = useState("");
  const [term, setTerm] = useState("");
  const [error, setError] = useState<string | null>(null);
  const create = useCreateSection(courseId);

  return (
    <Drawer title={t("roster.createSection.entry")} onClose={onClose} testId="create-section">
      <form
        className="stack"
        onSubmit={(event) => {
          event.preventDefault();
          if (code.trim() === "") {
            setError(t("roster.createSection.codeRequired"));
            return;
          }
          setError(null);
          create.mutate(
            { section_code: code.trim(), term: term.trim() === "" ? null : term.trim() },
            {
              onSuccess: (created) => onCreated(created),
              onError: (failure) => {
                if (isApiError(failure) && failure.status === 409) {
                  setError(t("roster.createSection.duplicate"));
                  return;
                }
                setError(t("common.requestFailed"));
              },
            },
          );
        }}
      >
        <Field label={t("roster.createSection.code")} htmlFor="section-code">
          <input
            id="section-code"
            value={code}
            maxLength={64}
            onChange={(event) => setCode(event.target.value)}
          />
        </Field>
        <Field label={t("roster.createSection.term")} htmlFor="section-term">
          <input
            id="section-term"
            value={term}
            maxLength={64}
            onChange={(event) => setTerm(event.target.value)}
          />
        </Field>
        {error ? (
          <Banner tone="error" testId="create-section-error">
            {error}
          </Banner>
        ) : null}
        <div className="inline">
          <button type="submit" className="button" disabled={create.isPending}>
            {create.isPending
              ? t("roster.createSection.saving")
              : t("roster.createSection.submit")}
          </button>
          <button type="button" className="button button--ghost" onClick={onClose}>
            {t("common.cancel")}
          </button>
        </div>
      </form>
    </Drawer>
  );
}

