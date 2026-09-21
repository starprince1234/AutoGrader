import { useMemo } from "react";
import { Link } from "@tanstack/react-router";
import { useQuery } from "@tanstack/react-query";
import { t } from "../../i18n";
import { MAX_AUTO_PAGES } from "../../api/pagination";
import type { GradeRow, QuestionVersionPublic } from "../../api/types";
import { apiRequest } from "../../api/client";
import { queryKeys } from "../../app/queryClient";
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
import { SubmissionMatrix } from "../grades/SubmissionMatrix";
import { gradesAvailability, useAllGrades, type GradesAvailability } from "../grades/useGrades";
import { useAllAssignments } from "./useAssignments";

/**
 * 作业详情：作业本身的元信息 + 它的题目版本（每个版本挂到题干页与作答页）。
 *
 * 契约**没有**"按 id 取作业"的端点，因此这里只能把列表取完后按 id 找；找不到就如实说
 * 找不到（`assignments.detailMissing`），不猜、也不用别的字段凑一个作业出来。
 *
 * 版本信息来自 `GET /question-versions/{id}`（公开 DTO）。作业的题目版本通常只有 1～3 个，
 * 因此**每个版本行自己取数**：这也是这里不用通用 `Table` 原语的原因——行的数据来自各自的
 * 查询，需要一个能持有 hook 的行组件。
 */

function myStatus(grade: GradeRow | null): { label: string; tone: "ok" | "warn" | "bad" | "idle" } {
  if (!grade) return { label: t("assignments.my.notSubmitted"), tone: "idle" };
  if (grade.state === "completed") return { label: t("assignments.my.submitted"), tone: "ok" };
  if (grade.state === "failed") return { label: t("grades.state.failed"), tone: "bad" };
  if (grade.state === "rejected") return { label: t("grades.state.rejected"), tone: "warn" };
  return { label: t(`grades.state.${grade.state}`), tone: "warn" };
}

/** 本人成绩行的可用性：只有 `ready` 才允许渲染"我交了没有"这类判定。 */
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
    // 成绩行被截断时"未提交"可能是分页造成的假象，因此这里不下结论。
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
  /** 学生视角的本人提交状态（教师不显示它：那不是"他"的成绩）。 */
  myGrade: GradeRow | null;
  myProgress: MyProgress;
}) {
  const query = useQuery({
    queryKey: queryKeys.questionVersion(versionId),
    queryFn: async (): Promise<QuestionVersionPublic> =>
      apiRequest<QuestionVersionPublic>(
        `/api/v1/question-versions/${encodeURIComponent(versionId)}`,
      ),
  });

  if (query.isPending) {
    return (
      <tr>
        <td colSpan={4}>
          <Spinner />
        </td>
      </tr>
    );
  }
  if (query.isError) {
    return (
      <tr>
        <td colSpan={4}>
          <ErrorState error={query.error} />
        </td>
      </tr>
    );
  }

  const version = query.data;
  return (
    <tr>
      <td>
        <Link
          to="/questions/$questionId"
          params={{ questionId: version.question_id }}
          search={{ version: versionId }}
        >
          {t("questions.version", { version: version.version })}
        </Link>
      </td>
      <td>
        {canAnswer ? (
          // 学生看的是"我交了没有"，不是版本的发布状态（学生只能看到已发布版本）。
          <MyStatusCell progress={myProgress} grade={myGrade} />
        ) : (
          <StatusPill tone={version.status === "published" ? "ok" : "warn"}>
            {t(`questions.status.${version.status}`)}
          </StatusPill>
        )}
      </td>
      <td>{t(`questions.language.${version.language}`)}</td>
      <td>
        {/* 判定未就绪时不渲染动作：动作标签（"作答"/"重新作答"）本身就是"我交了没有"的结论。 */}
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

export function AssignmentDetailPage({ assignmentId }: { assignmentId: string }) {
  const session = useSession();
  const scope = session.scope[0] ?? null;
  const scopeFilter = useMemo(
    () => ({ courseId: scope?.course_id ?? null, sectionId: scope?.section_id ?? null }),
    [scope?.course_id, scope?.section_id],
  );
  const assignments = useAllAssignments(scopeFilter);
  const assignment =
    assignments.assignments.find((item) => item.assignment_id === assignmentId) ?? null;
  const versions = useMemo(
    () => assignment?.question_version_ids ?? [],
    [assignment?.question_version_ids],
  );
  // 学生视角的"我交了没有"来自**本人成绩行**（`listGrades` 对学生只返回本人记录）。
  const mine = useAllGrades(scope?.course_id ?? null, scope?.section_id ?? null);
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

  const cohortFilter = useMemo(
    () => ({
      courseId: assignment?.course_id ?? null,
      sectionId: assignment?.section_id ?? null,
      questionVersionIds: versions.length > 0 ? versions : null,
    }),
    [assignment?.course_id, assignment?.section_id, versions],
  );

  // 本人成绩行是"我交了没有"的**唯一**来源：没取完 / 被截断时不得下结论。
  const myProgress: MyProgress = gradesAvailability(mine);

  return (
    <div className="stack">
      <PageTitle>{t("assignments.detailTitle")}</PageTitle>

      <Panel title={t("assignments.detailTitle")}>
        {assignments.isPending ? (
          <Spinner label={t("assignments.loadingAll")} />
        ) : assignments.isError ? (
          <ErrorState error={assignments.error} />
        ) : assignments.truncated ? (
          // 列表被截断 ⇒ "列表里没有这个 id"这个结论也不成立，因此既不下结论也不转圈。
          <Banner tone="warn" testId="assignment-detail-truncated">
            {t("common.paginationTruncated", { pages: MAX_AUTO_PAGES })}{" "}
            {t("assignments.truncated")}
          </Banner>
        ) : !assignments.complete ? (
          <>
            <Spinner label={t("assignments.loadingAll")} />
            <Banner tone="info" testId="assignment-detail-loading">
              {t("assignments.notComplete")}
            </Banner>
          </>
        ) : assignment === null ? (
          // 列表里没有这个 id：契约没有单取端点，因此只能如实说找不到。
          <EmptyState>{t("assignments.detailMissing")}</EmptyState>
        ) : (
          <div className="stack">
            <h2 className="panel__title" data-testid="assignment-title">
              {assignment.title}
            </h2>
            <div className="inline">
              <StatusPill tone={assignment.status === "open" ? "ok" : "idle"}>
                {t(`assignments.status.${assignment.status}`)}
              </StatusPill>
              <span className="muted">
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
        )}
      </Panel>

      {assignment ? (
        <Panel title={t("assignments.versions", { count: versions.length })}>
          {mine.truncated ? (
            <Banner tone="warn" testId="assignment-detail-grades-truncated">
              {t("common.paginationTruncated", { pages: MAX_AUTO_PAGES })}{" "}
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
                    <th scope="col">{t("questions.versionPicker")}</th>
                    <th scope="col">{t("grades.state")}</th>
                    <th scope="col">{t("questions.language")}</th>
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
      ) : null}

      {assignment && canViewCohortGrades(session) ? (
        <SubmissionMatrix filter={cohortFilter} />
      ) : null}
    </div>
  );
}
