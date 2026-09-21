import { useMemo, useState } from "react";
import { t } from "../../i18n";
import { isApiError } from "../../api/problem";
import type { IntakeQuarantineView } from "../../api/types";
import { loadedCourses, useCourses, useSections } from "../../api/directory";
import { useEnrollments } from "../roster/useEnrollments";
import {
  Banner,
  ErrorState,
  Field,
  LoadMoreActions,
  Modal,
  PageTitle,
  Panel,
  Spinner,
  StatusPill,
  Table,
} from "../../ui/components";
import { formatDateTime, isUuid, shortenId } from "../../ui/format";
import {
  isAttributableReason,
  loadedQuarantines,
  useQuarantines,
  useResolveQuarantine,
} from "./useQuarantines";

/**
 * 隔离区复核台（CHG-0014；可归属集合见 CHG-0021）。教师/助教/管理员在课程作用域内可以读，并手动归属。
 *
 * 归属的两条约束被原样反映到界面上：
 *
 * 1. **只有"身份没推出来"的记录可归属**（`CHG-0021` §2.1）：`not_uniquely_matched` /
 *    `ambiguous_match` 之外的原因码是内容类拒绝（附件超限、归档炸弹……），人工补一个学生修不好
 *    它们，服务端一律 `422`。因此本页**不显示**归属按钮并说明原因，而不是让教师点了才知道——
 *    界面不该用一次失败去表达一条已经写下来的规则。
 * 2. **归属不静默重评**：它记录人的决定，评测仍走正常提交路径。界面上因此不出现"重新评测"。
 *
 * 学生选择器：B1 没有"当前用户资料"端点，也没有全量用户列表（`GET /api/v1/users` 仅管理员）。
 * 教师能读的是自己课程/课序的**名单**，所以候选来自「名单」（`student_id` 就是契约要的平台
 * 用户 ID，与 `CHG-0007` 加进名单的是同一个键）。名单里 `student_id` 为空的行是"尚未绑定账号"，
 * 它们**不会**被当作候选人——猜一个 ID 出来是伪造数据，不是降级。
 */

function StudentPicker({ onPick }: { onPick: (studentId: string) => void }) {
  const [open, setOpen] = useState(false);
  const [courseId, setCourseId] = useState("");
  const [sectionId, setSectionId] = useState("");
  // 课程目录只在展开后才请求：这个选择器是可选的辅助，不该让打开弹窗就产生一次网络调用。
  const courses = useCourses({ enabled: open });
  const courseOptions = loadedCourses(courses.data?.pages);
  const sections = useSections(courseId === "" ? null : courseId);
  const enrollments = useEnrollments(sectionId === "" ? null : sectionId);
  const candidates = useMemo(
    () => (enrollments.data?.pages ?? []).flatMap((page) => page.items),
    [enrollments.data],
  );

  return (
    <div className="stack">
      <button
        type="button"
        className="button button--ghost"
        data-testid="toggle-student-picker"
        onClick={() => setOpen((previous) => !previous)}
      >
        {t("quarantine.picker")}
      </button>

      {open ? (
        <div className="stack case-evidence" data-testid="student-picker">
          <p className="muted">{t("quarantine.pickerHint")}</p>

          <Field label={t("quarantine.pickerCourse")} htmlFor="picker-course">
            <select
              id="picker-course"
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

          <Field label={t("quarantine.pickerSection")} htmlFor="picker-section">
            <select
              id="picker-section"
              value={sectionId}
              disabled={courseId === ""}
              onChange={(event) => setSectionId(event.target.value)}
            >
              <option value="">{t("common.selectPrompt")}</option>
              {(sections.data ?? []).map((section) => (
                <option key={section.sectionId} value={section.sectionId}>
                  {section.label}
                </option>
              ))}
            </select>
          </Field>

          <Field label={t("quarantine.pickerStudent")} htmlFor="picker-student">
            <select
              id="picker-student"
              data-testid="picker-student"
              value=""
              disabled={sectionId === ""}
              onChange={(event) => onPick(event.target.value)}
            >
              <option value="">{t("common.selectPrompt")}</option>
              {candidates
                .filter((row) => row.student_id !== null)
                .map((row) => (
                  <option key={row.enrollment_id} value={row.student_id as string}>
                    {(row.display_name ?? t("common.none")) +
                      " · " +
                      (row.student_number ?? t("quarantine.pickerNoAccount")) +
                      " · " +
                      shortenId(row.student_id as string)}
                  </option>
                ))}
            </select>
          </Field>

          {sections.isError ? <ErrorState error={sections.error} /> : null}
          {enrollments.isError ? <ErrorState error={enrollments.error} /> : null}
          {sectionId !== "" && candidates.every((row) => row.student_id === null) ? (
            <Banner tone="info" testId="student-picker-empty">
              {t("quarantine.pickerEmpty")}
            </Banner>
          ) : null}
        </div>
      ) : null}
    </div>
  );
}

export function QuarantinePage() {
  const [resolved, setResolved] = useState(false);
  const [reasonCode, setReasonCode] = useState("");
  const [target, setTarget] = useState<IntakeQuarantineView | null>(null);
  const [studentId, setStudentId] = useState("");
  const [note, setNote] = useState("");
  const [issue, setIssue] = useState<string | null>(null);
  const [success, setSuccess] = useState<string | null>(null);

  const filter = useMemo(
    () => ({ resolved, reasonCode: reasonCode.trim() === "" ? null : reasonCode.trim() }),
    [resolved, reasonCode],
  );
  const quarantines = useQuarantines(filter);
  const rows = useMemo(() => loadedQuarantines(quarantines.data?.pages), [quarantines.data]);
  const resolve = useResolveQuarantine();

  const closeModal = () => {
    setTarget(null);
    setStudentId("");
    setNote("");
    setIssue(null);
    resolve.reset();
  };

  const submit = () => {
    const trimmed = studentId.trim();
    if (trimmed === "") {
      setIssue(t("quarantine.issue.studentIdRequired"));
      return;
    }
    // 契约把 `student_id` 声明为 `Uuid`：格式不符时不发请求，因为那只会换来一个 422。
    if (!isUuid(trimmed)) {
      setIssue(t("quarantine.issue.studentIdShape"));
      return;
    }
    if (note.length > 500) {
      setIssue(t("quarantine.issue.noteTooLong"));
      return;
    }
    setIssue(null);
    resolve.mutate(
      {
        quarantineId: target?.intake_quarantine_id as string,
        // `additionalProperties: false`：只发契约声明的两个键。
        body: { student_id: trimmed, note: note.trim() === "" ? null : note.trim() },
      },
      {
        onSuccess: (view) => {
          setSuccess(
            t("quarantine.resolvedOk", { student: view.resolved_student_id ?? trimmed }),
          );
          closeModal();
        },
      },
    );
  };

  return (
    <div className="stack">
      <PageTitle>{t("quarantine.title")}</PageTitle>

      <Panel title={t("quarantine.filter")}>
        <p className="muted">{t("quarantine.intro")}</p>
        <div className="form-grid">
          <Field label={t("quarantine.resolvedFilter")} htmlFor="quarantine-resolved">
            <select
              id="quarantine-resolved"
              data-testid="quarantine-resolved-filter"
              value={resolved ? "true" : "false"}
              onChange={(event) => setResolved(event.target.value === "true")}
            >
              <option value="false">{t("quarantine.resolvedFalse")}</option>
              <option value="true">{t("quarantine.resolvedTrue")}</option>
            </select>
          </Field>
          <Field label={t("quarantine.reasonFilter")} htmlFor="quarantine-reason">
            <input
              id="quarantine-reason"
              value={reasonCode}
              onChange={(event) => setReasonCode(event.target.value)}
            />
          </Field>
        </div>
        <p className="muted">{t("quarantine.resolvedAlwaysSent")}</p>
        <p className="muted">{t("quarantine.reasonFilterHint")}</p>
        <p className="muted">{t("quarantine.noContent")}</p>
        {success ? (
          <Banner tone="success" testId="quarantine-resolved-ok">
            {success}
          </Banner>
        ) : null}
      </Panel>

      <Panel
        title={t("quarantine.countLoaded", { count: rows.length })}
        actions={
          <LoadMoreActions
            loadedCount={rows.length}
            hasNextPage={quarantines.hasNextPage}
            isFetchingNextPage={quarantines.isFetchingNextPage}
            onLoadMore={() => {
              void quarantines.fetchNextPage();
            }}
            testId="quarantines"
          />
        }
      >
        {quarantines.isPending ? (
          <Spinner />
        ) : quarantines.isError ? (
          <ErrorState error={quarantines.error} />
        ) : rows.length === 0 ? (
          <div data-testid="quarantine-empty">
            <p className="muted">{t("quarantine.empty")}</p>
            <p className="muted">{t("quarantine.emptyHint")}</p>
          </div>
        ) : (
          <Table<IntakeQuarantineView>
            testId="quarantine-table"
            label={t("quarantine.countLoaded", { count: rows.length })}
            rows={rows}
            getRowKey={(row) => row.intake_quarantine_id}
            columns={[
              {
                key: "recorded_at",
                header: t("quarantine.recordedAt"),
                render: (row) => formatDateTime(row.recorded_at, t("common.none")),
              },
              {
                key: "reason_code",
                header: t("quarantine.reasonCode"),
                render: (row) => <span className="mono-id">{row.reason_code}</span>,
              },
              {
                key: "detail",
                header: t("quarantine.detail"),
                render: (row) => row.detail,
              },
              {
                key: "mailbox",
                header: t("quarantine.mailbox"),
                render: (row) => row.mailbox,
              },
              {
                key: "message_id",
                header: t("quarantine.messageId"),
                render: (row) => (
                  <span className="mono-id">
                    {row.message_id ?? t("common.none")}
                    {row.uid === null ? "" : ` · UID ${row.uid}`}
                  </span>
                ),
              },
              {
                key: "state",
                header: t("quarantine.state"),
                render: (row) => (
                  <span className="inline">
                    <StatusPill tone={row.resolved ? "ok" : "warn"}>
                      {row.resolved ? t("quarantine.stateResolved") : t("quarantine.stateUnresolved")}
                    </StatusPill>
                    {row.resolved_student_id ? (
                      <span className="mono-id" title={row.resolved_student_id}>
                        {shortenId(row.resolved_student_id)}
                      </span>
                    ) : null}
                  </span>
                ),
              },
              {
                key: "action",
                header: t("quarantine.action"),
                render: (row) => {
                  if (row.resolved) return <span className="muted">{t("common.none")}</span>;
                  // 不可归属的原因码不显示按钮（CHG-0021 §2.1）：归属只补身份，
                  // 修不好"附件被体积上限拒绝"这类内容类拒绝。让教师点一次拿 422 是用失败表达规则。
                  if (!isAttributableReason(row.reason_code)) {
                    return (
                      <span
                        className="muted"
                        data-testid={`not-attributable-${row.intake_quarantine_id}`}
                      >
                        {t("quarantine.notAttributable", { reason: row.reason_code })}
                      </span>
                    );
                  }
                  return (
                    <button
                      type="button"
                      className="button button--ghost"
                      data-testid={`resolve-${row.intake_quarantine_id}`}
                      onClick={() => {
                        setSuccess(null);
                        setTarget(row);
                      }}
                    >
                      {t("quarantine.resolve")}
                    </button>
                  );
                },
              },
            ]}
          />
        )}
      </Panel>

      {target ? (
        <Modal title={t("quarantine.resolveTitle")} testId="resolve-modal" onClose={closeModal}>
          <div className="stack">
            <ul className="muted">
              <li>{t("quarantine.resolveRule1")}</li>
              <li>{t("quarantine.resolveRule2")}</li>
            </ul>
            <p className="muted" data-testid="resolve-target">
              {t("quarantine.reasonCode")}：{target.reason_code} · {target.detail}
            </p>

            <Field
              label={t("quarantine.studentId")}
              htmlFor="resolve-student"
              error={issue}
            >
              <input
                id="resolve-student"
                data-testid="resolve-student-id"
                value={studentId}
                onChange={(event) => setStudentId(event.target.value)}
              />
            </Field>
            <p className="muted">{t("quarantine.studentIdHint")}</p>

            <StudentPicker onPick={setStudentId} />

            <Field label={t("quarantine.note")} htmlFor="resolve-note">
              <textarea
                id="resolve-note"
                data-testid="resolve-note"
                rows={3}
                value={note}
                onChange={(event) => setNote(event.target.value)}
              />
            </Field>

            {resolve.isError ? (
              <ErrorState error={resolve.error} testId="resolve-error" />
            ) : null}

            <div className="inline">
              <button
                type="button"
                className="button"
                data-testid="submit-resolve"
                disabled={resolve.isPending}
                onClick={submit}
              >
                {resolve.isPending ? t("quarantine.submitting") : t("quarantine.submit")}
              </button>
              <button type="button" className="button button--ghost" onClick={closeModal}>
                {t("common.cancel")}
              </button>
            </div>
            {isApiError(resolve.error) && resolve.error.isConflict ? (
              <Banner tone="warn" testId="resolve-conflict">
                {t("common.errorTitle")}（409）
              </Banner>
            ) : null}
          </div>
        </Modal>
      ) : null}
    </div>
  );
}
