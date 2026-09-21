import { useMemo, useState } from "react";
import { Link } from "@tanstack/react-router";
import { t } from "../../i18n";
import { loadedCourses, useCourses } from "../../api/directory";
import { useSession } from "../../auth/sessionContext";
import { canAuthorQuestions } from "../../auth/visibility";
import {
  Banner,
  ErrorState,
  Field,
  LoadMoreActions,
  PageTitle,
  Panel,
  Spinner,
  StatusPill,
  Table,
} from "../../ui/components";
import type { QuestionPublic } from "../../api/types";
import { parseTags } from "../authoring/draft";
import { QuestionCoveragePanel } from "./QuestionCoveragePanel";
import { useQuestionList } from "./useQuestions";

function statusTone(status: QuestionPublic["status"]): "ok" | "warn" | "idle" {
  if (status === "published") return "ok";
  if (status === "draft") return "warn";
  return "idle";
}

export function QuestionListPage() {
  const session = useSession();
  const author = canAuthorQuestions(session);
  const courses = useCourses();
  const courseOptions = loadedCourses(courses.data?.pages);
  const [courseId, setCourseId] = useState("");
  const [tagFilter, setTagFilter] = useState("");
  const [includeDrafts, setIncludeDrafts] = useState(false);

  const parsedTags = useMemo(() => parseTags(tagFilter), [tagFilter]);
  const questions = useQuestionList(courseId === "" ? null : courseId, {
    // 契约 `published_only` 默认 true。草稿只有出题角色会需要，因此开关也只给他们。
    publishedOnly: !includeDrafts,
    tags: parsedTags.tags,
  });
  // 分页结果摊平成一张表：游标只用于"还有没有下一页"，页面本身不暴露游标。
  const rows = useMemo(
    () => (questions.data?.pages ?? []).flatMap((page) => page.items),
    [questions.data],
  );

  return (
    <div className="stack">
      <PageTitle>{t("nav.questions")}</PageTitle>

      <Panel
        title={t("questions.filter")}
        actions={
          author ? (
            <Link className="button" to="/questions/new">
              {t("questions.newQuestion")}
            </Link>
          ) : null
        }
      >
        <div className="form-grid">
          <Field label={t("import.course")} htmlFor="question-course">
            <select
              id="question-course"
              value={courseId}
              onChange={(event) => setCourseId(event.target.value)}
            >
              <option value="">{t("questions.allCourses")}</option>
              {courseOptions.map((course) => (
                <option key={course.course_id} value={course.course_id}>
                  {course.course_code} · {course.title}
                </option>
              ))}
            </select>
          </Field>
          <Field label={t("questions.tagFilter")} htmlFor="question-tags">
            <input
              id="question-tags"
              value={tagFilter}
              onChange={(event) => setTagFilter(event.target.value)}
            />
          </Field>
          {author ? (
            <div className="field">
              <label className="inline" htmlFor="question-include-drafts">
                <input
                  id="question-include-drafts"
                  type="checkbox"
                  checked={includeDrafts}
                  onChange={(event) => setIncludeDrafts(event.target.checked)}
                />
                {t("questions.includeDrafts")}
              </label>
              <span className="muted">{t("questions.includeDraftsHint")}</span>
            </div>
          ) : null}
        </div>
        {parsedTags.invalid.length > 0 ? (
          <Banner tone="warn" testId="question-tags-invalid">
            {t("questions.tagFilterInvalid", { tags: parsedTags.invalid.join(", ") })}
          </Banner>
        ) : null}
        <div className="inline">
          <span className="muted" data-testid="question-courses-count">
            {t("common.coursesLoaded", { count: courseOptions.length })}
          </span>
          <LoadMoreActions
            loadedCount={courseOptions.length}
            hasNextPage={courses.hasNextPage}
            isFetchingNextPage={courses.isFetchingNextPage}
            onLoadMore={() => {
              void courses.fetchNextPage();
            }}
            testId="courses"
          />
        </div>
        <Banner tone="info" testId="public-dto-guard">
          {t("questions.publicDtoGuard")}
        </Banner>
        {courses.isError ? <ErrorState error={courses.error} /> : null}
      </Panel>

      {/*
        标签覆盖度（CHG-0020）放在筛选面板与题目表之间。
        它与列表**共用**同一个课程作用域与同一个「包含草稿」开关：若让它各自选一次课程，
        两个并排的数字就可能来自不同作用域，而那种矛盾看起来像"数据不对"。
        点一个标签即填进上面的标签筛选（复用既有实现，不另造筛选机制）。
      */}
      <QuestionCoveragePanel
        courseId={courseId === "" ? null : courseId}
        publishedOnly={!includeDrafts}
        onSelectTag={(tag) => setTagFilter(tag)}
      />

      <Panel
        title={t("questions.countLoaded", { count: rows.length })}
        actions={
          <LoadMoreActions
            loadedCount={rows.length}
            hasNextPage={questions.hasNextPage}
            isFetchingNextPage={questions.isFetchingNextPage}
            onLoadMore={() => {
              void questions.fetchNextPage();
            }}
            testId="questions"
          />
        }
      >
        {questions.isPending ? (
          <Spinner />
        ) : questions.isError ? (
          <ErrorState error={questions.error} />
        ) : (
          <Table<QuestionPublic>
            testId="question-table"
            label={t("questions.title")}
            rows={rows}
            getRowKey={(row) => row.question_id}
            columns={[
              {
                key: "title",
                header: t("questions.title"),
                render: (row) => (
                  <Link
                    to="/questions/$questionId"
                    params={{ questionId: row.question_id }}
                    search={{ version: undefined }}
                  >
                    {row.title}
                  </Link>
                ),
              },
              {
                key: "language",
                header: t("questions.language"),
                render: (row) => t(`questions.language.${row.language}`),
              },
              {
                key: "status",
                header: t("grades.state"),
                render: (row) => (
                  <StatusPill tone={statusTone(row.status)}>
                    {t(`questions.status.${row.status}`)}
                  </StatusPill>
                ),
              },
              {
                // 标签在 CHG-0008 之前是"读得到、写不进"的字段；写路径补上之后，
                // 列表必须把它显示出来，否则教师标了标签却看不到标了什么。
                key: "tags",
                header: t("questions.tags"),
                render: (row) =>
                  (row.tags?.length ?? 0) === 0 ? (
                    <span className="muted">{t("questions.tagsNone")}</span>
                  ) : (
                    <span className="inline" data-testid={`tags-${row.question_id}`}>
                      {row.tags?.map((tag) => (
                        <span className="pill pill--idle" key={tag}>
                          {tag}
                        </span>
                      ))}
                    </span>
                  ),
              },
              {
                key: "assets",
                header: t("questions.assets"),
                render: (row) =>
                  (row.stem_assets?.length ?? 0) === 0 ? (
                    t("common.none")
                  ) : (
                    <span className="muted" data-testid="asset-gap">
                      {row.stem_assets?.length}
                    </span>
                  ),
              },
              ...(author
                ? [
                    {
                      key: "edit",
                      header: t("authoring.titleEdit"),
                      render: (row: QuestionPublic) => (
                        <Link
                          className="button button--ghost"
                          to="/questions/$questionId/edit"
                          params={{ questionId: row.question_id }}
                          data-testid={`edit-${row.question_id}`}
                        >
                          {t("questions.editDraft")}
                        </Link>
                      ),
                    },
                  ]
                : []),
            ]}
          />
        )}
      </Panel>
    </div>
  );
}
