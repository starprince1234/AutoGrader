import { useMemo, useState } from "react";
import { t } from "../../i18n";
import { isApiError } from "../../api/problem";
import type { IntakeQuarantineView } from "../../api/types";
import { loadedCourses, useCourses, useSections } from "../../api/directory";
import { useEnrollments } from "../roster/useEnrollments";
import { useAllAssignments } from "../assignments/useAssignments";
import { useSession } from "../../auth/sessionContext";
import { courseLabel as formatCourseLabel } from "../../api/directory";
import {
  Banner,
  EmptyState,
  ErrorState,
  Field,
  LoadMoreActions,
  Modal,
  Panel,
  Spinner,
  StatusPill,
  Table,
} from "../../ui/components";
import { formatDateTime, shortenId } from "../../ui/format";
import {
  loadedQuarantines,
  useCompleteQuarantine,
  useDismissQuarantine,
  useQuarantineMail,
  useSetQuarantineHold,
} from "./useReviewMail";
import { isAttributableReason, REASON_CODES, reasonHint, reasonLabel } from "./reasonCodes";
import {
  readMailFilter,
  withMailFilter,
  type ReviewSearch,
} from "./search";
import { Select } from "../../ui/Select";

/**
 * P13-A：邮件提交复核标签。
 *
 * 页面只做三件事：**按真实记录筛选**、**看记录详情**、**把身份没推出来的记录归属给一个学生**。
 * 三条边界原样反映在实现里：
 *
 * 1. 列表只渲染 `IntakeQuarantineView` 的字段；不请求邮件正文/附件，也不显示
 *    `evaluation_run_id`（隔离记录没有这个字段，证据包属于 P11）。
 * 2. 只有未处理且 `reason_code ∈ {not_uniquely_matched, ambiguous_match}` 的记录有归属入口；
 *    其余原因码是内容类拒绝，服务端会 422，界面不让人点了才知道。
 * 3. URL 的课程/课序只是筛选：篡改成作用域之外的课程由服务端 403，页面显示拒绝而不是空列表。
 *
 * 未关联记录（`scope_*=null`）只会出现在管理员视图里，并明确标为"未关联"；普通 staff 的
 * 可见集合由 B2 的 SQL 谓词决定，页面拿不到它们、也不显示"隐藏行数"。
 */

function scopeText(
  row: IntakeQuarantineView,
  courseMap: Map<string, { course_code: string | null; title: string }>,
  sectionMap: Map<string, string>,
): string {
  // `undefined` 只可能来自旧版服务端（字段未返回）：如实显示"未提供"，不能读成"未关联"。
  if (row.scope_course_id === undefined || row.scope_section_id === undefined) {
    return t("review.mail.scopeMissing");
  }
  if (row.scope_course_id === null) {
    return t("review.mail.scopeUnscoped");
  }
  const course = courseMap.get(row.scope_course_id);
  // 表格里用课程代码（短、可扫读）；完整标题留给 hover 与筛选下拉。
  const courseText = course ? course.course_code : shortenId(row.scope_course_id);
  const sectionText =
    row.scope_section_id === null
      ? t("review.mail.notProvided")
      : (sectionMap.get(row.scope_section_id) ?? shortenId(row.scope_section_id));
  return `${courseText} · ${sectionText}`;
}

export function MailReviewTab({
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
  const allCourses = loadedCourses(courses.data?.pages);
  // 令牌作用域是浏览器端能拿到的**唯一**授权事实：下拉只展示它覆盖的课程/课序，
  // 越权 URL 不会把别的课程"带进来"（服务端仍会 403，这里是不给它出现在界面里的机会）。
  const scopeCourseIds = new Set(session.scope.map((entry) => entry.course_id));
  const courseOptions = isAdmin
    ? allCourses
    : allCourses.filter((course) => scopeCourseIds.has(course.course_id));
  const courseMap = useMemo(
    () => new Map(courseOptions.map((course) => [course.course_id, course] as const)),
    [courseOptions],
  );
  // staff 未带 P04 上下文时预选**第一门有权课程**；URL 指向无权课程时也回落到有权课程，
  // 而不是把无权 ID 发出去再显示 403（这样"不能选其他班级课程"在请求之前就成立）。
  const requestedCourseId = filter.courseId;
  const requestedAllowed =
    requestedCourseId !== null && (isAdmin || scopeCourseIds.has(requestedCourseId));
  const effectiveCourseId = isAdmin
    ? requestedCourseId
    : requestedAllowed
      ? requestedCourseId
      : (courseOptions[0]?.course_id ?? null);
  const sections = useSections(effectiveCourseId);
  const sectionOptions = useMemo(() => {
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

  // 没有课程筛选时也允许查询：服务端按行级 scope 过滤，老师一进来就能看到自己的待处理记录。
  const listEnabled = isAdmin || session.scope.length > 0;
  const query = useQuarantineMail({
    identity,
    enabled: listEnabled,
    filter: {
      courseId: effectiveCourseId,
      sectionId: filter.sectionId,
      resolved: filter.resolved,
      held: filter.held,
      reasonCode: filter.reasonCode,
      cursor: filter.cursor,
    },
  });
  const rows = useMemo(() => loadedQuarantines(query.data?.pages), [query.data]);

  const [detailTarget, setDetailTarget] = useState<IntakeQuarantineView | null>(null);
  const [resolveTarget, setResolveTarget] = useState<IntakeQuarantineView | null>(null);
  const [studentId, setStudentId] = useState("");
  const [note, setNote] = useState("");
  const [issue, setIssue] = useState<string | null>(null);
  const [confirming, setConfirming] = useState(false);
  const [pickerCourseId, setPickerCourseId] = useState("");
  const [matchAssignmentId, setMatchAssignmentId] = useState("");
  const [matchVersionId, setMatchVersionId] = useState("");
  const [success, setSuccess] = useState<string | null>(null);
  const complete = useCompleteQuarantine();
  const hold = useSetQuarantineHold();
  const [dismissTarget, setDismissTarget] = useState<IntakeQuarantineView | null>(null);
  const [dismissNote, setDismissNote] = useState("");
  const [dismissIssue, setDismissIssue] = useState<string | null>(null);
  const [dismissConfirming, setDismissConfirming] = useState(false);
  const dismiss = useDismissQuarantine();

  // 匹配用户：目标课程先取记录 scope；管理员处置未关联行时才需要手动选课程。
  const matchCourseId =
    resolveTarget === null
      ? null
      : (resolveTarget.scope_course_id ?? (pickerCourseId === "" ? null : pickerCourseId));
  const matchSectionId = resolveTarget?.scope_section_id ?? null;
  const assignments = useAllAssignments(
    { courseId: matchCourseId, sectionId: matchSectionId },
    { enabled: matchCourseId !== null },
  );
  const assignmentOptions = assignments.assignments.filter(
    (assignment) => matchCourseId === null || assignment.course_id === matchCourseId,
  );
  const selectedAssignment =
    assignmentOptions.find((assignment) => assignment.assignment_id === matchAssignmentId) ?? null;
  const versionOptions = selectedAssignment?.question_version_ids ?? [];
  const candidateSectionId = selectedAssignment?.section_id ?? matchSectionId;
  const enrollments = useEnrollments(candidateSectionId);
  const candidates = useMemo(
    () => (enrollments.data?.pages ?? []).flatMap((page) => page.items),
    [enrollments.data],
  );
  const selectableStudents = candidates.filter((row) => row.student_id !== null);
  const selectedStudent =
    selectableStudents.find((row) => row.student_id === studentId) ?? null;

  const openResolve = (row: IntakeQuarantineView) => {
    setSuccess(null);
    setResolveTarget(row);
    setStudentId("");
    setNote("");
    setIssue(null);
    setConfirming(false);
    setPickerCourseId(row.scope_course_id ?? "");
    setMatchAssignmentId("");
    setMatchVersionId("");
    complete.reset();
  };

  const closeResolve = () => {
    setResolveTarget(null);
    setStudentId("");
    setNote("");
    setIssue(null);
    setConfirming(false);
    setMatchAssignmentId("");
    setMatchVersionId("");
    complete.reset();
  };

  const submit = () => {
    if (selectedAssignment === null) {
      setIssue(t("review.mail.issueAssignment"));
      return;
    }
    if (matchVersionId === "") {
      setIssue(t("review.mail.issueVersion"));
      return;
    }
    if (studentId === "") {
      setIssue(t("review.mail.issueStudent"));
      return;
    }
    if (note.length > 500) {
      setIssue(t("review.mail.issueNote"));
      return;
    }
    setIssue(null);
    setConfirming(true);
  };

  const confirm = () => {
    if (!resolveTarget || !selectedAssignment || studentId === "" || matchVersionId === "") return;
    complete.mutate(
      {
        quarantineId: resolveTarget.intake_quarantine_id,
        body: {
          student_id: studentId,
          course_id: selectedAssignment.course_id,
          section_id: selectedAssignment.section_id,
          question_version_id: matchVersionId,
          note: note.trim() === "" ? null : note.trim(),
        },
      },
      {
        onSuccess: () => {
          setSuccess(t("review.mail.completedOk"));
          closeResolve();
        },
        onError: () => {
          setConfirming(false);
        },
      },
    );
  };

  const toggleHold = (row: IntakeQuarantineView) => {
    hold.mutate(
      { quarantineId: row.intake_quarantine_id, body: { held: !row.held } },
      {
        onSuccess: (view) => {
          setSuccess(
            view.held ? t("review.mail.heldOk") : t("review.mail.resumedOk"),
          );
        },
      },
    );
  };

  const openDismiss = (row: IntakeQuarantineView) => {
    setSuccess(null);
    setDismissTarget(row);
    setDismissNote("");
    setDismissIssue(null);
    setDismissConfirming(false);
    dismiss.reset();
  };

  const closeDismiss = () => {
    setDismissTarget(null);
    setDismissNote("");
    setDismissIssue(null);
    setDismissConfirming(false);
    dismiss.reset();
  };

  const submitDismiss = () => {
    if (dismissNote.length > 500) {
      setDismissIssue(t("review.mail.issueNote"));
      return;
    }
    setDismissIssue(null);
    setDismissConfirming(true);
  };

  const confirmDismiss = () => {
    if (!dismissTarget) return;
    dismiss.mutate(
      {
        quarantineId: dismissTarget.intake_quarantine_id,
        body: { note: dismissNote.trim() === "" ? null : dismissNote.trim() },
      },
      {
        onSuccess: () => {
          setSuccess(t("review.mail.dismissedOk"));
          closeDismiss();
        },
        onError: () => {
          setDismissConfirming(false);
        },
      },
    );
  };

  const changeFilter = (next: {
    courseId?: string | null;
    sectionId?: string | null;
    resolved?: boolean | null;
    held?: boolean | null;
    reasonCode?: string | null;
  }) => {
    onChange(
      withMailFilter(search, {
        courseId: next.courseId === undefined ? effectiveCourseId : next.courseId,
        sectionId: next.sectionId === undefined ? filter.sectionId : next.sectionId,
        resolved: next.resolved === undefined ? filter.resolved : next.resolved,
        held: next.held === undefined ? filter.held : next.held,
        reasonCode: next.reasonCode === undefined ? filter.reasonCode : next.reasonCode,
      }),
    );
  };

  const forbidden = isApiError(query.error) && query.error.isForbidden;

  const resolutionText = (row: IntakeQuarantineView): string => {
    if (row.held) return t("review.mail.heldState");
    if (!row.resolved) return t("review.mail.unresolved");
    if (row.resolution === "dismissed") return t("review.mail.invalidatedState");
    if (row.resolution === "completed") return t("review.mail.completedState");
    if (row.resolution === "attributed") return t("review.mail.attributedState");
    // 0016 之前的历史行只有 `resolved=true`：如实显示"已处理"，不猜是哪一种。
    return t("review.mail.resolvedState");
  };

  const trailText = (row: IntakeQuarantineView): string | null => {
    if (row.held) {
      const heldActor =
        row.held_by === null
          ? t("review.mail.notProvided")
          : row.held_by === session.subject
            ? t("review.mail.actorYou")
            : shortenId(row.held_by);
      const heldTime =
        row.held_at === null
          ? t("review.mail.notProvided")
          : formatDateTime(row.held_at, t("common.none"));
      return t("review.mail.holdTrail", { actor: heldActor, time: heldTime });
    }
    if (!row.resolved) return null;
    if (row.resolved_at === null && row.resolved_by === null) {
      return t("review.mail.trailUnknown");
    }
    const actor =
      row.resolved_by === null
        ? t("review.mail.notProvided")
        : row.resolved_by === session.subject
          ? t("review.mail.actorYou")
          : shortenId(row.resolved_by);
    const time =
      row.resolved_at === null
        ? t("review.mail.notProvided")
        : formatDateTime(row.resolved_at, t("common.none"));
    return t("review.mail.processingTrail", { actor, time });
  };

  return (
    <div className="stack">
      <h2>{t("review.mail.title")}</h2>

      <Panel title={t("review.mail.filter")}>
        <div className="stack">
          <div className="form-grid">
            <Field label={t("review.mail.course")} htmlFor="review-mail-course">
              <Select
                id="review-mail-course"
                data-testid="review-mail-course"
                value={effectiveCourseId ?? ""}
                onChange={(event) =>
                  changeFilter({ courseId: event.target.value || null, sectionId: null })
                }
              >
                {isAdmin ? <option value="">{t("review.mail.allCourses")}</option> : null}
                {!isAdmin && courseOptions.length === 0 ? (
                  <option value="">{t("review.mail.noCourse")}</option>
                ) : null}
                {courseOptions.map((course) => (
                  <option key={course.course_id} value={course.course_id}>
                    {formatCourseLabel(course)}
                  </option>
                ))}
              </Select>
            </Field>

            <Field label={t("review.mail.section")} htmlFor="review-mail-section">
              <Select
                id="review-mail-section"
                data-testid="review-mail-section"
                value={filter.sectionId ?? ""}
                disabled={effectiveCourseId === null}
                onChange={(event) => changeFilter({ sectionId: event.target.value || null })}
              >
                <option value="">{t("review.mail.allSections")}</option>
                {(sections.data ?? []).map((section) => (
                  <option key={section.sectionId} value={section.sectionId}>
                    {section.label}
                  </option>
                ))}
              </Select>
            </Field>

            <Field label={t("review.mail.resolved")} htmlFor="review-mail-resolved">
              <Select
                id="review-mail-resolved"
                data-testid="review-mail-resolved"
                value={
                  filter.resolved === null
                    ? "all"
                    : filter.resolved
                      ? "processed"
                      : filter.held
                        ? "held"
                        : "pending"
                }
                onChange={(event) => {
                  const value = event.target.value;
                  if (value === "all") changeFilter({ resolved: null, held: null });
                  else if (value === "processed") changeFilter({ resolved: true, held: null });
                  else if (value === "held") changeFilter({ resolved: false, held: true });
                  else changeFilter({ resolved: false, held: false });
                }}
              >
                <option value="pending">{t("review.mail.pendingFilter")}</option>
                <option value="held">{t("review.mail.heldFilter")}</option>
                <option value="processed">{t("review.mail.processedFilter")}</option>
                <option value="all">{t("review.mail.allStates")}</option>
              </Select>
            </Field>

            <Field label={t("review.mail.reason")} htmlFor="review-mail-reason">
              <Select
                id="review-mail-reason"
                data-testid="review-mail-reason"
                value={filter.reasonCode ?? ""}
                onChange={(event) => changeFilter({ reasonCode: event.target.value || null })}
              >
                <option value="">{t("review.mail.allReasons")}</option>
                {REASON_CODES.map((code) => (
                  <option key={code} value={code}>
                    {reasonLabel(code)}
                  </option>
                ))}
              </Select>
            </Field>
          </div>
          {courses.isError ? <ErrorState error={courses.error} /> : null}
          {sections.isError ? <ErrorState error={sections.error} /> : null}
          <p className="muted">{t("review.mail.scopeNote")}</p>
          {success ? (
            <Banner tone="success" testId="review-mail-resolved-ok">
              {success}
            </Banner>
          ) : null}
        </div>
      </Panel>

      <Panel
        title={t(
          filter.resolved === true
            ? "review.mail.processedTitle"
            : filter.held === true
              ? "review.mail.heldTitle"
              : filter.resolved === false
                ? "review.mail.pendingTitle"
                : "review.mail.countLoaded",
          { count: rows.length },
        )}
        actions={
          <LoadMoreActions
            loadedCount={rows.length}
            hasNextPage={query.hasNextPage}
            isFetchingNextPage={query.isFetchingNextPage}
            onLoadMore={() => {
              // 追加下一页；URL 保留"起始位置"语义，不把游标改成最后一页。
              void query.fetchNextPage();
            }}
            testId="review-mail"
          />
        }
      >
        {!listEnabled ? (
          <EmptyState>{t("review.mail.needCourse")}</EmptyState>
        ) : query.isPending ? (
          <Spinner />
        ) : forbidden ? (
          <Banner tone="error" testId="review-mail-forbidden">
            {t("review.mail.forbidden")}
          </Banner>
        ) : query.isError ? (
          <ErrorState error={query.error} testId="review-mail-error" />
        ) : rows.length === 0 ? (
          <div data-testid="review-mail-empty">
            <p className="muted">
              {filter.resolved === false
                ? t("review.mail.emptyUnresolved")
                : t("review.mail.emptyFiltered")}
            </p>
            {!isAdmin ? <p className="muted">{t("review.mail.emptyStaffHint")}</p> : null}
          </div>
        ) : (
          <Table<IntakeQuarantineView>
            testId="review-mail-table"
            label={t("review.mail.tableLabel")}
            rows={rows}
            getRowKey={(row) => row.intake_quarantine_id}
            columns={[
              {
                key: "subject",
                header: t("review.mail.summary"),
                render: (row) => (
                  <div>
                    <strong title={row.subject ?? undefined}>
                      {row.subject ?? t("review.mail.noSubject")}
                    </strong>
                    <div className="muted">
                      {formatDateTime(row.recorded_at, t("common.none")).slice(0, 16)} ·{" "}
                      <button
                        type="button"
                        className="muted"
                        data-testid={`review-mail-detail-${row.intake_quarantine_id}`}
                        onClick={() => setDetailTarget(row)}
                        style={{
                          border: 0,
                          background: "none",
                          padding: 0,
                          cursor: "pointer",
                          textDecoration: "underline",
                        }}
                      >
                        {t("review.mail.detailAction")}
                      </button>
                    </div>
                  </div>
                ),
              },
              {
                key: "reason",
                header: t("review.mail.failureReason"),
                render: (row) => (
                  <div>
                    <div>{reasonLabel(row.reason_code)}</div>
                    <div className="muted">{reasonHint(row.reason_code)}</div>
                  </div>
                ),
              },
              {
                key: "scope",
                header: t("review.mail.scope"),
                render: (row) => <span title={row.scope_course_id ?? ""}>{scopeText(row, courseMap, sectionMap)}</span>,
              },
              {
                key: "state",
                header: t("review.mail.state"),
                render: (row) => (
                  <div>
                    <StatusPill
                      tone={
                        row.held
                          ? "warn"
                          : !row.resolved
                            ? "warn"
                            : row.resolution === "dismissed"
                              ? "idle"
                              : "ok"
                      }
                    >
                      {resolutionText(row)}
                    </StatusPill>
                    {trailText(row) ? <div className="muted">{trailText(row)}</div> : null}
                  </div>
                ),
              },
              {
                key: "action",
                header: t("review.mail.action"),
                render: (row) => (
                  <div className="row-actions">
                    {row.resolved || !isAttributableReason(row.reason_code) ? null : (
                      <button
                        type="button"
                        className="button"
                        data-testid={`review-mail-resolve-${row.intake_quarantine_id}`}
                        onClick={() => openResolve(row)}
                      >
                        {t("review.mail.resolveAction")}
                      </button>
                    )}
                    {row.resolved ? null : (
                      <button
                        type="button"
                        className="button button--ghost"
                        data-testid={`review-mail-hold-${row.intake_quarantine_id}`}
                        onClick={() => toggleHold(row)}
                      >
                        {row.held ? t("review.mail.resumeAction") : t("review.mail.holdAction")}
                      </button>
                    )}
                    {row.resolved ? null : (
                      <button
                        type="button"
                        className="button button--ghost"
                        data-testid={`review-mail-dismiss-${row.intake_quarantine_id}`}
                        onClick={() => openDismiss(row)}
                      >
                        {t("review.mail.dismissAction")}
                      </button>
                    )}
                  </div>
                ),
              },
            ]}
          />
        )}
      </Panel>

      {detailTarget ? (
        <Modal
          title={t("review.mail.detailTitle")}
          testId="review-mail-detail-drawer"
          onClose={() => setDetailTarget(null)}
        >
          <div className="stack">
            <p className="muted">{t("review.mail.detailHint")}</p>
            <p>
              {t("review.mail.recordedAt")}：{formatDateTime(detailTarget.recorded_at, t("common.none"))}
            </p>
            <p>{t("review.mail.scope")}：{scopeText(detailTarget, courseMap, sectionMap)}</p>
            <p>
              {t("review.mail.summary")}：
              {detailTarget.subject ?? t("review.mail.noSubject")}
            </p>
            <p>
              {t("review.mail.mailbox")}：
              {detailTarget.mailbox === "" ? t("review.mail.notProvided") : detailTarget.mailbox}
            </p>
            <p>
              {t("review.mail.reason")}：{reasonLabel(detailTarget.reason_code)}
            </p>
            <p className="muted">{reasonHint(detailTarget.reason_code)}</p>
            <p>
              {t("review.mail.state")}：
              {resolutionText(detailTarget)}
              {trailText(detailTarget) ? ` · ${trailText(detailTarget)}` : ""}
            </p>
            {detailTarget.resolution_note ? (
              <p>
                {t("review.mail.resolutionNote")}：{detailTarget.resolution_note}
              </p>
            ) : null}
          </div>
        </Modal>
      ) : null}

      {resolveTarget ? (
        <Modal
          title={t("review.mail.resolveTitle")}
          testId="review-mail-resolve-drawer"
          onClose={closeResolve}
        >
          <div className="stack">
            <p className="muted">{t("review.mail.resolveScopeNote")}</p>
            <p className="muted" data-testid="review-mail-resolve-target">
              {reasonLabel(resolveTarget.reason_code)} ·{" "}
              {formatDateTime(resolveTarget.recorded_at, t("common.none"))} ·{" "}
              {scopeText(resolveTarget, courseMap, sectionMap)}
            </p>

            {resolveTarget.scope_course_id === null ? (
              <Field label={t("review.mail.course")} htmlFor="review-resolve-course">
                <Select
                  id="review-resolve-course"
                  data-testid="review-resolve-course"
                  value={pickerCourseId}
                  onChange={(event) => {
                    setPickerCourseId(event.target.value);
                    setMatchAssignmentId("");
                    setMatchVersionId("");
                    setStudentId("");
                  }}
                >
                  <option value="">{t("common.selectPrompt")}</option>
                  {courseOptions.map((course) => (
                    <option key={course.course_id} value={course.course_id}>
                      {formatCourseLabel(course)}
                    </option>
                  ))}
                </Select>
              </Field>
            ) : null}

            <Field label={t("review.mail.assignment")} htmlFor="review-resolve-assignment">
              <Select
                id="review-resolve-assignment"
                data-testid="review-resolve-assignment"
                value={matchAssignmentId}
                disabled={matchCourseId === null}
                onChange={(event) => {
                  const assignmentId = event.target.value;
                  setMatchAssignmentId(assignmentId);
                  const assignment = assignmentOptions.find(
                    (option) => option.assignment_id === assignmentId,
                  );
                  setMatchVersionId(assignment?.question_version_ids[0] ?? "");
                  setStudentId("");
                }}
              >
                <option value="">{t("common.selectPrompt")}</option>
                {assignmentOptions.map((assignment) => (
                  <option key={assignment.assignment_id} value={assignment.assignment_id}>
                    {assignment.title}
                  </option>
                ))}
              </Select>
            </Field>

            <Field label={t("review.mail.questionVersion")} htmlFor="review-resolve-version">
              <Select
                id="review-resolve-version"
                data-testid="review-resolve-version"
                value={matchVersionId}
                disabled={selectedAssignment === null}
                onChange={(event) => {
                  setMatchVersionId(event.target.value);
                  setStudentId("");
                }}
              >
                <option value="">{t("common.selectPrompt")}</option>
                {versionOptions.map((versionId) => (
                  <option key={versionId} value={versionId}>
                    {shortenId(versionId)}
                  </option>
                ))}
              </Select>
            </Field>

            {assignments.isError ? (
              <ErrorState error={assignments.error} testId="review-resolve-assignments-error" />
            ) : null}
            {matchCourseId !== null &&
            !assignments.isPending &&
            assignmentOptions.length === 0 ? (
              <Banner tone="info" testId="review-resolve-no-assignments">
                {t("review.mail.noAssignments")}
              </Banner>
            ) : null}

            <Field label={t("review.mail.student")} htmlFor="review-resolve-student">
              <Select
                id="review-resolve-student"
                data-testid="review-resolve-student"
                value={studentId}
                disabled={candidateSectionId === null}
                onChange={(event) => setStudentId(event.target.value)}
              >
                <option value="">{t("common.selectPrompt")}</option>
                {selectableStudents.map((row) => (
                  <option key={row.enrollment_id} value={row.student_id as string}>
                    {row.display_name} · {row.student_number}
                  </option>
                ))}
              </Select>
            </Field>

            {enrollments.isError ? (
              <ErrorState error={enrollments.error} testId="review-resolve-candidates-error" />
            ) : null}
            {candidateSectionId !== null && !enrollments.isPending && selectableStudents.length === 0 ? (
              <Banner tone="info" testId="review-resolve-no-candidates">
                {t("review.mail.noCandidates")}
              </Banner>
            ) : null}

            <Field
              label={t("review.mail.note")}
              htmlFor="review-resolve-note"
              error={issue}
              hint={t("review.mail.noteHint", { count: note.length })}
            >
              <textarea
                id="review-resolve-note"
                data-testid="review-resolve-note"
                rows={3}
                maxLength={500}
                value={note}
                onChange={(event) => setNote(event.target.value)}
              />
            </Field>

            {complete.isError && !complete.isPending ? (
              isApiError(complete.error) && complete.error.isConflict ? (
                <Banner tone="warn" testId="review-resolve-conflict">
                  {t("review.mail.conflict")}
                </Banner>
              ) : isApiError(complete.error) && complete.error.isForbidden ? (
                <Banner tone="error" testId="review-resolve-forbidden">
                  {t("review.mail.resolveForbidden")}
                </Banner>
              ) : (
                <ErrorState error={complete.error} testId="review-resolve-error" />
              )
            ) : null}

            <div className="inline">
              <button
                type="button"
                className="button"
                data-testid="review-resolve-continue"
                disabled={
                  complete.isPending ||
                  selectedAssignment === null ||
                  matchVersionId === "" ||
                  studentId === "" ||
                  selectedStudent === null ||
                  candidateSectionId === null
                }
                onClick={submit}
              >
                {t("review.mail.resolveContinue")}
              </button>
              <button type="button" className="button button--ghost" onClick={closeResolve}>
                {t("common.cancel")}
              </button>
            </div>
          </div>
        </Modal>
      ) : null}

      {confirming && resolveTarget ? (
        <Modal
          title={t("review.mail.confirmTitle")}
          testId="review-mail-confirm"
          onClose={() => setConfirming(false)}
        >
          <div className="stack">
            <p data-testid="review-mail-confirm-text">
              {t("review.mail.confirmText", {
                name: selectedStudent?.display_name ?? "",
                number: selectedStudent?.student_number ?? "",
              })}
            </p>
            <p className="muted">
              {t("review.mail.confirmContext", {
                mailbox:
                  resolveTarget.mailbox === ""
                    ? t("review.mail.notProvided")
                    : resolveTarget.mailbox,
                recordedAt: formatDateTime(resolveTarget.recorded_at, t("common.none")),
              })}
            </p>
            <p className="muted">{t("review.mail.confirmNoEvaluation")}</p>
            <div className="inline">
              <button
                type="button"
                className="button"
                data-testid="review-mail-confirm-submit"
                disabled={complete.isPending}
                onClick={confirm}
              >
                {complete.isPending ? t("review.mail.submitting") : t("review.mail.confirmSubmit")}
              </button>
              <button
                type="button"
                className="button button--ghost"
                onClick={() => setConfirming(false)}
              >
                {t("review.mail.back")}
              </button>
            </div>
          </div>
        </Modal>
      ) : null}

      {dismissTarget ? (
        <Modal
          title={t("review.mail.dismissTitle")}
          testId="review-mail-dismiss-drawer"
          onClose={closeDismiss}
        >
          <div className="stack">
            <p className="muted">{t("review.mail.dismissHint")}</p>
            <p className="muted">
              {reasonLabel(dismissTarget.reason_code)} ·{" "}
              {formatDateTime(dismissTarget.recorded_at, t("common.none"))} ·{" "}
              {scopeText(dismissTarget, courseMap, sectionMap)}
            </p>
            {dismissConfirming ? (
              <div className="stack">
                <p>{t("review.mail.dismissConfirmText")}</p>
                <div className="row-actions">
                  <button
                    type="button"
                    className="button"
                    data-testid="review-mail-dismiss-confirm"
                    disabled={dismiss.isPending}
                    onClick={confirmDismiss}
                  >
                    {dismiss.isPending
                      ? t("review.mail.submitting")
                      : t("review.mail.dismissConfirm")}
                  </button>
                  <button
                    type="button"
                    className="button button--ghost"
                    onClick={() => setDismissConfirming(false)}
                  >
                    {t("review.mail.back")}
                  </button>
                </div>
              </div>
            ) : (
              <>
                <Field label={t("review.mail.dismissNote")} htmlFor="review-dismiss-note">
                  <textarea
                    id="review-dismiss-note"
                    data-testid="review-dismiss-note"
                    rows={3}
                    maxLength={500}
                    value={dismissNote}
                    onChange={(event) => setDismissNote(event.target.value)}
                  />
                </Field>
                <p className="muted">
                  {t("review.mail.noteHint", { count: dismissNote.length })}
                </p>
                {dismissIssue ? (
                  <Banner tone="error" testId="review-dismiss-issue">
                    {dismissIssue}
                  </Banner>
                ) : null}
                {isApiError(dismiss.error) && dismiss.error.isForbidden ? (
                  <Banner tone="error" testId="review-dismiss-forbidden">
                    {t("review.mail.resolveForbidden")}
                  </Banner>
                ) : null}
                {isApiError(dismiss.error) && dismiss.error.isConflict ? (
                  <Banner tone="warn" testId="review-dismiss-conflict">
                    {t("review.mail.conflict")}
                  </Banner>
                ) : null}
                {dismiss.isError &&
                !(isApiError(dismiss.error) && (dismiss.error.isForbidden || dismiss.error.isConflict)) ? (
                  <ErrorState error={dismiss.error} testId="review-dismiss-error" />
                ) : null}
                <div className="row-actions">
                  <button
                    type="button"
                    className="button"
                    data-testid="review-mail-dismiss-submit"
                    onClick={submitDismiss}
                  >
                    {t("review.mail.dismissAction")}
                  </button>
                  <button type="button" className="button button--ghost" onClick={closeDismiss}>
                    {t("common.cancel")}
                  </button>
                </div>
              </>
            )}
          </div>
        </Modal>
      ) : null}
    </div>
  );
}

