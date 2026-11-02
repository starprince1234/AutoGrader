import { useMemo, useState } from "react";
import { useNavigate, useSearch } from "@tanstack/react-router";
import { t } from "../../i18n";
import type { CoursePublic, EnrollmentPublic } from "../../api/types";
import { describeApiError, isApiError } from "../../api/problem";
import { useSession } from "../../auth/sessionContext";
import { canCreateCourse, canImportRoster } from "../../auth/visibility";
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
  StatusPill,
  Table,
  Tabs,
} from "../../ui/components";
import {
  loadedCourses,
  useCourses,
  useCreateCourse,
  useRenameCourse,
  useSections,
} from "../../api/directory";
import { RosterImportPanel } from "../roster-import/RosterImportPanel";
import { useCreateRosterImport } from "../roster-import/useRosterImport";
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
  // 重命名课程：选中课程后可用；标题按当前值预填，保存时带上它的 updated_at 作为乐观锁。
  const [renameOpen, setRenameOpen] = useState(false);
  const [renameTarget, setRenameTarget] = useState<CoursePublic | null>(null);
  const [renameTitle, setRenameTitle] = useState("");
  const [renameCode, setRenameCode] = useState("");
  const renameCourse = useRenameCourse();

  const [createOpen, setCreateOpen] = useState(false);
  const [createTitle, setCreateTitle] = useState("");
  const [createCode, setCreateCode] = useState("");
  const createCourse = useCreateCourse();

  // 导入名单：先选已注册的课序（班级），再上传文件；上传后进入既有的"映射→确认"流程。
  const [importOpen, setImportOpen] = useState(false);
  const [importTarget, setImportTarget] = useState<CoursePublic | null>(null);
  const [importSectionId, setImportSectionId] = useState("");
  const [importFile, setImportFile] = useState<File | null>(null);
  const importSections = useSections(importTarget ? importTarget.course_id : null);
  const createImport = useCreateRosterImport();
  const courseOptions = useMemo(() => loadedCourses(courses.data?.pages), [courses.data]);
  const sections = useSections(courseId === "" ? null : courseId);
  const enrollments = useEnrollments(sectionId === "" ? null : sectionId);
  const updateEnrollment = useUpdateEnrollment(sectionId);
  const deleteEnrollment = useDeleteEnrollment(sectionId);

  const [edit, setEdit] = useState<EditState | null>(null);
  const [removing, setRemoving] = useState<RemoveState | null>(null);

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
        {canCreateCourse(session) ? (
          <button
            type="button"
            className="button button--primary"
            data-testid="create-course-entry"
            onClick={() => {
              setCreateTitle("");
              setCreateCode("");
              setCreateOpen(true);
            }}
          >
            {t("roster.create.open")}
          </button>
        ) : null}
      </div>

      <Panel
        title={t("roster.coursesTitle")}
        actions={
          <LoadMoreActions
            loadedCount={courseOptions.length}
            hasNextPage={courses.hasNextPage}
            isFetchingNextPage={courses.isFetchingNextPage}
            onLoadMore={() => {
              void courses.fetchNextPage();
            }}
            testId="courses"
          />
        }
      >
        {courses.isPending ? (
          <Spinner />
        ) : courses.isError ? (
          <ErrorState error={courses.error} />
        ) : courseOptions.length === 0 ? (
          <EmptyState>{t("roster.emptyNoCourse")}</EmptyState>
        ) : (
          <Table<CoursePublic>
            testId="courses-table"
            label={t("roster.coursesTitle")}
            rows={courseOptions}
            getRowKey={(row) => row.course_id}
            columns={[
              {
                key: "title",
                header: t("roster.column.title"),
                render: (row) => (
                  <button
                    type="button"
                    className="button button--ghost"
                    data-testid={`course-open-${row.course_id}`}
                    onClick={() => setScope({ course_id: row.course_id, section_id: "" })}
                  >
                    {row.title}
                  </button>
                ),
              },
              {
                key: "code",
                header: t("roster.column.code"),
                render: (row) => row.course_code ?? t("roster.column.noCode"),
              },
              {
                key: "sections",
                header: t("roster.column.classes"),
                render: (row) => (
                  <span data-testid={`course-classes-${row.course_id}`}>{row.section_count}</span>
                ),
              },
              {
                key: "actions",
                header: t("roster.column.actions"),
                render: (row) => (
                  <span className="inline">
                    <button
                      type="button"
                      className="button button--ghost"
                      data-testid={`course-rename-${row.course_id}`}
                      onClick={() => {
                        setRenameTarget(row);
                        setRenameTitle(row.title);
                        setRenameCode(row.course_code ?? "");
                        setRenameOpen(true);
                      }}
                    >
                      {t("roster.rename.entry")}
                    </button>
                    <button
                      type="button"
                      className="button button--ghost"
                      data-testid={`course-import-${row.course_id}`}
                      onClick={() => {
                        setImportTarget(row);
                        setImportSectionId("");
                        setImportFile(null);
                        setScope({ course_id: row.course_id, section_id: "" });
                        setImportOpen(true);
                      }}
                    >
                      {t("roster.import.entry")}
                    </button>
                  </span>
                ),
              },
            ]}
          />
        )}
        {courseId !== "" ? (
          <p className="muted" data-testid="roster-scope">
            {courseLabel}
            {sectionText === "" ? "" : ` · ${sectionText}`}
          </p>
        ) : null}
      </Panel>

      <Panel title={t("roster.filter")}>
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

      {createOpen ? (
        <Modal
          title={t("roster.create.title")}
          testId="create-course"
          onClose={() => setCreateOpen(false)}
        >
          <form
            className="stack"
            onSubmit={(event) => {
              event.preventDefault();
              if (createTitle.trim() === "" || createCode.trim() === "") {
                return;
              }
              createCourse.mutate(
                { title: createTitle.trim(), course_code: createCode.trim() },
                {
                  onSuccess: (created) => {
                    setCreateOpen(false);
                    setScope({ course_id: created.course_id, section_id: "" });
                  },
                },
              );
            }}
          >
            <Field label={t("roster.create.titleLabel")} htmlFor="create-course-title">
              <input
                id="create-course-title"
                value={createTitle}
                data-testid="create-course-title"
                onChange={(event) => setCreateTitle(event.target.value)}
              />
            </Field>
            <Field label={t("roster.create.codeLabel")} htmlFor="create-course-code">
              <input
                id="create-course-code"
                value={createCode}
                data-testid="create-course-code"
                onChange={(event) => setCreateCode(event.target.value)}
              />
            </Field>
            {createCourse.isError ? (
              <p className="form-error" data-testid="create-course-error">
                {isApiError(createCourse.error) && createCourse.error.status === 409
                  ? t("roster.create.duplicate")
                  : t("common.requestFailed")}
              </p>
            ) : null}
            <div className="inline">
              <button
                type="submit"
                className="button button--primary"
                data-testid="create-course-save"
                disabled={
                  createTitle.trim() === "" || createCode.trim() === "" || createCourse.isPending
                }
              >
                {t("roster.create.submit")}
              </button>
              <button
                type="button"
                className="button button--ghost"
                onClick={() => setCreateOpen(false)}
              >
                {t("common.cancel")}
              </button>
            </div>
          </form>
        </Modal>
      ) : null}

      {renameOpen && renameTarget ? (
        <Modal
          title={t("roster.rename.title")}
          testId="rename-course"
          onClose={() => setRenameOpen(false)}
        >
          <form
            className="stack"
            onSubmit={(event) => {
              event.preventDefault();
              if (renameTitle.trim() === "") {
                return;
              }
              renameCourse.mutate(
                {
                  courseId: renameTarget.course_id,
                  title: renameTitle.trim(),
                  courseCode: renameCode.trim() === "" ? null : renameCode.trim(),
                  expectedUpdatedAt: renameTarget.updated_at,
                },
                { onSuccess: () => setRenameOpen(false) },
              );
            }}
          >
            <Field label={t("roster.rename.titleLabel")} htmlFor="rename-course-title">
              <input
                id="rename-course-title"
                value={renameTitle}
                data-testid="rename-course-title"
                onChange={(event) => setRenameTitle(event.target.value)}
              />
            </Field>
            <Field label={t("roster.rename.codeLabel")} htmlFor="rename-course-code">
              <input
                id="rename-course-code"
                value={renameCode}
                data-testid="rename-course-code"
                onChange={(event) => setRenameCode(event.target.value)}
              />
            </Field>
            <p className="muted">{t("roster.rename.hint")}</p>
            {renameCourse.isError ? (
              <p className="form-error" data-testid="rename-course-error">
                {t("roster.rename.failed")}
              </p>
            ) : null}
            <div className="inline">
              <button
                type="submit"
                className="button button--primary"
                data-testid="rename-course-save"
                disabled={renameTitle.trim() === "" || renameCourse.isPending}
              >
                {t("roster.rename.save")}
              </button>
              <button
                type="button"
                className="button button--ghost"
                onClick={() => setRenameOpen(false)}
              >
                {t("common.cancel")}
              </button>
            </div>
          </form>
        </Modal>
      ) : null}

      {importOpen && importTarget ? (
        <Modal
          title={t("roster.import.title")}
          testId="import-roster"
          onClose={() => setImportOpen(false)}
        >
          <form
            className="stack"
            onSubmit={(event) => {
              event.preventDefault();
              if (!importFile || importSectionId === "") {
                return;
              }
              createImport.mutate(
                {
                  file: importFile,
                  courseId: importTarget.course_id,
                  sectionId: importSectionId,
                },
                {
                  onSuccess: (preview) => {
                    setImportOpen(false);
                    setImportSectionId(importSectionId);
                    setScope({ course_id: importTarget.course_id, section_id: importSectionId });
                    void navigate({
                      search: (previous) => ({
                        ...previous,
                        view: "import",
                        import_id: preview.roster_import_id,
                      }),
                    });
                  },
                },
              );
            }}
          >
            <Field label={t("roster.import.classLabel")} htmlFor="import-class">
              <select
                id="import-class"
                value={importSectionId}
                data-testid="import-class"
                onChange={(event) => setImportSectionId(event.target.value)}
              >
                <option value="">{t("roster.import.classPrompt")}</option>
                {(importSections.data ?? []).map((option) => (
                  <option key={option.sectionId} value={option.sectionId}>
                    {option.label}
                  </option>
                ))}
              </select>
            </Field>
            {importSectionId === "" ? (
              <p className="muted">{t("roster.import.classRequired")}</p>
            ) : null}
            <Field label={t("roster.import.fileLabel")} htmlFor="import-file">
              <input
                id="import-file"
                type="file"
                accept=".csv,.xlsx"
                data-testid="import-file"
                onChange={(event) => setImportFile(event.target.files?.[0] ?? null)}
              />
            </Field>
            {importFile ? <p className="muted">{importFile.name}</p> : null}
            {createImport.isError ? (
              <p className="form-error" data-testid="import-roster-error">
                {describeApiError(createImport.error)}
              </p>
            ) : null}
            <div className="inline">
              <button
                type="submit"
                className="button button--primary"
                data-testid="import-roster-save"
                disabled={
                  importFile === null || importSectionId === "" || createImport.isPending
                }
              >
                {t("roster.import.submit")}
              </button>
              <button
                type="button"
                className="button button--ghost"
                onClick={() => setImportOpen(false)}
              >
                {t("common.cancel")}
              </button>
            </div>
          </form>
        </Modal>
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

