import { useMemo } from "react";
import { Link } from "@tanstack/react-router";
import { t, type MessageKey } from "../../i18n";
import { isApiError } from "../../api/problem";
import type { MailRecordCategory, MailRecordView } from "../../api/types";
import {
  courseLabel as formatCourseLabel,
  loadedCourses,
  useCourses,
  useSections,
  type SectionOption,
} from "../../api/directory";
import { useAllAssignments } from "../assignments/useAssignments";
import { useSession } from "../../auth/sessionContext";
import {
  Banner,
  EmptyState,
  ErrorState,
  Field,
  LoadMoreActions,
  Panel,
  Spinner,
  Table,
  Tabs,
} from "../../ui/components";
import { formatDateTime, shortenId } from "../../ui/format";
import { loadedMailRecords, useMailRecords } from "./useMailRecords";
import { reasonText } from "./reasonCodes";
import {
  MAIL_CATEGORIES,
  readMailFilter,
  withMailFilter,
  type MailFilter,
  type ReviewSearch,
} from "./search";
import { Select } from "../../ui/Select";

/**
 * `/review`：**邮件记录**（只读）。
 *
 * 用户的原话是"这个功能想用于查看邮件提交记录"，所以本页只做一件事：把邮件接收处理过的信
 * 按三态呈现出来。它**不提供任何处置动作**——归属/匹配用户/暂存/作废/打回都已被移除，
 * 后端端点保留但本页不再调用。
 *
 * 三态的语义（与服务端判定一致，界面不自行推导）：
 *
 * 1. `processed`（已处理）：邮件已经成功变成作业提交，可以从作业界面点进去查看；
 * 2. `pending`（待处理）：收到了真实提交，但没有列入作业（例如核验码过期；或发件地址属于
 *    已注册账号——它是学生的信，即使正文格式不对也不会被当成垃圾）；
 * 3. `other`（其他）：剩余全部——广告垃圾、没有按格式提交、报文/附件被拒绝等。
 *
 * 三条来自真实授权的约束原样保留：
 *
 * 1. 列表只渲染契约 `MailRecordView` 的字段：没有邮件正文、附件内容与发件地址。
 * 2. URL 的课程/课序只是筛选：越界由服务端 403，页面显示拒绝而不是空列表。
 * 3. 未关联记录（`course_id=null`）只对管理员可见并标为"未关联"；普通 staff 的可见集合由
 *    服务端 SQL 谓词决定，页面拿不到它们、也不显示"隐藏行数"。
 */

const CATEGORY_LABEL: Record<MailRecordCategory, MessageKey> = {
  processed: "review.category.processed",
  pending: "review.category.pending",
  other: "review.category.other",
};

const CATEGORY_TITLE: Record<MailRecordCategory, MessageKey> = {
  processed: "review.title.processed",
  pending: "review.title.pending",
  other: "review.title.other",
};

const CATEGORY_HINT: Record<MailRecordCategory, MessageKey> = {
  processed: "review.hint.processed",
  pending: "review.hint.pending",
  other: "review.hint.other",
};

const CATEGORY_EMPTY: Record<MailRecordCategory, MessageKey> = {
  processed: "review.empty.processed",
  pending: "review.empty.pending",
  other: "review.empty.other",
};

/** 列表「说明」列的分类提示：只给 pending/other 用（processed 显示作业）。 */
const CATEGORY_ROW_HINT: Partial<Record<MailRecordCategory, MessageKey>> = {
  pending: "review.rowHint.pending",
  other: "review.rowHint.other",
};

function courseText(course: { course_code: string | null; title: string } | undefined, courseId: string): string {
  if (course === undefined) return shortenId(courseId);
  const code = (course.course_code ?? "").trim();
  return code === "" ? course.title : code;
}

function scopeText(
  row: MailRecordView,
  courseMap: Map<string, { course_code: string | null; title: string }>,
  sectionMap: Map<string, string>,
): string {
  if (row.course_id === null) return t("review.scopeUnscoped");
  const course = courseMap.get(row.course_id);
  const section =
    row.section_id === null
      ? t("review.scopeUnknown")
      : (sectionMap.get(row.section_id) ?? shortenId(row.section_id));
  return `${courseText(course, row.course_id)} · ${section}`;
}

export function MailRecordsTab({
  search,
  onChange,
}: {
  search: ReviewSearch;
  onChange: (next: ReviewSearch) => void;
}) {
  const session = useSession();
  const filter = readMailFilter(search);
  const identity = session.subject ?? session.username ?? "anonymous";
  const isAdmin = session.rolesKnown && session.roles.includes("admin");

  const courses = useCourses();
  // 令牌作用域是浏览器端能拿到的**唯一**授权事实：下拉只展示它覆盖的课程/课序，
  // 越权 URL 不会把别的课程"带进来"（服务端仍会 403，这里是不给它出现在界面里的机会）。
  const courseOptions = useMemo(
    () =>
      loadedCourses(courses.data?.pages).filter(
        (course) =>
          isAdmin || session.scope.some((entry) => entry.course_id === course.course_id),
      ),
    [courses.data, isAdmin, session.scope],
  );
  const scopeCourseIds = useMemo(
    () => new Set(session.scope.map((entry) => entry.course_id)),
    [session.scope],
  );
  const courseMap = useMemo(
    () => new Map(courseOptions.map((course) => [course.course_id, course] as const)),
    [courseOptions],
  );
  // staff 的默认视图是"我有权限的**全部**课程"（没有课程筛选 = 服务端按令牌 scope 过滤），
  // 而不是只看第一门课；URL 指向无权课程时同样回落到"全部"——界面不发出无权 ID。
  const requestedCourseId = filter.courseId;
  const requestedAllowed =
    requestedCourseId !== null && (isAdmin || scopeCourseIds.has(requestedCourseId));
  const effectiveCourseId = isAdmin
    ? requestedCourseId
    : requestedAllowed
      ? requestedCourseId
      : null;
  // 没有课程就没有课序：把 URL 里孤立的 section 丢掉，避免发出"只有 section 没有 course"的请求。
  const effectiveSectionId = effectiveCourseId === null ? null : filter.sectionId;
  const sections = useSections(effectiveCourseId);
  const sectionOptions = useMemo<SectionOption[]>(() => {
    const fetched = sections.data ?? [];
    if (isAdmin || effectiveCourseId === null) return fetched;
    const entries = session.scope.filter((entry) => entry.course_id === effectiveCourseId);
    if (entries.some((entry) => entry.section_id === null)) return fetched;
    const allowed = new Set(entries.map((entry) => entry.section_id));
    return fetched.filter((section) => allowed.has(section.sectionId));
  }, [effectiveCourseId, isAdmin, sections.data, session.scope]);
  const sectionMap = useMemo(
    () => new Map(sectionOptions.map((item) => [item.sectionId, item.label] as const)),
    [sectionOptions],
  );

  // 没有课程筛选时也允许查询：服务端按行级 scope 过滤，老师一进来就能看到自己的记录。
  const listEnabled = isAdmin || session.scope.length > 0;
  const query = useMailRecords({
    identity,
    enabled: listEnabled,
    filter: {
      category: filter.category,
      courseId: effectiveCourseId,
      sectionId: effectiveSectionId,
    },
  });
  const rows = useMemo(() => loadedMailRecords(query.data?.pages), [query.data]);

  // 已处理记录要显示作业标题：作业是课程级的，按当前课程筛选（管理员/未选课程 = 全部可见作业）。
  // 只有"已处理"列会用到它，因此其他分类不请求；取不到时退回短 id（不猜标题）。
  const assignments = useAllAssignments(
    { courseId: effectiveCourseId },
    { enabled: listEnabled && filter.category === "processed" },
  );
  const assignmentTitles = useMemo(
    () =>
      new Map(
        assignments.assignments.map(
          (assignment) => [assignment.assignment_id, assignment.title] as const,
        ),
      ),
    [assignments.assignments],
  );

  const changeFilter = (next: Partial<MailFilter>) => {
    onChange(
      withMailFilter({
        category: next.category ?? filter.category,
        courseId: next.courseId === undefined ? effectiveCourseId : next.courseId,
        sectionId: next.sectionId === undefined ? effectiveSectionId : next.sectionId,
      }),
    );
  };

  const forbidden = isApiError(query.error) && query.error.isForbidden;

  const renderDetail = (row: MailRecordView) => {
    if (row.category !== "processed") {
      const hintKey = CATEGORY_ROW_HINT[row.category];
      return (
        <div>
          <div data-testid={`review-reason-${row.record_id}`}>{reasonText(row.reason_code)}</div>
          {hintKey ? (
            <div className="muted" data-testid={`review-row-hint-${row.record_id}`}>
              {t(hintKey)}
            </div>
          ) : null}
        </div>
      );
    }
    // 已处理：显示作业标题；找不到标题时显示短 id（不能把"还没取到"写成"没有作业"）。
    const assignmentId = row.assignment_id;
    const title =
      assignmentId === null
        ? t("review.assignmentUnknown")
        : (assignmentTitles.get(assignmentId) ?? shortenId(assignmentId));
    return (
      <div>
        <div data-testid={`review-assignment-${row.record_id}`}>{title}</div>
        {assignmentId !== null && row.student_id !== null ? (
          <Link
            to="/assignments/$assignmentId/grading"
            params={{ assignmentId }}
            search={{ student: row.student_id }}
            data-testid={`review-view-assignment-${row.record_id}`}
          >
            {t("review.viewAssignment")}
          </Link>
        ) : null}
      </div>
    );
  };

  const renderList = (category: MailRecordCategory) => (
    <Panel
      title={t(CATEGORY_TITLE[category], { count: rows.length })}
      actions={
        <LoadMoreActions
          loadedCount={rows.length}
          hasNextPage={query.hasNextPage}
          isFetchingNextPage={query.isFetchingNextPage}
          onLoadMore={() => {
            void query.fetchNextPage();
          }}
          testId="review-records"
        />
      }
    >
      {!listEnabled ? (
        <EmptyState>{t("review.needCourse")}</EmptyState>
      ) : query.isPending ? (
        <Spinner />
      ) : forbidden ? (
        <Banner tone="error" testId="review-records-forbidden">
          {t("review.forbidden")}
        </Banner>
      ) : query.isError ? (
        <ErrorState error={query.error} testId="review-records-error" />
      ) : rows.length === 0 ? (
        <div data-testid="review-records-empty">
          <p className="muted">{t(CATEGORY_EMPTY[category])}</p>
          {!isAdmin ? <p className="muted">{t("review.emptyHint")}</p> : null}
        </div>
      ) : (
        <Table<MailRecordView>
          testId="review-records-table"
          label={t("review.tableLabel")}
          rows={rows}
          getRowKey={(row) => row.record_id}
          columns={[
            {
              key: "time",
              header: t("review.col.time"),
              render: (row) => (
                <span data-testid={`review-time-${row.record_id}`}>
                  {formatDateTime(row.received_at, t("common.none"))}
                </span>
              ),
            },
            {
              key: "subject",
              header: t("review.col.subject"),
              render: (row) => (
                <strong title={row.subject ?? undefined}>
                  {row.subject ?? t("review.noSubject")}
                </strong>
              ),
            },
            {
              key: "scope",
              header: t("review.col.scope"),
              render: (row) => (
                <span title={row.course_id ?? ""}>{scopeText(row, courseMap, sectionMap)}</span>
              ),
            },
            {
              key: "detail",
              header: t("review.col.detail"),
              render: renderDetail,
            },
          ]}
        />
      )}
    </Panel>
  );

  const tabs = MAIL_CATEGORIES.map((category) => ({
    id: category,
    label: t(CATEGORY_LABEL[category]),
    content: (
      <div className="stack">
        <p className="muted" data-testid={`review-status-hint-${category}`}>
          {t(CATEGORY_HINT[category])}
        </p>
        {renderList(category)}
      </div>
    ),
  }));

  return (
    <div className="stack">
      <Panel title={t("review.filter")}>
        <div className="stack">
          <div className="form-grid">
            <Field label={t("review.course")} htmlFor="review-records-course">
              <Select
                id="review-records-course"
                data-testid="review-records-course"
                value={effectiveCourseId ?? ""}
                onChange={(event) =>
                  changeFilter({ courseId: event.target.value || null, sectionId: null })
                }
              >
                <option value="">{t("review.allCourses")}</option>
                {courseOptions.map((course) => (
                  <option key={course.course_id} value={course.course_id}>
                    {formatCourseLabel(course)}
                  </option>
                ))}
              </Select>
            </Field>

            <Field label={t("review.section")} htmlFor="review-records-section">
              <Select
                id="review-records-section"
                data-testid="review-records-section"
                value={effectiveSectionId ?? ""}
                disabled={effectiveCourseId === null}
                onChange={(event) => changeFilter({ sectionId: event.target.value || null })}
              >
                <option value="">{t("review.allSections")}</option>
                {sectionOptions.map((section) => (
                  <option key={section.sectionId} value={section.sectionId}>
                    {section.label}
                  </option>
                ))}
              </Select>
            </Field>
          </div>
          {courses.isError ? <ErrorState error={courses.error} /> : null}
          {sections.isError ? <ErrorState error={sections.error} /> : null}
          <p className="muted">{t("review.scopeNote")}</p>
        </div>
      </Panel>

      <Tabs
        label={t("review.status.label")}
        testId="review-status"
        activeId={filter.category}
        tabs={tabs}
        onChange={(id) => {
          const category = MAIL_CATEGORIES.find((item) => item === id);
          if (category !== undefined) changeFilter({ category });
        }}
      />
    </div>
  );
}
