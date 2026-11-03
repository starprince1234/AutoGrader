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
import type { CoursePublic } from "../../api/types";
import {
  loadedCourses,
  useCourses,
  useCreateCourse,
  useRenameCourse,
  useSections,
} from "../../api/directory";
import { useDeleteEnrollment, useEnrollments } from "./useEnrollments";

/**
 * 课程与名单（P12）：一张课程表 + 新建/重命名两个居中弹窗。
 *
 * 为什么去掉"查询条件"：课程是这一页唯一的主体，先选课程再选课序等于让老师每次进来都先做
 * 一次"进入状态"的操作，而表格本身就能把课程、序号、班级数一次呈现出来。
 *
 * 为什么这里不再有"导入名单"：班级名单在「用户管理 → 注册名单」维护（一个文件一个班级），
 * 课程这页不再提供第二个文件上传入口——同一件事有两个入口，迟早会出现"这份名单是从哪边导入的"。
 */
export function RosterPage() {
  const session = useSession();
  const search = useSearch({ from: "/roster" });
  const navigate = useNavigate({ from: "/roster" });

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
  const classes = useSections(selectedCourseId === "" ? null : selectedCourseId);
  const [expandedClass, setExpandedClass] = useState<string | null>(null);

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
                    onClick={() =>
                      void navigate({
                        search: (previous) => ({ ...previous, course_id: row.course_id }),
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
                  <button
                    type="button"
                    className="button button--ghost"
                    data-testid={`course-rename-${row.course_id}`}
                    onClick={() => void openRename(row)}
                  >
                    {t("roster.rename.entry")}
                  </button>
                ),
              },
            ]}
          />
        )}
        {selectedCourseId !== "" ? (
          <div className="stack" data-testid="course-classes">
            <p className="muted" data-testid="roster-scope">
              {courseOptions.find((course) => course.course_id === selectedCourseId)?.title ??
                selectedCourseId}
            </p>
            {classes.isPending ? (
              <Spinner />
            ) : classes.isError ? (
              <ErrorState error={classes.error} />
            ) : (classes.data ?? []).length === 0 ? (
              <EmptyState>{t("roster.classes.empty")}</EmptyState>
            ) : (
              <ul className="allowlist-batches">
                {(classes.data ?? []).map((option) => {
                  const row = option.row;
                  const isOpen = expandedClass === option.sectionId;
                  return (
                    <li key={option.sectionId} data-testid={`class-row-${option.sectionId}`}>
                      <div className="allowlist-batch__head">
                        <button
                          type="button"
                          className="allowlist-batch__toggle"
                          aria-expanded={isOpen}
                          data-testid={`class-expand-${option.sectionId}`}
                          onClick={() => setExpandedClass(isOpen ? null : option.sectionId)}
                        >
                          <span className="allowlist-batch__name">
                            <span aria-hidden="true">{isOpen ? "▾" : "▸"}</span> {option.label}
                          </span>
                          <span
                            className="muted"
                            data-testid={`class-count-${option.sectionId}`}
                          >
                            {t("allowlist.students", { count: row.enrollment_count })}
                          </span>
                          <span className="muted">
                            {(row.created_at ?? "").slice(0, 19).replace("T", " ")}
                          </span>
                        </button>
                      </div>
                      {isOpen ? <ClassStudents sectionId={option.sectionId} /> : null}
                    </li>
                  );
                })}
              </ul>
            )}
          </div>
        ) : null}
      </Panel>

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
                    void navigate({
                      search: (previous) => ({ ...previous, course_id: created.course_id }),
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
            <p className="muted">{t("roster.rename.hint")}</p>
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
    </div>
  );
}

/**
 * 一个班级的学生，排布与「注册名单」展开后完全一致：姓名、学号、右侧垃圾桶。
 *
 * 垃圾桶删的是"这个学生在名单里的记录"（退课），而不是账号——判断依据来自 API 的
 * `DELETE /enrollments/{id}`，页面只是它的入口。
 */
function ClassStudents({ sectionId }: { sectionId: string }) {
  const enrollments = useEnrollments(sectionId);
  const removeEnrollment = useDeleteEnrollment(sectionId);

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
    <ul className="allowlist-entries" data-testid={`class-students-${sectionId}`}>
      {rows.map((row) => (
        <li
          key={row.enrollment_id}
          className="allowlist-entry"
          data-testid={`class-student-${row.enrollment_id}`}
        >
          <span className="allowlist-entry__name">{row.display_name}</span>
          <span className="mono-id allowlist-entry__number">{row.student_number}</span>
          <button
            type="button"
            className="button button--ghost allowlist-entry__delete"
            aria-label={t("roster.classes.remove", { name: row.display_name })}
            title={t("roster.classes.remove", { name: row.display_name })}
            data-testid={`class-student-delete-${row.enrollment_id}`}
            onClick={() => removeEnrollment.mutate(row.enrollment_id)}
          >
            <svg viewBox="0 0 24 24" width="16" height="16" aria-hidden="true" focusable="false">
              <path
                d="M9 3h6l1 2h4v2H4V5h4l1-2zm-3 6h12l1 12H7L6 9zm3 2v8h2v-8H9zm4 0v8h2v-8h-2z"
                fill="currentColor"
              />
            </svg>
          </button>
        </li>
      ))}
    </ul>
  );
}
