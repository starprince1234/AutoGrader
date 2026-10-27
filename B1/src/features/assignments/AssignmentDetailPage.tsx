import { Link } from "@tanstack/react-router";
import { useMemo } from "react";
import { t } from "../../i18n";
import type { AssignmentPublic, GradeRow } from "../../api/types";
import { isApiError } from "../../api/problem";
import { useSession } from "../../auth/sessionContext";
import { canSubmitAnswer, canViewCohortGrades } from "../../auth/visibility";
import { formatDateTime } from "../../ui/format";
import {
  Banner,
  EmptyState,
  ErrorState,
  PageTitle,
  Panel,
  Spinner,
  StatusPill,
} from "../../ui/components";
import { useQuestion, useQuestionVersion } from "../questions/useQuestions";
import { SubmissionMatrix } from "../grades/SubmissionMatrix";
import { gradesAvailability, useAllGrades, type GradesAvailability } from "../grades/useGrades";
import { useAssignment } from "./useAssignments";

/**
 * 作业详情（P05）：作业元信息 + 它的题目版本。
 *
 * 读路径是 `GET /api/v1/assignments/{assignment_id}`（契约 0.19.0）。此前是“取完所有作业列表
 * 再按 id 本地查找”，一旦列表分页，深链就会显示“找不到”，那是错误结论而不是真实答案。
 *
 * 每个版本行显示**题目标题 + 版本号 + 语言 + 本人状态**：标题来自 `GET /questions/{id}`
 * （公开 DTO），版本来自 `GET /question-versions/{id}`；不读参考答案与隐藏用例。
 */

function myStatus(grade: GradeRow | null): { label: string; tone: "ok" | "warn" | "bad" | "idle" } {
  if (!grade) return { label: t("assignments.my.notSubmitted"), tone: "idle" };
  if (grade.state === "completed") return { label: t("assignments.my.submitted"), tone: "ok" };
  if (grade.state === "failed") return { label: t("grades.state.failed"), tone: "bad" };
  if (grade.state === "rejected") return { label: t("grades.state.rejected"), tone: "warn" };
  return { label: t(`grades.state.${grade.state}`), tone: "warn" };
}

type MyProgress = GradesAvailability;

function MyStatusCell({ progress, grade }: { progress: MyProgress; grade: GradeRow | null }) {
  if (progress === "loading") {
    return (
      <span className="muted" data-testid="my-version-progress-loading">
        {t("common.loading")}
      </span>
    );
  }
  if (progress === "truncated") {
    return (
      <span className="muted" data-testid="my-version-progress-truncated">
        {t("assignments.gradesTruncatedShort")}
      </span>
    );
  }
  const status = myStatus(grade);
  return <StatusPill tone={status.tone}>{status.label}</StatusPill>;
}

function VersionRow({
  versionId,
  canAnswer,
  myGrade,
  myProgress,
}: {
  versionId: string;
  canAnswer: boolean;
  myGrade: GradeRow | null;
  myProgress: MyProgress;
}) {
  const versionQuery = useQuestionVersion(versionId);
  // 标题只存在于题目（版本 DTO 不含 title），因此按 question_id 再取一次公开题目。
  const questionId = versionQuery.data?.question_id ?? null;
  const questionQuery = useQuestion(questionId ?? "");

  if (versionQuery.isPending) {
    return (
      <tr>
        <td colSpan={5}>
          <Spinner />
        </td>
      </tr>
    );
  }
  if (versionQuery.isError) {
    // 一个版本读取失败只占它自己这一行，作业头部与其他题目仍然可用。
    return (
      <tr>
        <td colSpan={5}>
          <ErrorState error={versionQuery.error} testId={`version-error-${versionId}`} />
        </td>
      </tr>
    );
  }

  const version = versionQuery.data;
  const title =
    questionQuery.isPending && questionId !== null
      ? t("common.loading")
      : questionQuery.isError
        ? t("questions.titleUnavailable")
        : (questionQuery.data?.title ?? t("questions.titleUnavailable"));

  return (
    <tr>
      <td>
        <Link
          to="/questions/$questionId"
          params={{ questionId: version.question_id }}
          search={{ version: versionId }}
          data-testid={`question-link-${versionId}`}
        >
          {title}
        </Link>
      </td>
      <td>{t("questions.version", { version: version.version })}</td>
      <td>{t(`questions.language.${version.language}`)}</td>
      <td>
        {canAnswer ? (
          <MyStatusCell progress={myProgress} grade={myGrade} />
        ) : (
          <StatusPill tone={version.status === "published" ? "ok" : "warn"}>
            {t(`questions.status.${version.status}`)}
          </StatusPill>
        )}
      </td>
      <td>
        {!canAnswer ? (
          <span className="muted">{t("questions.submitStudentOnly")}</span>
        ) : myProgress !== "ready" ? (
          <span className="muted">{t("common.none")}</span>
        ) : (
          <Link
            className="button button--ghost"
            to="/submit/$questionVersionId"
            params={{ questionVersionId: versionId }}
          >
            {myGrade ? t("assignments.actionResubmit") : t("assignments.answer")}
          </Link>
        )}
      </td>
    </tr>
  );
}

/** 403 与 404 是不同结论：一个说“你没权限”，一个说“它不存在”，不能都渲染成空列表。 */
function assignmentErrorContent(error: unknown) {
  if (isApiError(error)) {
    if (error.status === 403) {
      return <EmptyState>{t("assignments.detailForbidden")}</EmptyState>;
    }
    if (error.status === 404) {
      return <EmptyState>{t("assignments.detailMissing")}</EmptyState>;
    }
  }
  return <ErrorState error={error} testId="assignment-detail-error" />;
}

function DetailBody({ assignment }: { assignment: AssignmentPublic }) {
  const versions = assignment.question_version_ids ?? [];
  const session = useSession();
  const mine = useAllGrades(assignment.course_id, assignment.section_id);
  const byVersion = useMemo(() => {
    const map = new Map<string, GradeRow>();
    for (const row of mine.rows) {
      const previous = map.get(row.question_version_id);
      if (!previous || (row.submitted_at ?? "") > (previous.submitted_at ?? "")) {
        map.set(row.question_version_id, row);
      }
    }
    return map;
  }, [mine.rows]);
  const myProgress: MyProgress = gradesAvailability(mine);

  return (
    <>
      <Panel title={t("assignments.detailOverview")}>
        <div className="stack">
          <h2 className="panel__title" data-testid="assignment-title">
            {assignment.title}
          </h2>
          <div className="inline">
            <StatusPill tone={assignment.status === "open" ? "ok" : "idle"}>
              {t(`assignments.status.${assignment.status}`)}
            </StatusPill>
            <span className="muted" data-testid="assignment-due">
              {t("assignments.due")}：
              {assignment.due_at
                ? formatDateTime(assignment.due_at, t("common.none"))
                : t("assignments.noDue")}
            </span>
            <span className="muted">
              {t("assignments.versionCount")}：{versions.length}
            </span>
          </div>
        </div>
      </Panel>

      <Panel title={t("assignments.versions", { count: versions.length })}>
        {mine.truncated ? (
          <Banner tone="warn" testId="assignment-detail-grades-truncated">
            {t("assignments.gradesTruncated")}
          </Banner>
        ) : null}
        {versions.length === 0 ? (
          <EmptyState>{t("assignments.noVersions")}</EmptyState>
        ) : (
          <div className="table__wrap">
            <table
              className="table"
              aria-label={t("assignments.versions", { count: versions.length })}
              data-testid="assignment-versions-table"
            >
              <thead>
                <tr>
                  <th scope="col">{t("assignments.col.question")}</th>
                  <th scope="col">{t("questions.versionPicker")}</th>
                  <th scope="col">{t("questions.language")}</th>
                  <th scope="col">{t("grades.state")}</th>
                  <th scope="col">{t("assignments.col.action")}</th>
                </tr>
              </thead>
              <tbody>
                {versions.map((versionId) => (
                  <VersionRow
                    key={versionId}
                    versionId={versionId}
                    canAnswer={canSubmitAnswer(session)}
                    myGrade={byVersion.get(versionId) ?? null}
                    myProgress={myProgress}
                  />
                ))}
              </tbody>
            </table>
          </div>
        )}
      </Panel>

      {canViewCohortGrades(session) ? (
        <SubmissionMatrix
          filter={{
            courseId: assignment.course_id,
            sectionId: assignment.section_id,
            questionVersionIds: versions.length > 0 ? versions : null,
          }}
        />
      ) : null}
    </>
  );
}

export function AssignmentDetailPage({ assignmentId }: { assignmentId: string }) {
  const query = useAssignment(assignmentId);

  return (
    <div className="stack">
      <nav className="muted" aria-label={t("assignments.breadcrumb")}>
        <Link to="/assignments" data-testid="assignment-back-link">
          {t("nav.assignments")}
        </Link>
        <span aria-hidden="true"> / </span>
        <span data-testid="assignment-breadcrumb-current">
          {query.data?.title ?? t("assignments.detailTitle")}
        </span>
      </nav>

      <div className="inline">
        <PageTitle>{query.data?.title ?? t("assignments.detailTitle")}</PageTitle>
        <Link to="/assignments" className="button button--ghost">
          {t("assignments.backToList")}
        </Link>
      </div>

      {query.isPending ? (
        <Spinner label={t("assignments.detailLoading")} />
      ) : query.isError ? (
        assignmentErrorContent(query.error)
      ) : (
        <DetailBody assignment={query.data} />
      )}
    </div>
  );
}
