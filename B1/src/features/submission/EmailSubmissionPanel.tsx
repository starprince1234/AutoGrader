import { useEffect, useState } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { Link } from "@tanstack/react-router";
import { apiRequest } from "../../api/client";
import { describeApiError, isApiError } from "../../api/problem";
import type {
  AssignmentPublic,
  CurrentUser,
  EmailVerificationChallenge,
  EmailVerificationChallengeRecord,
  EmailVerificationChallengeStatus,
} from "../../api/types";
import { queryKeys } from "../../app/queryClient";
import { t } from "../../i18n";
import { Banner, InlineLoader, Panel } from "../../ui/components";
import { formatDateTime } from "../../ui/format";
import { useEmailSubmissionRecords, useSubmissionView } from "./useSubmission";
import { emailHintText, emailHintTone } from "./emailLabels";
import type { QuestionVersionRow } from "../questions/useQuestions";

/**
 * 邮件提交面板（CHG-0032 §7.2）。
 *
 * 学生看到的必须是**可复制、可核对的完整格式**：
 * 1. 正文五行：核验码 / 作业编号 / 课序 / 姓名 / 学号。核验码在申请后填入，作业编号来自作业，
 *    姓名与学号来自 `GET /api/v1/me`（自动填入，可编辑）；**身份由核验码证明**，发件邮箱不限，
 *    只有发件账号恰好是核验码所有者时 B2 才严格比对姓名/学号；课序取本人所在课序的显示名。
 * 2. 附件映射：第 N 题 → `N.txt`（1 基题号，作业顺序）；附件由学生在自己的邮箱客户端添加。
 * 3. 收件邮箱：核验码签发响应带 `mailbox_address`（部署配置；未声明为 null），面板显示并支持复制，
 *    否则学生无法在平台内得知该发往哪里。
 * 4. 申请下方给一句结果提示：成功绿色「提交成功」，失败红色「提交失败」，pending 灰色；
 *    学生端**不展示失败原因**（原因码留给教师复核），页面只回答"交上了没有"。
 * 5. 「我的邮件提交记录」：**一次申请核验码 = 一条记录**（服务端保存），显示申请时间与
 *    等待/成功/失败；关掉面板或刷新页面后仍能回看，工作区顶部也常驻最近一条状态。
 *
 * 状态轮询只在 `pending` 时进行；`received` 后按 `submissions[]` 提供逐题结果入口，
 * 并通知调用方刷新每题状态（成绩行是唯一权威状态来源）。
 */

type CopyState = "idle" | "ok" | "manual";

async function copyText(text: string): Promise<CopyState> {
  const clipboard = globalThis.navigator?.clipboard;
  if (!clipboard || typeof clipboard.writeText !== "function") return "manual";
  try {
    await clipboard.writeText(text);
    return "ok";
  } catch {
    return "manual";
  }
}

/** 状态短提示：成功绿色、失败红色、等待灰色；文案与记录列表/工作区顶部共用。 */
function SubmitHint({ status }: { status: EmailVerificationChallengeStatus }) {
  const tone = emailHintTone(status.state);
  const testId =
    tone === "ok"
      ? "email-submit-success"
      : tone === "pending"
        ? "email-submit-pending"
        : "email-submit-failure";
  return (
    <p
      className={`email-submit-hint email-submit-hint--${tone}`}
      data-testid={testId}
      role={tone === "bad" ? "alert" : "status"}
    >
      {emailHintText(status.state)}
    </p>
  );
}

/** 一条邮件提交记录（一次核验码申请）的状态与逐题提交。 */
function EmailSubmissionRecordRow({
  record,
  questionRows,
}: {
  record: EmailVerificationChallengeRecord;
  questionRows: QuestionVersionRow[];
}) {
  const tone = emailHintTone(record.state);
  return (
    <div
      className="email-record"
      data-testid={`email-record-${record.challenge_id}`}
    >
      <div className="inline">
        <span className="muted">
          {t("submit.email.recordAppliedAt")}：
          {formatDateTime(record.created_at, t("common.none"))}
        </span>
        <span
          className={`email-submit-hint email-submit-hint--${tone}`}
          data-testid={`email-record-state-${record.challenge_id}`}
        >
          {emailHintText(record.state)}
        </span>
      </div>
      {record.submissions.length > 0 ? (
        <ul>
          {record.submissions.map((item) => {
            const row = questionRows.find(
              (candidate) =>
                candidate.questionVersionId === item.question_version_id,
            );
            return (
              <EmailSubmissionResult
                key={item.submission_id}
                submissionId={item.submission_id}
                title={t("submit.email.resultLine", {
                  ordinal: row?.ordinal ?? "?",
                  title: row?.question?.title ?? item.question_version_id,
                })}
              />
            );
          })}
        </ul>
      ) : null}
    </div>
  );
}

/** 逐题结果：用提交视图把 `submissions[]` 推进到评测运行，再给出报告深链。 */
function EmailSubmissionResult({
  submissionId,
  title,
}: {
  submissionId: string;
  title: string;
}) {
  const view = useSubmissionView(submissionId);
  const runId = view.data?.evaluation_run_id ?? null;
  return (
    <li data-testid={`email-submission-${submissionId}`}>
      <span>{title}</span>
      {view.isPending ? (
        <span className="muted">
          {" "}
          <InlineLoader />
        </span>
      ) : view.isError ? (
        <span className="muted"> · {describeApiError(view.error)}</span>
      ) : view.data ? (
        <span className="muted">
          {" "}
          · {t(`grades.state.${view.data.state}`)}
          {runId !== null ? (
            <>
              {" · "}
              <Link
                to="/grades"
                search={{ evaluation_run_id: runId }}
                data-testid={`email-result-link-${submissionId}`}
              >
                {t("submit.email.viewResult")}
              </Link>
            </>
          ) : null}
        </span>
      ) : null}
    </li>
  );
}

export function EmailSubmissionPanel({
  assignment,
  courseId,
  sectionId,
  sectionLabel,
  currentUser,
  questionRows,
  onReceived,
}: {
  assignment: AssignmentPublic;
  courseId: string | null;
  sectionId: string | null;
  /** 本人课程/课序的显示名（学生对自己所在课序有 course read）。 */
  sectionLabel: string | null;
  currentUser: CurrentUser | undefined;
  /** 作业题目（含题号与标题），用于附件映射表。 */
  questionRows: QuestionVersionRow[];
  /** `received` 后刷新每题状态（成绩行是权威来源）。 */
  onReceived: () => void;
}) {
  const [challenge, setChallenge] = useState<EmailVerificationChallenge | null>(null);
  const [copyState, setCopyState] = useState<CopyState>("idle");
  const [addressCopy, setAddressCopy] = useState<CopyState>("idle");
  const queryClient = useQueryClient();
  const records = useEmailSubmissionRecords(assignment.assignment_id);
  const [overrides, setOverrides] = useState<{
    code?: string;
    section?: string;
    name?: string;
    studentNumber?: string;
  }>({});

  const issue = useMutation({
    mutationFn: async (): Promise<EmailVerificationChallenge> => {
      if (courseId === null || sectionId === null) {
        throw new Error(t("submit.email.missingScope"));
      }
      return apiRequest<EmailVerificationChallenge>(
        "/api/v1/email-verification/challenges",
        {
          method: "POST",
          body: {
            course_id: courseId,
            section_id: sectionId,
            assignment_id: assignment.assignment_id,
          },
        },
      );
    },
    onSuccess: (created) => {
      setChallenge(created);
      setCopyState("idle");
      setAddressCopy("idle");
      setOverrides((previous) => ({ ...previous, code: created.code }));
      // 一次申请 = 一条记录：立刻刷新记录列表，学生能在“我的邮件提交记录”里看到它。
      void queryClient.invalidateQueries({
        queryKey: queryKeys.emailVerificationRecords(assignment.assignment_id),
      });
    },
  });

  const status = useQuery({
    queryKey: queryKeys.emailVerificationChallenge(challenge?.challenge_id ?? ""),
    enabled: challenge !== null,
    queryFn: async (): Promise<EmailVerificationChallengeStatus> =>
      apiRequest<EmailVerificationChallengeStatus>(
        `/api/v1/email-verification/challenges/${encodeURIComponent(
          (challenge as EmailVerificationChallenge).challenge_id,
        )}`,
      ),
    refetchInterval: (query) =>
      (query.state.data as EmailVerificationChallengeStatus | undefined)?.state === "pending"
        ? 5000
        : false,
  });

  const received = status.data?.state === "received";
  useEffect(() => {
    if (received) onReceived();
  }, [received, onReceived]);

  // 面板重新打开时挑战状态可能为空，但记录里已有成功项：同样刷新成绩/答案。
  const recordsReceived = (records.data?.items ?? []).some(
    (item) => item.state === "received",
  );
  useEffect(() => {
    if (recordsReceived) onReceived();
  }, [recordsReceived, onReceived]);

  const code = overrides.code ?? challenge?.code ?? "";
  const section = overrides.section ?? sectionLabel ?? "";
  const name = overrides.name ?? currentUser?.display_name ?? "";
  const studentNumber = overrides.studentNumber ?? currentUser?.student_number ?? "";

  const template = [
    `${t("submit.email.field.code")}：${code || t("submit.email.codePlaceholder")}`,
    `${t("submit.email.field.assignment")}：${assignment.assignment_id}`,
    `${t("submit.email.field.section")}：${section || t("submit.email.sectionPlaceholder")}`,
    `${t("submit.email.field.name")}：${name || t("submit.email.namePlaceholder")}`,
    `${t("submit.email.field.studentNumber")}：${
      studentNumber || t("submit.email.numberPlaceholder")
    }`,
  ].join("\n");

  const issueError = issue.error;
  const issueErrorText = issueError
    ? isApiError(issueError) && issueError.isForbidden
      ? t("submit.email.forbidden")
      : isApiError(issueError) && issueError.isNotFound
        ? t("submit.email.assignmentMissing")
        : isApiError(issueError) && issueError.status === 409
          ? t("submit.email.assignmentClosed")
          : isApiError(issueError) && issueError.status === 503
            ? t("submit.email.intakeUnavailable")
            : describeApiError(issueError)
    : null;

  return (
    <Panel testId="email-submission-panel">
      <div className="stack">
        <ol className="email-submit-steps" data-testid="email-submit-steps">
          <li>{t("submit.email.step.issue")}</li>
          <li>{t("submit.email.step.save")}</li>
          <li>{t("submit.email.step.send")}</li>
          <li>{t("submit.email.step.wait")}</li>
        </ol>

        <fieldset className="stack" data-testid="email-template-fields">
          <legend>{t("submit.email.templateTitle")}</legend>
          <label className="field">
            <span>{t("submit.email.field.section")}</span>
            <input
              className="input"
              value={section}
              data-testid="email-field-section"
              placeholder={t("submit.email.sectionPlaceholder")}
              onChange={(event) =>
                setOverrides((previous) => ({ ...previous, section: event.target.value }))
              }
            />
          </label>
          <label className="field">
            <span>{t("submit.email.field.name")}</span>
            <input
              className="input"
              value={name}
              data-testid="email-field-name"
              placeholder={t("submit.email.namePlaceholder")}
              onChange={(event) =>
                setOverrides((previous) => ({ ...previous, name: event.target.value }))
              }
            />
          </label>
          <label className="field">
            <span>{t("submit.email.field.studentNumber")}</span>
            <input
              className="input"
              value={studentNumber}
              data-testid="email-field-student-number"
              placeholder={t("submit.email.numberPlaceholder")}
              onChange={(event) =>
                setOverrides((previous) => ({
                  ...previous,
                  studentNumber: event.target.value,
                }))
              }
            />
          </label>
          <p className="muted">{t("submit.email.identityHint")}</p>
        </fieldset>

        <label className="field">
          <span>{t("submit.email.bodyLabel")}</span>
          <textarea
            className="input"
            readOnly
            rows={7}
            value={template}
            data-testid="email-template-body"
            aria-label={t("submit.email.bodyLabel")}
          />
        </label>
        <div className="inline">
          <button
            type="button"
            className="button button--ghost"
            data-testid="email-copy-template"
            onClick={() => {
              void copyText(template).then(setCopyState);
            }}
          >
            {t("submit.email.copyTemplate")}
          </button>
          {copyState === "ok" ? (
            <span className="muted" data-testid="email-copied">
              {t("submit.email.copied")}
            </span>
          ) : null}
          {copyState === "manual" ? (
            <span className="muted" data-testid="email-copy-manual">
              {t("submit.email.copyManual")}
            </span>
          ) : null}
        </div>

        <Panel title={t("submit.email.mappingTitle")} testId="email-attachment-mapping">
          <div className="table__wrap">
            <table className="table" aria-label={t("submit.email.mappingTitle")}>
              <thead>
                <tr>
                  <th scope="col">{t("submit.email.colOrdinal")}</th>
                  <th scope="col">{t("submit.email.colQuestion")}</th>
                  <th scope="col">{t("submit.email.colFileName")}</th>
                </tr>
              </thead>
              <tbody>
                {questionRows.map((row) => (
                  <tr key={row.questionVersionId}>
                    <td>{row.ordinal}</td>
                    <td>
                      {row.question?.title ?? (row.isPending ? "—" : t("questions.titleUnavailable"))}
                    </td>
                    <td className="mono-id">{row.ordinal}.txt</td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
          <p className="muted">{t("submit.email.mappingHint")}</p>
        </Panel>

        <div className="inline">
          <button
            type="button"
            className="button"
            data-testid="email-issue-code"
            disabled={
              issue.isPending ||
              courseId === null ||
              sectionId === null ||
              challenge !== null
            }
            onClick={() => issue.mutate()}
          >
            {challenge
              ? t("submit.email.issued")
              : issue.isPending
                ? t("submit.email.issuing")
                : t("submit.email.issue")}
          </button>
          {challenge ? (
            <button
              type="button"
              className="button button--ghost"
              data-testid="email-reissue-code"
              disabled={issue.isPending}
              onClick={() => issue.mutate()}
            >
              {t("submit.email.reissue")}
            </button>
          ) : null}
        </div>
        {issueErrorText ? (
          <Banner tone="error" testId="email-issue-error">
            {issueErrorText}
          </Banner>
        ) : null}

        {challenge ? (
          <div className="stack" data-testid="email-challenge">
            {status.data ? (
              <SubmitHint status={status.data} />
            ) : (
              <p className="muted">
                <InlineLoader />
              </p>
            )}
            <p>
              {t("submit.email.code")}：
              <strong className="mono-id" data-testid="email-code">
                {challenge.code}
              </strong>
            </p>
            <p className="muted">
              {t("submit.email.expires")}：
              {formatDateTime(challenge.expires_at, t("common.none"))}
            </p>
            {challenge.mailbox_address ? (
              <p data-testid="email-mailbox-address">
                {t("submit.email.toAddress")}：
                <strong className="mono-id">{challenge.mailbox_address}</strong>{" "}
                <button
                  type="button"
                  className="button button--ghost"
                  data-testid="email-copy-address"
                  onClick={() => {
                    void copyText(challenge.mailbox_address as string).then(setAddressCopy);
                  }}
                >
                  {t("submit.email.copyAddress")}
                </button>
                {addressCopy === "ok" ? (
                  <span className="muted" data-testid="email-address-copied">
                    {t("submit.email.addressCopied")}
                  </span>
                ) : null}
                {addressCopy === "manual" ? (
                  <span className="muted" data-testid="email-address-copy-manual">
                    {t("submit.email.copyManual")}
                  </span>
                ) : null}
              </p>
            ) : (
              <p className="muted" data-testid="email-mailbox-address-missing">
                {t("submit.email.addressMissing")}
              </p>
            )}
            {status.isError ? (
              <Banner tone="error" testId="email-status-error">
                {describeApiError(status.error)}
              </Banner>
            ) : null}
            {(status.data?.submissions ?? []).length > 0 ? (
              <div className="stack" data-testid="email-received-submissions">
                <h3>{t("submit.email.receivedTitle")}</h3>
                <ul>
                  {(status.data?.submissions ?? []).map((item) => {
                    const row = questionRows.find(
                      (candidate) =>
                        candidate.questionVersionId === item.question_version_id,
                    );
                    return (
                      <EmailSubmissionResult
                        key={item.submission_id}
                        submissionId={item.submission_id}
                        title={t("submit.email.resultLine", {
                          ordinal: row?.ordinal ?? "?",
                          title:
                            row?.question?.title ??
                            row?.questionVersionId ??
                            item.question_version_id,
                        })}
                      />
                    );
                  })}
                </ul>
              </div>
            ) : null}
          </div>
        ) : null}

        <Panel title={t("submit.email.recordsTitle")} testId="email-submission-records">
          {records.isPending ? (
            <p className="muted">
              <InlineLoader />
            </p>
          ) : records.isError ? (
            <Banner tone="error" testId="email-records-error">
              {describeApiError(records.error)}
            </Banner>
          ) : (records.data?.items ?? []).length === 0 ? (
            <p className="muted" data-testid="email-records-empty">
              {t("submit.email.recordsEmpty")}
            </p>
          ) : (
            <div className="stack">
              {(records.data?.items ?? []).map((record) => (
                <EmailSubmissionRecordRow
                  key={record.challenge_id}
                  record={record}
                  questionRows={questionRows}
                />
              ))}
            </div>
          )}
        </Panel>
      </div>
    </Panel>
  );
}
