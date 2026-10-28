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
import type { QuestionPublic } from "../../api/types";
import { parseTags } from "../authoring/draft";
import { QuestionCoveragePanel } from "./QuestionCoveragePanel";
import { useQuestionList } from "./useQuestions";

/**
 * 题库列表（P06，`listQuestions` + `getQuestionTagCoverage`）。
 *
 * 版面顺序是产品定义的一部分：筛选 → **题目列表** → 知识点覆盖。教师进这一页的第一件事是
 * 看"题库里有哪些题"，所以列表必须在分析面板之前。
 *
 * 三条与角色相关的规则，都由服务端再次判定、这里只决定"要不要显示和请求"：
 *
 * 1. **学生不浏览全量题库**：这一页只对教师/助教/管理员开放；学生从作业详情进入 P07 看被布置的
 *    那一个已发布版本。对非出题角色，本页不请求课程列表、不请求题目列表、也不请求覆盖度。
 * 2. **标签覆盖度是教师分析**：面板与请求都只对出题角色存在。
 * 3. **草稿一并显示**：原来的「包含草稿」开关已移除，列表固定取 `published_only=false`，
 *    否则刚建的草稿会从列表里消失，教师再也进不去它的编辑页。
 *
 * 题干搜索在**已加载的题目**里做：契约 `listQuestions` 没有文本参数（只有 `course_id` /
 * `language` / `published_only` / `tags` / 游标），所以这里不声称"搜遍全库"；继续加载更多页后
 * 搜索范围随之扩大。
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
  const [stemQuery, setStemQuery] = useState("");

  const parsedTags = useMemo(() => parseTags(tagFilter), [tagFilter]);
  // 标签输入非法时不发请求：契约里 tags 有形态约束，发一个畸形值只会换来一个看起来像
  // "没有符合条件的题目"的空结果。
  const tagsValid = parsedTags.invalid.length === 0;
  const questions = useQuestionList(
    author ? (courseId === "" ? null : courseId) : null,
    {
      // 开关已移除：草稿必须一起列出来，否则教师无法再进入自己的草稿。
      publishedOnly: false,
      tags: parsedTags.tags,
    },
    // 非出题角色连请求都不发（不是"发了再藏起来"）。
    { enabled: author && tagsValid },
  );
  // 分页结果摊平成一张表：游标只用于"还有没有下一页"，页面本身不暴露游标。
  const loadedRows = useMemo(
    () => (questions.data?.pages ?? []).flatMap((page) => page.items),
    [questions.data],
  );

  const query = stemQuery.trim().toLowerCase();
  const rows = useMemo(
    () =>
      query === ""
        ? loadedRows
        : loadedRows.filter((row) => row.stem_markdown.toLowerCase().includes(query)),
    [loadedRows, query],
  );

  // 课程显示"代码 · 名称"；查不到就留白，绝不把内部编号贴到界面上。
  const courseLabel = (id: string): string => {
    const match = courseOptions.find((course) => course.course_id === id);
    return match ? `${match.course_code} · ${match.title}` : "—";
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
      <div className="inline">
        <PageTitle>{t("nav.questions")}</PageTitle>
        <Link className="button" to="/questions/new" data-testid="question-new">
          {t("questions.newQuestion")}
        </Link>
      </div>

      <Panel title={t("questions.filter")}>
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
          <Field label={t("questions.searchStem")} htmlFor="question-stem-search">
            <input
              id="question-stem-search"
              value={stemQuery}
              placeholder={t("questions.searchStemPlaceholder")}
              onChange={(event) => setStemQuery(event.target.value)}
            />
          </Field>
        </div>
        {parsedTags.invalid.length > 0 ? (
          <Banner tone="warn" testId="question-tags-invalid">
            {t("questions.tagFilterInvalid", { tags: parsedTags.invalid.join(", ") })}
          </Banner>
        ) : null}
        <LoadMoreActions
          loadedCount={courseOptions.length}
          hasNextPage={courses.hasNextPage}
          isFetchingNextPage={courses.isFetchingNextPage}
          onLoadMore={() => {
            void courses.fetchNextPage();
          }}
          testId="courses"
        />
        {courses.isError ? <ErrorState error={courses.error} /> : null}
      </Panel>

      <Panel
        title={t("questions.countLoaded", { count: rows.length })}
        actions={
          <LoadMoreActions
            loadedCount={loadedRows.length}
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
            {query !== ""
              ? t("questions.noStemMatch")
              : parsedTags.tags.length > 0
                ? t("questions.noMatches")
                : t("questions.emptyBank")}
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

      {/*
        标签覆盖度（CHG-0020）与列表**共用**同一个课程作用域与同一个发布范围：若让它各自
        选一次课程，两个并排的数字就可能来自不同作用域，而那种矛盾看起来像"数据不对"。
        只在出题角色下渲染——组件内部才发请求，因此非出题角色连这次请求都不会发生。
        点一个标签即填进上面的标签筛选（复用既有实现，不另造筛选机制）。
      */}
      <QuestionCoveragePanel
        courseId={courseId === "" ? null : courseId}
        publishedOnly={false}
        onSelectTag={(tag) => setTagFilter(tag)}
      />
    </div>
  );
}
