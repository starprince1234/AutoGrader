import { useEffect, useState } from "react";
import { useMutation, useQuery } from "@tanstack/react-query";
import { Link } from "@tanstack/react-router";
import { apiRequest } from "../../api/client";
import { describeApiError, isApiError } from "../../api/problem";
import type {
  AssignmentPublic,
  CurrentUser,
  EmailVerificationChallenge,
  EmailVerificationChallengeStatus,
  EmailVerificationReasonCode,
} from "../../api/types";
import { queryKeys } from "../../app/queryClient";
import { t } from "../../i18n";
import { Banner, InlineLoader, Panel } from "../../ui/components";
import { formatDateTime } from "../../ui/format";
import { useSubmissionView } from "./useSubmission";
import type { QuestionVersionRow } from "../questions/useQuestions";

/**
 * 邮件提交面板（CHG-0032 §7.2）。
 *
 * 学生看到的必须是**可复制、可核对的完整格式**：
 * 1. 正文五行：核验码 / 作业编号 / 课序 / 姓名 / 学号。核验码在申请后填入，作业编号来自作业，
 *    姓名与学号来自 `GET /api/v1/me`（B2 会拿它们与身份库逐字比较，手输错一个字整封邮件就会
 *    被判为 `email_body_mismatch`）；课序取本人所在课序的显示名。
 * 2. 附件映射：第 N 题 → `N.txt`（1 基题号，作业顺序）。
 * 3. 状态：pending / received / rejected（逐原因码文案）/ expired / intake_unavailable。
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

const REASON_KEYS = {
  subject_mismatch: "submit.email.reason.subject_mismatch",
  attachment_missing: "submit.email.reason.attachment_missing",
  email_sender_mismatch: "submit.email.reason.email_sender_mismatch",
  email_verification_rejected: "submit.email.reason.email_verification_rejected",
  email_verification_expired: "submit.email.reason.email_verification_expired",
  email_body_missing_field: "submit.email.reason.email_body_missing_field",
  email_body_mismatch: "submit.email.reason.email_body_mismatch",
  attachment_mapping_invalid: "submit.email.reason.attachment_mapping_invalid",
  intake_unavailable: "submit.email.reason.intake_unavailable",
} as const satisfies Record<EmailVerificationReasonCode, string>;

const STATE_KEYS = {
  pending: "submit.email.state.pending",
  received: "submit.email.state.received",
  rejected: "submit.email.state.rejected",
  expired: "submit.email.state.expired",
} as const;

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
      setOverrides((previous) => ({ ...previous, code: created.code }));
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
        <p style={{ whiteSpace: "pre-line" }}>{t("submit.email.instructions")}</p>

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

        <p className="muted">{t("submit.email.senderHint")}</p>

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
            {status.data ? (
              <p data-testid="email-challenge-state">
                <strong>{t(STATE_KEYS[status.data.state])}</strong>
                {status.data.reason_code ? (
                  <span data-testid="email-challenge-reason">
                    {" · "}
                    {t(REASON_KEYS[status.data.reason_code])}
                  </span>
                ) : null}
              </p>
            ) : (
              <p className="muted">
                <InlineLoader />
              </p>
            )}
            {status.isError ? (
              <Banner tone="error" testId="email-status-error">
                {describeApiError(status.error)}
              </Banner>
            ) : null}
            {received ? (
              <p className="muted" data-testid="email-received-hint">
                {t("submit.email.receivedHint")}
              </p>
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
      </div>
    </Panel>
  );
}
