import { useMemo, useState } from "react";
import { Link } from "@tanstack/react-router";
import { t, type MessageKey } from "../../i18n";
import { isApiError } from "../../api/problem";
import type { MailRecordCategory, MailRecordView } from "../../api/types";
import {
  courseLabel as formatCourseLabel,
  loadedCourses,
  useCourses,
  useSections,
  useSectionsForCourses,
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
  Modal,
  Panel,
  Spinner,
  Table,
  Tabs,
} from "../../ui/components";
import { formatDateTime, shortenId } from "../../ui/format";
import { loadedMailRecords, useMailRecords } from "./useMailRecords";
import { useDismissMailRecord, useRejectMailRecord } from "./useMailRecordActions";
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
 * `/review`：**邮件记录**。
 *
 * 用户的原话是"这个功能想用于查看邮件提交记录"，所以本页的主体是把邮件接收处理过的信按三态
 * 呈现出来；但"有待处理记录却无法处理"是明确报过的 bug，因此 `pending`/`other` 行提供两个
 * 处置动作：**打回**（处置 + 通知原发件人）与**标记作废**（只处置，不发信）。
 *
 * 三态的语义（与服务端判定一致，界面不自行推导）：
 *
 * 1. `processed`（已处理）：已经**有结论**的邮件，两个来源——(a) 已经成功变成作业提交，可以从
 *    作业界面点进去查看（`reason_code` 为 null）；(b) 老师已经打回/作废过的记录，没有提交可看，
 *    只显示"为什么没成为提交"（`reason_code` 非空）。
 * 2. `pending`（待处理）：收到了真实提交、但还没有列入作业**也还没有被老师处置**——这是待办队列。
 *    打回或作废之后它就不再属于这里（处置是队列的终点），因此队列会真的被排空。
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

const CATEGORY_EMPTY: Record<MailRecordCategory, MessageKey> = {
  processed: "review.empty.processed",
  pending: "review.empty.pending",
  other: "review.empty.other",
};

function courseText(course: { course_code: string | null; title: string } | undefined, courseId: string): string {
  if (course === undefined) return shortenId(courseId);
  return formatCourseLabel(course);
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
  // 列表没有课程筛选时，行可能来自多门课；按行里的 course_id 补取课序名称，
  // 否则跨课程记录只能显示课序 UUID 的前几位。
  const rowCourseIds = useMemo(
    () => [...new Set(rows.map((row) => row.course_id).filter((id): id is string => id !== null))],
    [rows],
  );
  const rowSectionQueries = useSectionsForCourses(rowCourseIds);
  const allSectionMap = useMemo(() => {
    const merged = new Map(sectionMap);
    for (const entry of rowSectionQueries) {
      for (const section of entry.data ?? []) merged.set(section.sectionId, section.label);
    }
    return merged;
  }, [sectionMap, rowSectionQueries]);

  // 处置动作（打回 / 作废）：这一页曾经只读，导致"有待处理记录却处理不了"。
  // `processed` 行是已经落库的提交，没有可处置的隔离记录，因此不提供动作。
  const rejectRecord = useRejectMailRecord();
  const dismissRecord = useDismissMailRecord();
  const [action, setAction] = useState<{
    kind: "reject" | "dismiss";
    row: MailRecordView;
  } | null>(null);
  const [actionNote, setActionNote] = useState("");
  const [actionIssue, setActionIssue] = useState<string | null>(null);
  const [success, setSuccess] = useState<string | null>(null);

  const openAction = (kind: "reject" | "dismiss", row: MailRecordView) => {
    setSuccess(null);
    setAction({ kind, row });
    setActionNote("");
    setActionIssue(null);
    rejectRecord.reset();
    dismissRecord.reset();
  };

  const closeAction = () => {
    setAction(null);
    setActionNote("");
    setActionIssue(null);
    rejectRecord.reset();
    dismissRecord.reset();
  };

  const submitAction = () => {
    if (!action) return;
    if (actionNote.length > 500) {
      setActionIssue(t("review.action.noteTooLong"));
      return;
    }
    const input = {
      recordId: action.row.record_id,
      note: actionNote.trim() === "" ? null : actionNote.trim(),
    };
    if (action.kind === "reject") {
      rejectRecord.mutate(input, {
        onSuccess: (result) => {
          // 处置已经生效：通知没发出去是另一句话，不能合并成"失败"。
          setSuccess(
            result.notification === "queued"
              ? t("review.action.rejectedOk")
              : t("review.action.rejectedOkNoNotice"),
          );
          closeAction();
        },
      });
      return;
    }
    dismissRecord.mutate(input, {
      onSuccess: () => {
        setSuccess(t("review.action.dismissedOk"));
        closeAction();
      },
    });
  };

  const actionPending = rejectRecord.isPending || dismissRecord.isPending;

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
      return (
        <div data-testid={`review-reason-${row.record_id}`}>
          {reasonText(row.reason_code)}
        </div>
      );
    }
    // 已处理有**两个来源**：真正变成提交的邮件（`reason_code` 为 null），以及老师已经打回/作废
    // 过的记录（`reason_code` 保留收件原因）。后者没有提交可看，所以只说明"为什么没成为提交"，
    // 不渲染"查看作业"——那个链接会把人带到一份并不存在的提交上。
    if (row.reason_code !== null) {
      return (
        <div>
          <div className="muted">{t("review.disposed")}</div>
          <div data-testid={`review-reason-${row.record_id}`}>
            {reasonText(row.reason_code)}
          </div>
        </div>
      );
    }
    // 变成提交的邮件：显示作业标题；找不到标题时显示短 id（不能把"还没取到"写成"没有作业"）。
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
      testId={`review-records-panel-${category}`}
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
                <span title={row.course_id ?? ""}>{scopeText(row, courseMap, allSectionMap)}</span>
              ),
            },
            {
              key: "detail",
              header: t("review.col.detail"),
              render: renderDetail,
            },
            ...(category === "processed"
              ? []
              : [
                  {
                    key: "action",
                    header: t("review.col.action"),
                    render: (row: MailRecordView) => (
                      <div className="row-actions">
                        <button
                          type="button"
                          className="button button--ghost"
                          data-testid={`review-record-reject-${row.record_id}`}
                          onClick={() => openAction("reject", row)}
                        >
                          {t("review.action.reject")}
                        </button>
                        <button
                          type="button"
                          className="button button--ghost"
                          data-testid={`review-record-dismiss-${row.record_id}`}
                          onClick={() => openAction("dismiss", row)}
                        >
                          {t("review.action.dismiss")}
                        </button>
                      </div>
                    ),
                  },
                ]),
          ]}
        />
      )}
    </Panel>
  );

  const tabs = MAIL_CATEGORIES.map((category) => ({
    id: category,
    label: t(CATEGORY_LABEL[category]),
    content: renderList(category),
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
        </div>
      </Panel>

      {success ? (
        <Banner tone="success" testId="review-records-success">
          {success}
        </Banner>
      ) : null}

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

      {action ? (
        <Modal
          title={
            action.kind === "reject"
              ? t("review.action.rejectTitle")
              : t("review.action.dismissTitle")
          }
          testId="review-record-action"
          onClose={closeAction}
        >
          <div className="stack">
            <p className="muted">
              {action.kind === "reject"
                ? t("review.action.rejectHint")
                : t("review.action.dismissHint")}
            </p>
            <p className="muted" data-testid="review-record-action-target">
              {formatDateTime(action.row.received_at, t("common.none"))} ·{" "}
              {scopeText(action.row, courseMap, sectionMap)}
            </p>
            <Field label={t("review.action.note")} htmlFor="review-record-note">
              <textarea
                id="review-record-note"
                className="input"
                data-testid="review-record-note"
                rows={3}
                maxLength={500}
                value={actionNote}
                onChange={(event) => setActionNote(event.target.value)}
              />
            </Field>
            {actionIssue ? (
              <Banner tone="error" testId="review-record-issue">
                {actionIssue}
              </Banner>
            ) : null}
            {[rejectRecord.error, dismissRecord.error].some(
              (error) => isApiError(error) && error.isForbidden,
            ) ? (
              <Banner tone="error" testId="review-record-forbidden">
                {t("review.action.forbidden")}
              </Banner>
            ) : null}
            {[rejectRecord.error, dismissRecord.error].some(
              (error) => isApiError(error) && error.isConflict,
            ) ? (
              <Banner tone="warn" testId="review-record-conflict">
                {t("review.action.conflict")}
              </Banner>
            ) : null}
            {actionPending ? null : rejectRecord.isError || dismissRecord.isError ? (
              !isApiError(rejectRecord.error ?? dismissRecord.error) ? (
                <ErrorState
                  error={rejectRecord.error ?? dismissRecord.error}
                  testId="review-record-error"
                />
              ) : null
            ) : null}
            <div className="row-actions">
              <button
                type="button"
                className="button"
                data-testid="review-record-confirm"
                disabled={actionPending}
                onClick={submitAction}
              >
                {actionPending ? t("review.action.pending") : t("review.action.confirm")}
              </button>
              <button type="button" className="button button--ghost" onClick={closeAction}>
                {t("common.cancel")}
              </button>
            </div>
          </div>
        </Modal>
      ) : null}
    </div>
  );
}
