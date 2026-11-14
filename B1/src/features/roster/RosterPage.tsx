import { useMemo, useState } from "react";
import { useNavigate, useSearch } from "@tanstack/react-router";
import { t } from "../../i18n";
import { describeApiError, isApiError } from "../../api/problem";
import { useSession } from "../../auth/sessionContext";
import { canCreateCourse } from "../../auth/visibility";
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
import type { ClassPublic, CoursePublic } from "../../api/types";
import {
  loadedCourses,
  useClasses,
  useCourses,
  useCreateClass,
  useCreateCourse,
  useCreateSection,
  useDeleteClass,
  useDeleteCourse,
  useDeleteSection,
  useRenameClass,
  useRenameCourse,
  useSections,
} from "../../api/directory";
import { useAllowlistBatches } from "../admin/useAllowlist";
import {
  useClassEnrollments,
  useDeleteEnrollment,
} from "./useEnrollments";
import { RosterImportPanel } from "../roster-import/RosterImportPanel";
import { Select } from "../../ui/Select";

/**
 * 课程与名单（P12 / v0.32.0）。
 *
 * 结构按契约的领域模型展开：**课程 → 课序（section）→ 班级（class）→ 学生（enrollment）**。
 * 课序不再等同班级：一个课序可挂多个班级，"新建班级 / 把已注册名单的班级导入某个课序 /
 * 重命名或删除班级"都是班级级操作。旧的一课序一班的 `POST /courses/{id}/classes` 已从契约
 * 删除，页面也不再提供那个入口。
 *
 * 名单导入在同一个工作区的「导入名单」标签里完成（选择 课程 → 课序 → 班级 后上传），
 * 因此不再有第二个文件上传入口。
 */

type DeleteTarget =
  | { kind: "course"; courseId: string; name: string }
  | { kind: "section"; sectionId: string; name: string }
  | { kind: "class"; classId: string; sectionId: string; name: string };

/** 409 的可读提示：服务端 detail 里带冲突计数（冲突学生数 / 冲突班级），原样展示。 */
function conflictHint(error: unknown): string {
  if (isApiError(error) && error.status === 409 && error.problem?.detail) {
    return error.problem.detail;
  }
  return describeApiError(error);
}

function isConflict(error: unknown): boolean {
  return isApiError(error) && error.status === 409;
}

export function RosterPage() {
  const session = useSession();
  const search = useSearch({ from: "/roster" });
  const navigate = useNavigate({ from: "/roster" });
  const view = search.view === "import" ? "import" : "roster";

  const courses = useCourses();
  const courseOptions = useMemo(() => loadedCourses(courses.data?.pages), [courses.data]);

  const [createOpen, setCreateOpen] = useState(false);
  const [createTitle, setCreateTitle] = useState("");
  const [createCode, setCreateCode] = useState("");
  const createCourse = useCreateCourse();

  const [renameOpen, setRenameOpen] = useState(false);
  const [renameTarget, setRenameTarget] = useState<CoursePublic | null>(null);
  const [renameTitle, setRenameTitle] = useState("");
  const [renameCode, setRenameCode] = useState("");
  const renameCourse = useRenameCourse();

  const selectedCourseId = search.course_id ?? "";
  const expandedSectionId = search.section_id ?? null;
  const sectionsQuery = useSections(selectedCourseId === "" ? null : selectedCourseId);
  const deleteCourse = useDeleteCourse();
  const deleteSection = useDeleteSection(selectedCourseId === "" ? null : selectedCourseId);
  const [deleteError, setDeleteError] = useState<unknown>(null);
  const [pendingDelete, setPendingDelete] = useState<DeleteTarget | null>(null);

  // ---- 新建课序 ----------------------------------------------------------
  const [createSectionOpen, setCreateSectionOpen] = useState(false);
  const [newSectionCode, setNewSectionCode] = useState("");
  const [newSectionTerm, setNewSectionTerm] = useState("");
  const createSection = useCreateSection(selectedCourseId === "" ? null : selectedCourseId);

  // ---- 班级操作（新建 / 导入注册班级 / 重命名 / 删除）---------------------
  const [classTargetSection, setClassTargetSection] = useState<string | null>(null);
  const [classCreateOpen, setClassCreateOpen] = useState(false);
  const [newClassName, setNewClassName] = useState("");
  const createClass = useCreateClass({
    courseId: selectedCourseId === "" ? null : selectedCourseId,
    sectionId: classTargetSection,
  });

  const [attachOpen, setAttachOpen] = useState(false);
  const [attachBatchId, setAttachBatchId] = useState("");
  const [attachClassName, setAttachClassName] = useState("");
  const batches = useAllowlistBatches();
  const attachClass = useCreateClass({
    courseId: selectedCourseId === "" ? null : selectedCourseId,
    sectionId: classTargetSection,
  });

  const [renameClassOpen, setRenameClassOpen] = useState(false);
  const [renameClassTarget, setRenameClassTarget] = useState<ClassPublic | null>(null);
  const [renameClassName, setRenameClassName] = useState("");
  const renameClass = useRenameClass({
    courseId: selectedCourseId === "" ? null : selectedCourseId,
    sectionId: renameClassTarget?.section_id ?? classTargetSection,
  });
  const deleteClass = useDeleteClass({
    courseId: selectedCourseId === "" ? null : selectedCourseId,
    sectionId: classTargetSection,
  });
  const [expandedClassId, setExpandedClassId] = useState<string | null>(null);

  const openRename = async (row: CoursePublic) => {
    // 用刚拉取到的行做乐观锁：列表可能是几分钟前读的，拿旧 `updated_at` 保存会被服务端 409
    // 拒绝，而用户看到的只是"重命名没反应"。刷新一次再打开弹窗，失败率显著降低。
    const refreshed = await courses.refetch();
    const latest = loadedCourses(refreshed.data?.pages).find(
      (course) => course.course_id === row.course_id,
    );
    setRenameTarget(latest ?? row);
    setRenameTitle((latest ?? row).title);
    setRenameCode((latest ?? row).course_code ?? "");
    setRenameOpen(true);
  };

  const sectionOptions = useMemo(() => sectionsQuery.data ?? [], [sectionsQuery.data]);

  const selectedCourse =
    courseOptions.find((course) => course.course_id === selectedCourseId) ?? null;

  const rosterView = (
    <div className="stack">
      {deleteError ? (
        <Banner tone="error" testId="roster-delete-error">
          {describeApiError(deleteError)}
        </Banner>
      ) : null}

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
                    onClick={() =>
                      void navigate({
                        search: (previous) => ({
                          ...previous,
                          course_id: row.course_id,
                          section_id: undefined,
                        }),
                      })
                    }
                  >
                    {row.title}
                  </button>
                ),
              },
              {
                key: "code",
                header: t("roster.column.code"),
                render: (row) =>
                  row.section_codes?.length
                    ? row.section_codes.join("、")
                    : (row.course_code ?? t("roster.column.noCode")),
              },
              {
                key: "sections",
                header: t("roster.column.classes"),
                render: (row) => (
                  <span data-testid={`course-sections-${row.course_id}`}>
                    {row.section_count}
                  </span>
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
                      onClick={() => void openRename(row)}
                    >
                      {t("roster.rename.entry")}
                    </button>
                    <button
                      type="button"
                      className="button button--ghost allowlist-entry__delete"
                      aria-label={t("roster.course.delete", { name: row.title })}
                      title={t("roster.course.delete", { name: row.title })}
                      data-testid={`course-delete-${row.course_id}`}
                      onClick={() => {
                        setDeleteError(null);
                        setPendingDelete({
                          kind: "course",
                          courseId: row.course_id,
                          name: row.title,
                        });
                      }}
                    >
                      <TrashIcon />
                    </button>
                  </span>
                ),
              },
            ]}
          />
        )}
      </Panel>

      {selectedCourseId !== "" ? (
        <Panel
          title={t("roster.sectionListTitle")}
          testId="course-sections"
          actions={
            <button
              type="button"
              className="button button--primary"
              data-testid="section-create-entry"
              onClick={() => {
                setNewSectionCode("");
                setNewSectionTerm("");
                setCreateSectionOpen(true);
              }}
            >
              {t("roster.createSection.entry")}
            </button>
          }
        >
          <p className="muted" data-testid="roster-scope">
            {selectedCourse ? selectedCourse.title : selectedCourseId}
          </p>
          {sectionsQuery.isPending ? (
            <Spinner />
          ) : sectionsQuery.isError ? (
            <ErrorState error={sectionsQuery.error} />
          ) : sectionOptions.length === 0 ? (
            <EmptyState>{t("roster.emptyNoSection")}</EmptyState>
          ) : (
            <ul className="allowlist-batches" data-testid="section-list">
              {sectionOptions.map((option) => {
                const row = option.row;
                const isOpen = expandedSectionId === option.sectionId;
                return (
                  <li key={option.sectionId} data-testid={`section-row-${option.sectionId}`}>
                    <div className="allowlist-batch__head">
                      <button
                        type="button"
                        className="allowlist-batch__toggle"
                        aria-expanded={isOpen}
                        data-testid={`section-expand-${option.sectionId}`}
                        onClick={() =>
                          void navigate({
                            search: (previous) => ({
                              ...previous,
                              section_id: isOpen ? undefined : option.sectionId,
                              course_id: selectedCourseId,
                            }),
                          })
                        }
                      >
                        <span className="allowlist-batch__name">
                          <span aria-hidden="true">{isOpen ? "▾" : "▸"}</span>{" "}
                          {t("roster.sectionLabel", {
                            code: row.section_code,
                            term: row.term ? ` · ${row.term}` : "",
                          })}
                        </span>
                        <span
                          className="muted"
                          data-testid={`section-class-count-${option.sectionId}`}
                        >
                          {t("roster.section.classCount", { count: row.class_count ?? 0 })}
                        </span>
                        <span
                          className="muted"
                          data-testid={`section-enrollment-count-${option.sectionId}`}
                        >
                          {t("allowlist.students", { count: row.enrollment_count })}
                        </span>
                      </button>
                      <span className="inline">
                        <button
                          type="button"
                          className="button button--ghost"
                          data-testid={`section-create-class-${option.sectionId}`}
                          onClick={() => {
                            setClassTargetSection(option.sectionId);
                            setNewClassName("");
                            createClass.reset();
                            setClassCreateOpen(true);
                          }}
                        >
                          {t("roster.section.createClass")}
                        </button>
                        <button
                          type="button"
                          className="button button--ghost"
                          data-testid={`section-attach-${option.sectionId}`}
                          onClick={() => {
                            setClassTargetSection(option.sectionId);
                            setAttachBatchId("");
                            setAttachClassName("");
                            attachClass.reset();
                            setAttachOpen(true);
                          }}
                        >
                          {t("roster.section.attachBatch")}
                        </button>
                        <button
                          type="button"
                          className="button button--ghost allowlist-entry__delete"
                          aria-label={t("roster.section.delete", {
                            code: row.section_code,
                          })}
                          title={t("roster.section.delete", { code: row.section_code })}
                          data-testid={`section-delete-${option.sectionId}`}
                          onClick={() => {
                            setDeleteError(null);
                            setPendingDelete({
                              kind: "section",
                              sectionId: option.sectionId,
                              name: row.section_code,
                            });
                          }}
                        >
                          <TrashIcon />
                        </button>
                      </span>
                    </div>
                    {isOpen ? (
                      <SectionClasses
                        courseId={selectedCourseId}
                        sectionId={option.sectionId}
                        expandedClassId={expandedClassId}
                        onToggleClass={(classId) =>
                          setExpandedClassId((previous) =>
                            previous === classId ? null : classId,
                          )
                        }
                        onRenameClass={(row) => {
                          setClassTargetSection(row.section_id);
                          setRenameClassTarget(row);
                          setRenameClassName(row.class_name);
                          renameClass.reset();
                          setRenameClassOpen(true);
                        }}
                        onDeleteClass={(row) => {
                          setClassTargetSection(row.section_id);
                          setDeleteError(null);
                          setPendingDelete({
                            kind: "class",
                            classId: row.class_id,
                            sectionId: row.section_id,
                            name: row.class_name,
                          });
                        }}
                      />
                    ) : null}
                  </li>
                );
              })}
            </ul>
          )}
        </Panel>
      ) : null}
    </div>
  );

  const importView = (
    <RosterImportPanel
      importId={search.import_id ?? null}
      initialCourseId={selectedCourseId}
      initialSectionId={expandedSectionId ?? ""}
      onImportId={(id) =>
        void navigate({
          search: (previous) => ({
            ...previous,
            view: "import",
            import_id: id ?? undefined,
          }),
        })
      }
    />
  );

  const body = view === "import" ? importView : rosterView;

  return (
    <div className="stack">
      <div className="inline">
        <PageTitle>{t("roster.workspaceTitle")}</PageTitle>
        <div className="row-actions">
          <button
            type="button"
            className="button"
            data-testid="roster-tab-roster"
            aria-pressed={view === "roster"}
            onClick={() =>
              void navigate({
                search: (previous) => ({ ...previous, view: "roster" }),
              })
            }
          >
            {t("roster.tabs.roster")}
          </button>
          <button
            type="button"
            className="button"
            data-testid="roster-tab-import"
            aria-pressed={view === "import"}
            onClick={() =>
              void navigate({
                search: (previous) => ({ ...previous, view: "import" }),
              })
            }
          >
            {t("roster.tabs.import")}
          </button>
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
      </div>

      {body}

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
              if (createTitle.trim() === "") return;
              createCourse.mutate(
                {
                  title: createTitle.trim(),
                  ...(createCode.trim() === "" ? {} : { course_code: createCode.trim() }),
                },
                {
                  onSuccess: (created) => {
                    setCreateOpen(false);
                    void navigate({
                      search: (previous) => ({
                        ...previous,
                        course_id: created.course_id,
                        section_id: undefined,
                      }),
                    });
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
              <Banner tone="error" testId="create-course-error">
                {isApiError(createCourse.error) && createCourse.error.status === 409
                  ? t("roster.create.duplicate")
                  : describeApiError(createCourse.error)}
              </Banner>
            ) : null}
            <div className="inline">
              <button
                type="submit"
                className="button button--primary"
                data-testid="create-course-save"
                disabled={createTitle.trim() === "" || createCourse.isPending}
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
              if (renameTitle.trim() === "") return;
              renameCourse.mutate(
                {
                  courseId: renameTarget.course_id,
                  title: renameTitle.trim(),
                  // 空字符串 = 清空序号；保持不变则原样回传。
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
            {renameCourse.isError ? (
              <Banner tone="error" testId="rename-course-error">
                {describeApiError(renameCourse.error)}
              </Banner>
            ) : null}
            <div className="inline">
              <button
                type="submit"
                className="button button--primary"
                data-testid="rename-course-save"
                disabled={renameTitle.trim() === "" || renameCourse.isPending}
              >
                {renameCourse.isPending ? t("roster.rename.saving") : t("roster.rename.save")}
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

      {createSectionOpen ? (
        <Modal
          title={t("roster.createSection.title")}
          testId="create-section"
          onClose={() => setCreateSectionOpen(false)}
        >
          <form
            className="stack"
            onSubmit={(event) => {
              event.preventDefault();
              if (newSectionCode.trim() === "") return;
              createSection.mutate(
                {
                  section_code: newSectionCode.trim(),
                  term: newSectionTerm.trim() === "" ? null : newSectionTerm.trim(),
                },
                {
                  onSuccess: (created) => {
                    setCreateSectionOpen(false);
                    void navigate({
                      search: (previous) => ({
                        ...previous,
                        course_id: selectedCourseId,
                        section_id: created.section_id,
                      }),
                    });
                  },
                },
              );
            }}
          >
            <Field label={t("roster.createSection.code")} htmlFor="create-section-code">
              <input
                id="create-section-code"
                value={newSectionCode}
                data-testid="create-section-code"
                onChange={(event) => setNewSectionCode(event.target.value)}
              />
            </Field>
            <Field label={t("roster.createSection.term")} htmlFor="create-section-term">
              <input
                id="create-section-term"
                value={newSectionTerm}
                data-testid="create-section-term"
                onChange={(event) => setNewSectionTerm(event.target.value)}
              />
            </Field>
            {createSection.isError ? (
              <Banner tone="error" testId="create-section-error">
                {isApiError(createSection.error) && createSection.error.status === 409
                  ? t("roster.createSection.duplicate")
                  : describeApiError(createSection.error)}
              </Banner>
            ) : null}
            <div className="inline">
              <button
                type="submit"
                className="button button--primary"
                data-testid="create-section-save"
                disabled={newSectionCode.trim() === "" || createSection.isPending}
              >
                {createSection.isPending
                  ? t("roster.createSection.saving")
                  : t("roster.createSection.submit")}
              </button>
              <button
                type="button"
                className="button button--ghost"
                onClick={() => setCreateSectionOpen(false)}
              >
                {t("common.cancel")}
              </button>
            </div>
          </form>
        </Modal>
      ) : null}

      {classCreateOpen ? (
        <Modal
          title={t("roster.class.create.title")}
          testId="create-class"
          onClose={() => setClassCreateOpen(false)}
        >
          <form
            className="stack"
            onSubmit={(event) => {
              event.preventDefault();
              if (newClassName.trim() === "" || classTargetSection === null) return;
              createClass.mutate(
                { class_name: newClassName.trim() },
                {
                  onSuccess: () => setClassCreateOpen(false),
                },
              );
            }}
          >
            <Field label={t("roster.class.create.name")} htmlFor="create-class-name">
              <input
                id="create-class-name"
                value={newClassName}
                maxLength={64}
                placeholder={t("roster.class.create.namePlaceholder")}
                data-testid="create-class-name"
                onChange={(event) => setNewClassName(event.target.value)}
              />
            </Field>
            {createClass.isError ? (
              <Banner tone="error" testId="create-class-error">
                {isConflict(createClass.error)
                  ? t("roster.class.create.conflict", {
                      detail: conflictHint(createClass.error),
                    })
                  : describeApiError(createClass.error)}
              </Banner>
            ) : null}
            <div className="inline">
              <button
                type="submit"
                className="button button--primary"
                data-testid="create-class-save"
                disabled={newClassName.trim() === "" || createClass.isPending}
              >
                {t("roster.class.create.submit")}
              </button>
              <button
                type="button"
                className="button button--ghost"
                onClick={() => setClassCreateOpen(false)}
              >
                {t("common.cancel")}
              </button>
            </div>
          </form>
        </Modal>
      ) : null}

      {attachOpen ? (
        <Modal
          title={t("roster.class.attach.title")}
          testId="attach-registered-class"
          onClose={() => setAttachOpen(false)}
        >
          <form
            className="stack"
            onSubmit={(event) => {
              event.preventDefault();
              if (
                attachBatchId === "" ||
                attachClassName.trim() === "" ||
                classTargetSection === null
              ) {
                return;
              }
              attachClass.mutate(
                { class_name: attachClassName.trim(), batch_id: attachBatchId },
                {
                  onSuccess: () => setAttachOpen(false),
                },
              );
            }}
          >
            <Field label={t("roster.class.attach.batch")} htmlFor="attach-batch">
              <Select
                id="attach-batch"
                value={attachBatchId}
                data-testid="attach-batch"
                onChange={(event) => {
                  const batchId = event.target.value;
                  setAttachBatchId(batchId);
                  const batch = (batches.data?.items ?? []).find(
                    (row) => row.batch_id === batchId,
                  );
                  // 默认用注册班级名（文件名去扩展名），教师仍可改。
                  setAttachClassName(batch?.class_name ?? "");
                }}
              >
                <option value="">{t("roster.class.attach.batchPrompt")}</option>
                {(batches.data?.items ?? []).map((batch) => (
                  <option key={batch.batch_id} value={batch.batch_id}>
                    {t("roster.import.classOption", {
                      name: batch.class_name,
                      count: batch.row_count,
                    })}
                  </option>
                ))}
              </Select>
            </Field>
            {batches.isPending ? <Spinner /> : null}
            {batches.isError ? <ErrorState error={batches.error} /> : null}
            {(batches.data?.items ?? []).length === 0 && !batches.isPending ? (
              <p className="muted">{t("roster.class.attach.noBatches")}</p>
            ) : null}
            <Field label={t("roster.class.attach.name")} htmlFor="attach-class-name">
              <input
                id="attach-class-name"
                value={attachClassName}
                maxLength={64}
                data-testid="attach-class-name"
                onChange={(event) => setAttachClassName(event.target.value)}
              />
            </Field>
            {attachClass.isError ? (
              <Banner tone="error" testId="attach-class-error">
                {isConflict(attachClass.error)
                  ? t("roster.class.attach.conflict", {
                      detail: conflictHint(attachClass.error),
                    })
                  : describeApiError(attachClass.error)}
              </Banner>
            ) : null}
            <div className="inline">
              <button
                type="submit"
                className="button button--primary"
                data-testid="attach-class-save"
                disabled={
                  attachBatchId === "" ||
                  attachClassName.trim() === "" ||
                  attachClass.isPending
                }
              >
                {t("roster.class.attach.submit")}
              </button>
              <button
                type="button"
                className="button button--ghost"
                onClick={() => setAttachOpen(false)}
              >
                {t("common.cancel")}
              </button>
            </div>
          </form>
        </Modal>
      ) : null}

      {renameClassOpen && renameClassTarget ? (
        <Modal
          title={t("roster.class.rename.title")}
          testId="rename-class"
          onClose={() => setRenameClassOpen(false)}
        >
          <form
            className="stack"
            onSubmit={(event) => {
              event.preventDefault();
              if (renameClassName.trim() === "") return;
              renameClass.mutate(
                {
                  classId: renameClassTarget.class_id,
                  body: { class_name: renameClassName.trim() },
                },
                { onSuccess: () => setRenameClassOpen(false) },
              );
            }}
          >
            <Field label={t("roster.class.rename.name")} htmlFor="rename-class-name">
              <input
                id="rename-class-name"
                value={renameClassName}
                maxLength={64}
                data-testid="rename-class-name"
                onChange={(event) => setRenameClassName(event.target.value)}
              />
            </Field>
            {renameClass.isError ? (
              <Banner tone="error" testId="rename-class-error">
                {isConflict(renameClass.error)
                  ? t("roster.class.rename.conflict", {
                      detail: conflictHint(renameClass.error),
                    })
                  : describeApiError(renameClass.error)}
              </Banner>
            ) : null}
            <div className="inline">
              <button
                type="submit"
                className="button button--primary"
                data-testid="rename-class-save"
                disabled={renameClassName.trim() === "" || renameClass.isPending}
              >
                {t("roster.class.rename.submit")}
              </button>
              <button
                type="button"
                className="button button--ghost"
                onClick={() => setRenameClassOpen(false)}
              >
                {t("common.cancel")}
              </button>
            </div>
          </form>
        </Modal>
      ) : null}

      {pendingDelete ? (
        <Modal
          title={
            pendingDelete.kind === "course"
              ? t("roster.deleteCourse.title")
              : pendingDelete.kind === "section"
                ? t("roster.section.deleteTitle")
                : t("roster.deleteClass.title")
          }
          testId="confirm-delete"
          onClose={() => setPendingDelete(null)}
        >
          <div className="stack">
            <p data-testid="confirm-delete-message">
              {pendingDelete.kind === "course"
                ? t("roster.deleteCourse.message", { name: pendingDelete.name })
                : pendingDelete.kind === "section"
                  ? t("roster.section.deleteMessage", { name: pendingDelete.name })
                  : t("roster.deleteClass.message", { name: pendingDelete.name })}
            </p>
            <p className="muted">
              {pendingDelete.kind === "course"
                ? t("roster.deleteCourse.hint")
                : pendingDelete.kind === "section"
                  ? t("roster.section.deleteHint")
                  : t("roster.deleteClass.hint")}
            </p>
            {deleteError ? (
              <Banner tone="error" testId="roster-delete-error">
                {describeApiError(deleteError)}
              </Banner>
            ) : null}
            <div className="inline">
              <button
                type="button"
                className="button button--danger"
                data-testid="confirm-delete-accept"
                disabled={
                  deleteCourse.isPending || deleteSection.isPending || deleteClass.isPending
                }
                onClick={() => {
                  setDeleteError(null);
                  if (pendingDelete.kind === "course") {
                    const courseId = pendingDelete.courseId;
                    deleteCourse.mutate(courseId, {
                      onSuccess: () => {
                        setPendingDelete(null);
                        if (selectedCourseId === courseId) {
                          void navigate({
                            search: (previous) => ({
                              ...previous,
                              course_id: undefined,
                              section_id: undefined,
                            }),
                          });
                        }
                      },
                      onError: (failure) => setDeleteError(failure),
                    });
                    return;
                  }
                  if (pendingDelete.kind === "section") {
                    const sectionId = pendingDelete.sectionId;
                    deleteSection.mutate(sectionId, {
                      onSuccess: () => {
                        setPendingDelete(null);
                        if (expandedSectionId === sectionId) {
                          void navigate({
                            search: (previous) => ({
                              ...previous,
                              section_id: undefined,
                            }),
                          });
                        }
                      },
                      onError: (failure) => setDeleteError(failure),
                    });
                    return;
                  }
                  deleteClass.mutate(pendingDelete.classId, {
                    onSuccess: () => setPendingDelete(null),
                    onError: (failure) => setDeleteError(failure),
                  });
                }}
              >
                {deleteCourse.isPending || deleteSection.isPending || deleteClass.isPending
                  ? t("roster.delete.deleting")
                  : t("roster.delete.confirm")}
              </button>
              <button
                type="button"
                className="button button--ghost"
                data-testid="confirm-delete-cancel"
                onClick={() => setPendingDelete(null)}
              >
                {t("common.cancel")}
              </button>
            </div>
          </div>
        </Modal>
      ) : null}
    </div>
  );
}

/** 一个课序下的班级列表 + 展开后的班级学生（v0.32.0）。 */
function SectionClasses({
  courseId,
  sectionId,
  expandedClassId,
  onToggleClass,
  onRenameClass,
  onDeleteClass,
}: {
  courseId: string;
  sectionId: string;
  expandedClassId: string | null;
  onToggleClass: (classId: string) => void;
  onRenameClass: (row: ClassPublic) => void;
  onDeleteClass: (row: ClassPublic) => void;
}) {
  const classes = useClasses(sectionId);
  if (classes.isPending) return <Spinner />;
  if (classes.isError) return <ErrorState error={classes.error} />;
  const rows = classes.data ?? [];
  if (rows.length === 0) {
    return <EmptyState>{t("roster.classes.empty")}</EmptyState>;
  }
  return (
    <ul className="allowlist-batches" data-testid={`section-classes-${sectionId}`}>
      {rows.map((row) => {
        const isOpen = expandedClassId === row.class_id;
        return (
          <li key={row.class_id} className="roster-class-card" data-testid={`class-card-${row.class_id}`}>
            <div className="allowlist-batch__head">
              <button
                type="button"
                className="allowlist-batch__toggle"
                aria-expanded={isOpen}
                data-testid={`class-expand-${row.class_id}`}
                onClick={() => onToggleClass(row.class_id)}
              >
                <span className="allowlist-batch__name">
                  <span aria-hidden="true">{isOpen ? "▾" : "▸"}</span> {row.class_name}
                </span>
                <span className="muted" data-testid={`class-count-${row.class_id}`}>
                  {t("allowlist.students", { count: row.enrollment_count })}
                </span>
                <span className="muted" data-testid={`class-bound-${row.class_id}`}>
                  {t("roster.class.boundCount", {
                    bound: row.bound_count,
                    total: row.enrollment_count,
                  })}
                </span>
                <span className="muted" data-testid={`class-source-${row.class_id}`}>
                  {t(`roster.class.source.${row.source}`)}
                </span>
              </button>
              <span className="inline">
                <button
                  type="button"
                  className="button button--ghost"
                  data-testid={`class-rename-${row.class_id}`}
                  onClick={() => onRenameClass(row)}
                >
                  {t("roster.rename.entry")}
                </button>
                <button
                  type="button"
                  className="button button--ghost allowlist-entry__delete"
                  aria-label={t("roster.classes.delete", { name: row.class_name })}
                  title={t("roster.classes.delete", { name: row.class_name })}
                  data-testid={`class-delete-${row.class_id}`}
                  onClick={() => onDeleteClass(row)}
                >
                  <TrashIcon />
                </button>
              </span>
            </div>
            {isOpen ? (
              <ClassStudents courseId={courseId} sectionId={sectionId} classId={row.class_id} />
            ) : null}
          </li>
        );
      })}
    </ul>
  );
}

/**
 * 一个班级的学生，排布与「注册名单」展开后一致：姓名、学号、右侧垃圾桶。
 *
 * 垃圾桶删的是"这个学生在名单里的记录"（退课），而不是账号——判断依据来自 API 的
 * `DELETE /enrollments/{id}`，页面只是它的入口。
 */
function ClassStudents({
  courseId,
  sectionId,
  classId,
}: {
  courseId: string;
  sectionId: string;
  classId: string;
}) {
  const enrollments = useClassEnrollments(classId);
  const removeEnrollment = useDeleteEnrollment({ classId, sectionId, courseId });

  if (enrollments.isPending) {
    return <Spinner />;
  }
  if (enrollments.isError) {
    return <ErrorState error={enrollments.error} />;
  }
  const rows = (enrollments.data?.pages ?? []).flatMap((page) => page.items);
  if (rows.length === 0) {
    return <p className="muted">{t("roster.classes.noStudents")}</p>;
  }
  return (
    <div className="roster-students-wrap" data-testid={`class-students-${classId}`}>
      <table className="table roster-students-table">
        <thead>
          <tr>
            <th>{t("roster.classes.colName")}</th>
            <th>{t("roster.classes.colNumber")}</th>
            <th>{t("roster.classes.colStatus")}</th>
            <th>{t("roster.column.actions")}</th>
          </tr>
        </thead>
        <tbody>
          {rows.map((row) => (
            <tr key={row.enrollment_id} data-testid={`class-student-${row.enrollment_id}`}>
              <td>{row.display_name}</td>
              <td className="mono-id">{row.student_number}</td>
              <td>
                {row.student_id === null ? (
                  <span
                    className="badge badge--warn roster-status-badge"
                    data-testid={`class-student-unbound-${row.enrollment_id}`}
                  >
                    {t("roster.studentUnbound")}
                  </span>
                ) : (
                  <span className="badge badge--ok roster-status-badge">
                    {t("roster.studentBound")}
                  </span>
                )}
              </td>
              <td>
                <button
                  type="button"
                  className="button button--ghost allowlist-entry__delete"
                  aria-label={t("roster.classes.remove", { name: row.display_name })}
                  title={t("roster.classes.remove", { name: row.display_name })}
                  data-testid={`class-student-delete-${row.enrollment_id}`}
                  onClick={() => removeEnrollment.mutate(row.enrollment_id)}
                >
                  <TrashIcon />
                </button>
              </td>
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  );
}

/** 垃圾桶图标：课程、课序、班级、名单行共用同一枚，避免四处各画一个。 */
function TrashIcon() {
  return (
    <svg viewBox="0 0 24 24" width="16" height="16" aria-hidden="true" focusable="false">
      <path
        d="M9 3h6l1 2h4v2H4V5h4l1-2zm-3 6h12l1 12H7L6 9zm3 2v8h2v-8H9zm4 0v8h2v-8h-2z"
        fill="currentColor"
      />
    </svg>
  );
}
