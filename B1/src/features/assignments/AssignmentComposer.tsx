import { useEffect, useMemo, useState } from "react";
import { useNavigate } from "@tanstack/react-router";
import { t } from "../../i18n";
import type { CreateAssignmentRequest, QuestionVersionSummary } from "../../api/types";
import { describeApiError, isApiError } from "../../api/problem";
import {
  courseLabel,
  loadedCourses,
  useCourses,
  useSections,
} from "../../api/directory";
import { useQuestionList } from "../questions/useQuestions";
import { Banner, Field, InlineLoader, Modal } from "../../ui/components";
import { shortenId } from "../../ui/format";
import { Select } from "../../ui/Select";
import { useCreateAssignment } from "./useAssignments";
import { useSectionsRoster } from "./useAssignmentRoster";

/**
 * 新建作业（CHG-0032）。
 *
 * 目标模型：作业属于**一门课程**，必须选 1..N 个课序（`section_ids`）；可以在这些课序里
 * 再勾选指定学生收窄受众（`student_ids`，空 = 整个课序）。
 *
 * 两条容易做错的地方：
 * 1. **不能按同名课程跨 course 合并**：课程选择器里每个 course 是独立选项（同名时用
 *    `course_id` 前缀区分），课序列表只来自**当前选中的 course_id**。旧实现按课程名把清单
 *    合并，会把另一门同名课程的班级混进来，最终由服务端 422 拒绝。
 * 2. **指定学生只能来自所选课序的有效在册学生**：名单按所选课序并行拉取；未绑定账号的
 *    名单行不能被单独指定（服务端也只接受有效在册且已绑定账号的学生）。
 *
 * 发布分两步：先预览（课程/课序/学生/名称/截止时间/题目数），再二次确认才 `POST`。
 */

type PublishedVersion = QuestionVersionSummary & { questionTitle: string };

function localInputToIso(value: string): string | undefined {
  if (!value) return undefined;
  const parsed = new Date(value);
  if (Number.isNaN(parsed.getTime())) return undefined;
  // `datetime-local` 是本地时间；契约要 UTC RFC 3339，因此先转 UTC 再序列化。
  return parsed.toISOString();
}

export function AssignmentComposer({
  onClose,
  onCreated,
}: {
  onClose: () => void;
  onCreated: (assignmentId: string) => void;
}) {
  const navigate = useNavigate();
  const courses = useCourses();
  const courseOptions = useMemo(() => loadedCourses(courses.data?.pages), [courses.data]);
  const [courseId, setCourseId] = useState("");
  const [sectionIds, setSectionIds] = useState<string[]>([]);
  const [studentIds, setStudentIds] = useState<string[]>([]);
  const [title, setTitle] = useState("");
  const [dueAt, setDueAt] = useState("");
  const [selected, setSelected] = useState<string[]>([]);
  const [error, setError] = useState<string | null>(null);
  const [previewing, setPreviewing] = useState(false);

  const selectedCourseId = courseId || (courseOptions[0]?.course_id ?? "");
  const selectedCourse = courseOptions.find(
    (course) => course.course_id === selectedCourseId,
  );
  // 课程按 `course_id` 独立列出；同名课程用短 id 区分，绝不合并成一项。
  const duplicateLabels = useMemo(() => {
    const seen = new Map<string, number>();
    for (const course of courseOptions) {
      const label = courseLabel(course);
      seen.set(label, (seen.get(label) ?? 0) + 1);
    }
    return new Set(
      [...seen.entries()].filter(([, count]) => count > 1).map(([label]) => label),
    );
  }, [courseOptions]);
  const courseOptionLabel = (course: { course_id: string; course_code?: string | null; title: string }) => {
    const label = courseLabel(course);
    return duplicateLabels.has(label) ? `${label} · ${shortenId(course.course_id)}` : label;
  };

  const sectionsQuery = useSections(selectedCourseId || null);
  const sectionOptions = sectionsQuery.data ?? [];
  const sectionLabelOf = (id: string) =>
    sectionOptions.find((option) => option.sectionId === id)?.label ?? shortenId(id);

  const roster = useSectionsRoster(sectionIds);

  // 名单变化后丢掉已经不在名单里的选择，避免提交一个服务端必然 422 的 id。
  useEffect(() => {
    const available = new Set(
      roster.students
        .map((student) => student.student_id)
        .filter((studentId): studentId is string => studentId !== null),
    );
    setStudentIds((previous) => {
      const next = previous.filter((studentId) => available.has(studentId));
      return next.length === previous.length ? previous : next;
    });
  }, [roster.students]);

  const questions = useQuestionList(
    selectedCourseId || null,
    { publishedOnly: true, tags: [] },
    { enabled: Boolean(selectedCourseId) },
  );

  const publishedVersions = useMemo<PublishedVersion[]>(() => {
    const rows: PublishedVersion[] = [];
    for (const page of questions.data?.pages ?? []) {
      for (const question of page.items) {
        for (const version of question.versions ?? []) {
          if (version.status === "published") {
            rows.push({ ...version, questionTitle: question.title });
          }
        }
      }
    }
    return rows;
  }, [questions.data]);

  const create = useCreateAssignment();

  const submit = () => {
    setError(null);
    if (title.trim() === "") {
      setError(t("assignments.create.errorTitle"));
      return;
    }
    if (sectionIds.length === 0) {
      setError(t("assignments.create.errorSections"));
      return;
    }
    if (selected.length === 0) {
      setError(t("assignments.create.errorVersions"));
      return;
    }
    setPreviewing(true);
  };

  const publish = () => {
    const body: CreateAssignmentRequest = {
      course_id: selectedCourseId,
      section_ids: sectionIds,
      title: title.trim(),
      question_version_ids: selected,
    };
    if (studentIds.length > 0) body.student_ids = studentIds;
    const isoDue = localInputToIso(dueAt);
    if (isoDue) body.due_at = isoDue;
    create.mutate(body, {
      onSuccess: (created) => {
        setPreviewing(false);
        onCreated(created.assignment_id);
        onClose();
        // 详情页是新建结果的唯一可信展示（服务端已存储），因此直接进入它。
        navigate({
          to: "/assignments/$assignmentId",
          params: { assignmentId: created.assignment_id },
          search: {},
        });
      },
      onError: (failure) => {
        setPreviewing(false);
        if (isApiError(failure) && failure.status === 409) {
          setError(t("assignments.create.conflict"));
          return;
        }
        if (isApiError(failure) && failure.status === 422) {
          setError(t("assignments.create.invalidTargets"));
          return;
        }
        if (isApiError(failure) && failure.status === 403) {
          setError(t("assignments.create.forbidden"));
          return;
        }
        if (isApiError(failure) && failure.status === 503) {
          setError(t("assignments.create.targetsUnavailable"));
          return;
        }
        setError(isApiError(failure) ? failure.message : t("common.requestFailed"));
      },
    });
  };

  const selectedStudents = roster.students.filter(
    (student) =>
      typeof student.student_id === "string" && studentIds.includes(student.student_id),
  );

  return (
    <>
      <Modal title={t("assignments.create.title")} onClose={onClose} testId="assignment-composer">
        <div className="stack">
          <Field label={t("grades.course")} htmlFor="compose-course">
            <Select
              id="compose-course"
              value={selectedCourseId}
              onChange={(event) => {
                setCourseId(event.target.value);
                // 换课程等于换受众：已选课序与指定学生都不能沿用。
                setSectionIds([]);
                setStudentIds([]);
                setSelected([]);
              }}
              disabled={create.isPending}
            >
              {courseOptions.map((course) => (
                <option key={course.course_id} value={course.course_id}>
                  {courseOptionLabel(course)}
                </option>
              ))}
            </Select>
          </Field>

          <fieldset className="stack" data-testid="compose-section-list">
            <legend>
              {t("assignments.create.sections")}
              <span className="muted"> · {t("assignments.create.sectionsHint")}</span>
            </legend>
            {sectionsQuery.isPending ? (
              <p className="muted">
                <InlineLoader />
              </p>
            ) : sectionsQuery.isError ? (
              <Banner tone="error" testId="compose-sections-error">
                {describeApiError(sectionsQuery.error)}
              </Banner>
            ) : sectionOptions.length === 0 ? (
              <p className="muted">{t("assignments.create.noSections")}</p>
            ) : (
              <ul className="stack">
                {sectionOptions.map((option) => {
                  const checked = sectionIds.includes(option.sectionId);
                  return (
                    <li key={option.sectionId}>
                      <label className="inline">
                        <input
                          type="checkbox"
                          checked={checked}
                          disabled={create.isPending}
                          data-testid={`compose-section-${option.sectionId}`}
                          onChange={() => {
                            setSectionIds((previous) =>
                              checked
                                ? previous.filter((id) => id !== option.sectionId)
                                : [...previous, option.sectionId],
                            );
                            setStudentIds([]);
                          }}
                        />
                        <span>{option.label}</span>
                      </label>
                    </li>
                  );
                })}
              </ul>
            )}
          </fieldset>

          <fieldset className="stack" data-testid="compose-student-fieldset">
            <legend>
              {t("assignments.create.students")}
              <span className="muted"> · {t("assignments.create.studentsHint")}</span>
            </legend>
            {sectionIds.length === 0 ? (
              <p className="muted">{t("assignments.create.studentsNeedSections")}</p>
            ) : roster.isPending ? (
              <p className="muted" data-testid="compose-roster-loading">
                <InlineLoader />
              </p>
            ) : roster.isError ? (
              <Banner tone="error" testId="compose-roster-error">
                {describeApiError(roster.error)}
              </Banner>
            ) : roster.truncated ? (
              <Banner tone="warn" testId="compose-roster-truncated">
                {t("assignments.create.rosterTruncated")}
              </Banner>
            ) : (
              <>
                <p className="muted">{t("assignments.create.wholeSections")}</p>
                {roster.students.filter((student) => student.student_id !== null).length === 0 ? (
                  <p className="muted">{t("assignments.create.noStudents")}</p>
                ) : (
                  <ul className="stack" data-testid="compose-student-list">
                    {roster.students
                      .filter((student) => student.student_id !== null)
                      .map((student) => {
                        const studentId = student.student_id as string;
                        const checked = studentIds.includes(studentId);
                        return (
                          <li key={student.enrollment_id}>
                            <label className="inline">
                              <input
                                type="checkbox"
                                checked={checked}
                                disabled={create.isPending}
                                data-testid={`compose-student-${studentId}`}
                                onChange={() =>
                                  setStudentIds((previous) =>
                                    checked
                                      ? previous.filter((id) => id !== studentId)
                                      : [...previous, studentId],
                                  )
                                }
                              />
                              <span>
                                {student.display_name} · {student.student_number}
                              </span>
                            </label>
                          </li>
                        );
                      })}
                  </ul>
                )}
                {roster.students.some((student) => student.student_id === null) ? (
                  <p className="muted" data-testid="compose-unbound-note">
                    {t("assignments.create.unboundNote")}
                  </p>
                ) : null}
              </>
            )}
          </fieldset>

          <Field label={t("assignments.create.name")} htmlFor="compose-title">
            <input
              id="compose-title"
              value={title}
              maxLength={200}
              placeholder={t("assignments.create.namePlaceholder")}
              onChange={(event) => setTitle(event.target.value)}
              disabled={create.isPending}
            />
          </Field>

          <Field
            label={t("assignments.create.due")}
            htmlFor="compose-due"
            hint={t("assignments.create.dueHint")}
          >
            <input
              id="compose-due"
              type="datetime-local"
              value={dueAt}
              onChange={(event) => setDueAt(event.target.value)}
              disabled={create.isPending}
            />
          </Field>

          <fieldset className="stack">
            <legend>
              {t("assignments.create.versions")}
              <span className="muted"> · {t("assignments.create.versionsHint")}</span>
            </legend>
            {questions.isPending ? (
              <p className="muted">
                <InlineLoader />
              </p>
            ) : publishedVersions.length === 0 ? (
              <p className="muted">{t("assignments.create.noVersions")}</p>
            ) : (
              <ul className="stack" data-testid="compose-version-list">
                {publishedVersions.map((version) => {
                  const checked = selected.includes(version.question_version_id);
                  return (
                    <li key={version.question_version_id}>
                      <label className="inline">
                        <input
                          type="checkbox"
                          checked={checked}
                          disabled={create.isPending}
                          onChange={() =>
                            setSelected((previous) =>
                              checked
                                ? previous.filter((id) => id !== version.question_version_id)
                                : [...previous, version.question_version_id],
                            )
                          }
                        />
                        <span>
                          {version.questionTitle} · v{version.version}
                        </span>
                      </label>
                    </li>
                  );
                })}
              </ul>
            )}
          </fieldset>

          {error ? (
            <Banner tone="error" testId="assignment-compose-error">
              {error}
            </Banner>
          ) : null}

          <div className="inline">
            <button type="button" className="button button--ghost" onClick={onClose}>
              {t("common.cancel")}
            </button>
            <button
              type="button"
              className="button"
              data-testid="assignment-compose-preview"
              disabled={create.isPending}
              onClick={submit}
            >
              {t("assignments.create.preview")}
            </button>
          </div>
        </div>
      </Modal>

      {previewing ? (
        <Modal title={t("assignments.create.confirmTitle")} onClose={() => setPreviewing(false)}>
          <div className="stack" data-testid="assignment-compose-confirm">
            <p>{t("assignments.create.confirmBody")}</p>
            <dl className="stack">
              <div>
                <dt className="muted">{t("grades.course")}</dt>
                <dd>{selectedCourse ? courseOptionLabel(selectedCourse) : selectedCourseId}</dd>
              </div>
              <div>
                <dt className="muted">{t("assignments.create.sections")}</dt>
                <dd data-testid="compose-preview-sections">
                  {sectionIds.map(sectionLabelOf).join("、")}
                </dd>
              </div>
              <div>
                <dt className="muted">{t("assignments.create.students")}</dt>
                <dd data-testid="compose-preview-students">
                  {studentIds.length === 0
                    ? t("assignments.create.wholeSections")
                    : t("assignments.create.chosenStudents", { count: studentIds.length }) +
                      (selectedStudents.length > 0
                        ? `（${selectedStudents
                            .map(
                              (student) =>
                                `${student.display_name} ${student.student_number}`,
                            )
                            .join("、")}）`
                        : "")}
                </dd>
              </div>
              <div>
                <dt className="muted">{t("assignments.create.name")}</dt>
                <dd>{title.trim()}</dd>
              </div>
              <div>
                <dt className="muted">{t("assignments.create.due")}</dt>
                <dd>{dueAt ? new Date(dueAt).toLocaleString() : t("assignments.noDue")}</dd>
              </div>
              <div>
                <dt className="muted">{t("assignments.create.versions")}</dt>
                <dd>{t("assignments.create.chosen", { count: selected.length })}</dd>
              </div>
            </dl>
            <div className="inline">
              <button
                type="button"
                className="button button--ghost"
                onClick={() => setPreviewing(false)}
                disabled={create.isPending}
              >
                {t("common.cancel")}
              </button>
              <button
                type="button"
                className="button"
                data-testid="assignment-compose-confirm-button"
                onClick={publish}
                disabled={create.isPending}
              >
                {create.isPending ? t("assignments.create.saving") : t("assignments.create.confirm")}
              </button>
            </div>
          </div>
        </Modal>
      ) : null}
    </>
  );
}
