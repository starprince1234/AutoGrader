import { useMemo, useState } from "react";
import { Link } from "@tanstack/react-router";
import { t } from "../../i18n";
import { loadedCourses, useCourses } from "../../api/directory";
import { useSession } from "../../auth/sessionContext";
import { canAuthorQuestions } from "../../auth/visibility";
import {
  Banner,
  EmptyState,
  ErrorState,
  Field,
  LoadMoreActions,
  PageTitle,
  Panel,
  Spinner,
  StatusPill,
  Table,
} from "../../ui/components";
import { shortenId } from "../../ui/format";
import type { QuestionPublic } from "../../api/types";
import { parseTags } from "../authoring/draft";
import { QuestionCoveragePanel } from "./QuestionCoveragePanel";
import { useQuestionList } from "./useQuestions";

/**
 * 题库列表（P06，`listQuestions` + `getQuestionTagCoverage`）。
 *
 * 三条与角色相关的规则，都由服务端再次判定、这里只决定"要不要显示和请求"：
 *
 * 1. **学生不浏览全量题库**：这一页只对教师/助教/管理员开放；学生从作业详情进入 P07 看被布置的
 *    那一个已发布版本。对非出题角色，本页**不请求课程列表、不请求题目列表、也不请求覆盖度**，
 *    而不是先请求再把结果藏起来。
 * 2. **标签覆盖度是教师分析**：面板与请求都只对出题角色存在（覆盖度聚合对备课有用，对学生没有任何
 *    意义，而"学生也能看到"曾经是线上缺陷）。
 * 3. **草稿开关同理**：`published_only` 默认 true，只有出题角色能把它关掉。
 *
 * 表格列顺序是产品定义的一部分：课程、题目、语言、状态、标签、图示数、操作。长 UUID 不直接展示，
 * 课程显示课程代码与名称（拿不到时退回短 id，而不是空白）。
 */

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
  // 标签输入非法时不发请求：契约里 tags 有形态约束，发一个畸形值只会换来一个看起来像
  // "没有符合条件的题目"的空结果。
  const tagsValid = parsedTags.invalid.length === 0;
  const questions = useQuestionList(
    author ? (courseId === "" ? null : courseId) : null,
    {
      // 契约 `published_only` 默认 true。草稿只有出题角色会需要，因此开关也只给他们。
      publishedOnly: !includeDrafts,
      tags: parsedTags.tags,
    },
    // 非出题角色连请求都不发（不是"发了再藏起来"）。
    { enabled: author && tagsValid },
  );
  // 分页结果摊平成一张表：游标只用于"还有没有下一页"，页面本身不暴露游标。
  const rows = useMemo(
    () => (questions.data?.pages ?? []).flatMap((page) => page.items),
    [questions.data],
  );

  const courseLabel = (id: string): string => {
    const match = courseOptions.find((course) => course.course_id === id);
    return match ? `${match.course_code} · ${match.title}` : shortenId(id);
  };

  if (!author) {
    return (
      <div className="stack">
        <PageTitle>{t("nav.questions")}</PageTitle>
        <Panel title={t("questions.staffOnly")} testId="question-list-staff-only">
          <p className="muted">{t("questions.staffOnlyHint")}</p>
        </Panel>
      </div>
    );
  }

  return (
    <div className="stack">
      <PageTitle>{t("nav.questions")}</PageTitle>

      <Panel
        title={t("questions.filter")}
        actions={
          <Link className="button" to="/questions/new" data-testid="question-new">
            {t("questions.newQuestion")}
          </Link>
        }
      >
        <div className="form-grid">
          <Field label={t("import.course")} htmlFor="question-course">
            <select
              id="question-course"
              value={courseId}
              onChange={(event) => setCourseId(event.target.value)}
            >
              <option value="">{t("questions.selectCourse")}</option>
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
              placeholder={t("questions.tagPlaceholder")}
              onChange={(event) => setTagFilter(event.target.value)}
            />
          </Field>
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
        </div>
        <p className="muted" data-testid="question-tag-help">
          {t("questions.tagHelp")}
        </p>
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
        {courses.isError ? <ErrorState error={courses.error} /> : null}
      </Panel>

      {/*
        标签覆盖度（CHG-0020）与列表**共用**同一个课程作用域与同一个「包含草稿」开关：若让它各自
        选一次课程，两个并排的数字就可能来自不同作用域，而那种矛盾看起来像"数据不对"。
        只在出题角色下渲染——组件内部才发请求，因此非出题角色连这次请求都不会发生。
        点一个标签即填进上面的标签筛选（复用既有实现，不另造筛选机制）。
      */}
      {author ? (
        <QuestionCoveragePanel
          courseId={courseId === "" ? null : courseId}
          publishedOnly={!includeDrafts}
          onSelectTag={(tag) => setTagFilter(tag)}
        />
      ) : null}

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
        {!tagsValid ? (
          <Banner tone="warn" testId="question-list-tag-invalid">
            {t("questions.tagFilterInvalid", { tags: parsedTags.invalid.join(", ") })}
          </Banner>
        ) : questions.isPending ? (
          <Spinner />
        ) : questions.isError ? (
          <Banner tone="error" testId="question-list-error">
            {t("questions.listError")}
          </Banner>
        ) : rows.length === 0 ? (
          <EmptyState>
            {parsedTags.tags.length > 0 ? t("questions.noMatches") : t("questions.emptyBank")}
          </EmptyState>
        ) : (
          <Table<QuestionPublic>
            testId="question-table"
            label={t("questions.title")}
            rows={rows}
            getRowKey={(row) => row.question_id}
            columns={[
              {
                key: "course",
                header: t("import.course"),
                render: (row) => (
                  <span data-testid={`course-${row.question_id}`}>{courseLabel(row.course_id)}</span>
                ),
              },
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
                align: "right",
                render: (row) =>
                  (row.stem_assets?.length ?? 0) === 0 ? (
                    <span className="muted">{t("common.none")}</span>
                  ) : (
                    <span data-testid={`assets-${row.question_id}`}>
                      {row.stem_assets?.length}
                    </span>
                  ),
              },
              {
                key: "actions",
                header: t("questions.actions"),
                render: (row) =>
                  row.status === "draft" ? (
                    <Link
                      className="button button--ghost"
                      to="/questions/$questionId/edit"
                      params={{ questionId: row.question_id }}
                      data-testid={`edit-${row.question_id}`}
                    >
                      {t("questions.continueEditing")}
                    </Link>
                  ) : (
                    <Link
                      className="button button--ghost"
                      to="/questions/$questionId/edit"
                      params={{ questionId: row.question_id }}
                      data-testid={`edit-${row.question_id}`}
                    >
                      {t("questions.newVersion")}
                    </Link>
                  ),
              },
            ]}
          />
        )}
      </Panel>
    </div>
  );
}
