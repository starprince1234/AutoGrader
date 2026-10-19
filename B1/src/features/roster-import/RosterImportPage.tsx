import { useMemo, useState } from "react";
import { t, type MessageKey } from "../../i18n";
import type { RosterImportPreview, RosterRowPreview } from "../../api/types";
import { collectColumns, type OpaqueRow } from "../../api/opaque";
import { maskCellValue } from "../../ui/masking";
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
import { describeApiError } from "../../api/problem";
import { loadedCourses, useCourses, useSections } from "../../api/directory";
import {
  TARGET_FIELDS,
  duplicateAssignments,
  fieldMessageKey,
  isCommitted,
  isPreviewable,
  needsMapping,
  validateMapping,
  type MappingDraft,
  type TargetField,
} from "./mapping";
import {
  useConfirmRosterImport,
  useCreateRosterImport,
  useRosterImport,
  useSetRosterMapping,
} from "./useRosterImport";

const STATUS_TONE: Record<RosterImportPreview["status"], "ok" | "warn" | "bad" | "idle"> = {
  uploaded: "idle",
  parsed: "idle",
  mapping_required: "warn",
  validating: "warn",
  previewed: "ok",
  committed: "ok",
  rejected: "bad",
  failed_parse: "bad",
};

/** 状态 → 文案键（显式映射，避免用模板串拼出并不存在的键）。 */
const STATUS_MESSAGE: Record<RosterImportPreview["status"], MessageKey> = {
  uploaded: "import.status.uploaded",
  parsed: "import.status.parsed",
  mapping_required: "import.status.mapping_required",
  validating: "import.status.validating",
  previewed: "import.status.previewed",
  committed: "import.status.committed",
  rejected: "import.status.rejected",
  failed_parse: "import.status.failed_parse",
};

function statusLabel(status: RosterImportPreview["status"]): string {
  return t(STATUS_MESSAGE[status]);
}

/** 逐行预览/结果报告表格：列来自服务端返回的键，敏感列脱敏（学号除外）。 */
function RosterRowsTable({
  rows,
  rejected,
}: {
  rows: RosterRowPreview[];
  rejected: boolean;
}) {
  const valueColumns = useMemo(
    () => collectColumns(rows.map((row) => (row.values ?? {}) as OpaqueRow)),
    [rows],
  );

  if (rows.length === 0) {
    return <p className="muted">{t("common.empty")}</p>;
  }

  return (
    <Table<RosterRowPreview>
      testId={rejected ? "rejected-rows" : "accepted-rows"}
      rows={rows}
      getRowKey={(row, index) => `${row.row_number}-${index}`}
      columns={[
        {
          key: "row_number",
          header: t("import.rowNumber"),
          render: (row) => row.row_number,
        },
        ...valueColumns.map((column) => ({
          key: column,
          header: column,
          render: (row: RosterRowPreview) => {
            const values = (row.values ?? {}) as OpaqueRow;
            return maskCellValue(column, values[column]);
          },
        })),
        ...(rejected
          ? [
              {
                key: "reason",
                header: t("import.reason"),
                render: (row: RosterRowPreview) => row.reason ?? t("common.none"),
              },
            ]
          : []),
      ]}
    />
  );
}

function MappingForm({ preview }: { preview: RosterImportPreview }) {
  const [draft, setDraft] = useState<MappingDraft>({});
  const [localError, setLocalError] = useState<string[]>([]);
  const mutation = useSetRosterMapping(preview.roster_import_id);

  const duplicates = duplicateAssignments(draft);

  return (
    <form
      className="stack"
      data-testid="mapping-form"
      onSubmit={(event) => {
        event.preventDefault();
        const validation = validateMapping(draft, preview.detected_columns);
        if (duplicates.length > 0) {
          setLocalError([t("import.error.duplicateColumn", { column: duplicates.join("、") })]);
          return;
        }
        if (!validation.ok) {
          const errors: string[] = [];
          if (validation.missing.length > 0) {
            errors.push(
              t("import.error.missingMapping", {
                fields: validation.missing.map((field) => t(fieldMessageKey(field))).join("、"),
              }),
            );
          }
          if (validation.unknownColumns.length > 0) {
            errors.push(
              t("import.error.unknownColumn", {
                column: validation.unknownColumns.join("、"),
              }),
            );
          }
          setLocalError(errors);
          return;
        }
        setLocalError([]);
        mutation.mutate(validation.columns);
      }}
    >
      <Banner tone="warn" testId="mapping-required-notice">
        {t("import.mappingRequired")} {t("import.notGuessing")}
      </Banner>

      <p className="muted">
        {t("import.detectedColumns")}：{preview.detected_columns.join("、")}
      </p>
      {preview.proposed_mapping && Object.keys(preview.proposed_mapping).length > 0 ? (
        <p className="muted" data-testid="proposed-mapping-reference">
          服务端建议（仅供参考，需人工确认）：
          {Object.entries(preview.proposed_mapping)
            .map(([field, column]) => `${field}→${column}`)
            .join("，")}
        </p>
      ) : null}

      <div className="form-grid">
        {TARGET_FIELDS.map((field: TargetField) => {
          const selectId = `mapping-${field}`;
          return (
            <Field key={field} label={t("import.mappingTarget", { field: t(fieldMessageKey(field)) })} htmlFor={selectId}>
              <select
                id={selectId}
                value={draft[field] ?? ""}
                onChange={(event) => {
                  const value = event.target.value;
                  setDraft((previous) => ({ ...previous, [field]: value }));
                }}
              >
                <option value="">{t("import.unmapped")}</option>
                {preview.detected_columns.map((column) => (
                  <option key={column} value={column}>
                    {column}
                  </option>
                ))}
              </select>
            </Field>
          );
        })}
      </div>

      {localError.length > 0 ? (
        <Banner tone="error" testId="mapping-error">
          <ul>
            {localError.map((message) => (
              <li key={message}>{message}</li>
            ))}
          </ul>
        </Banner>
      ) : null}
      {mutation.isError ? <ErrorState error={mutation.error} /> : null}

      <div className="inline">
        <button type="submit" className="button" disabled={mutation.isPending}>
          {mutation.isPending ? t("import.mappingSubmitting") : t("import.mappingSubmit")}
        </button>
      </div>
    </form>
  );
}

function PreviewAndConfirm({ preview }: { preview: RosterImportPreview }) {
  const confirm = useConfirmRosterImport(preview.roster_import_id);
  const committed = isCommitted(preview.status);

  return (
    <div className="stack">
      {committed ? (
        <Banner tone="success" testId="import-committed">
          {t("import.confirmed", { id: preview.roster_import_id })}
        </Banner>
      ) : (
        <Banner tone="info" testId="no-write-notice">
          {t("import.noWriteBeforeConfirm")}
        </Banner>
      )}

      <p className="muted" data-testid="masking-notice">
        {t("import.maskedNotice")}
      </p>

      <Panel
        title={t("import.acceptedRows", { count: preview.accepted_rows?.length ?? 0 })}
      >
        <RosterRowsTable rows={preview.accepted_rows ?? []} rejected={false} />
      </Panel>

      <Panel
        title={t("import.rejectedRows", { count: preview.rejected_rows?.length ?? 0 })}
        testId="rejected-panel"
      >
        <Banner tone="warn">{t("import.noRowDropped")}</Banner>
        <RosterRowsTable rows={preview.rejected_rows ?? []} rejected />
      </Panel>

      {!committed ? (
        <div className="inline">
          <button
            type="button"
            className="button"
            data-testid="confirm-import"
            disabled={confirm.isPending}
            onClick={() => confirm.mutate()}
          >
            {confirm.isPending ? t("import.confirming") : t("import.confirm")}
          </button>
          <span className="muted">{t("import.idempotencyNotice")}</span>
        </div>
      ) : null}
      {confirm.isError ? <ErrorState error={confirm.error} /> : null}
    </div>
  );
}

export function RosterImportPage() {
  const courses = useCourses();
  const courseOptions = loadedCourses(courses.data?.pages);
  const [courseId, setCourseId] = useState("");
  const [sectionId, setSectionId] = useState("");
  const [file, setFile] = useState<File | null>(null);
  const [importId, setImportId] = useState<string | null>(null);
  const [uploadError, setUploadError] = useState<unknown>(null);
  const [uploadMessage, setUploadMessage] = useState<string | null>(null);

  const sections = useSections(courseId === "" ? null : courseId);
  const createImport = useCreateRosterImport();
  const previewQuery = useRosterImport(importId);
  const preview = previewQuery.data;

  return (
    <div className="stack">
      <PageTitle>{t("nav.rosterImport")}</PageTitle>

      <Panel title={t("import.uploadTitle")}>
        {/* 序号由 `<ol>` 自己产生。文案里再写一遍 "1." 会渲染成 "1. 1. 上传文件"。 */}
        <ol className="steps muted">
          <li>{t("import.step.upload")}</li>
          <li>{t("import.step.preview")}</li>
          <li>{t("import.step.mapping")}</li>
          <li>{t("import.step.confirm")}</li>
          <li>{t("import.step.result")}</li>
        </ol>

        <form
          className="form-grid"
          data-testid="upload-form"
          onSubmit={(event) => {
            event.preventDefault();
            setUploadError(null);
            setUploadMessage(null);
            if (!file || courseId === "" || sectionId === "") return;
            createImport.mutate(
              { file, courseId, sectionId },
              {
                onSuccess: (created) => {
                  setImportId(created.roster_import_id);
                  if (!needsMapping(created.status) && !isPreviewable(created.status)) {
                    setUploadMessage(statusLabel(created.status));
                  }
                },
                onError: (error) => setUploadError(error),
              },
            );
          }}
        >
          <Field label={t("import.course")} htmlFor="import-course">
            <select
              id="import-course"
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

          <Field label={t("import.section")} htmlFor="import-section">
            <select
              id="import-section"
              value={sectionId}
              onChange={(event) => setSectionId(event.target.value)}
              disabled={courseId === ""}
            >
              <option value="">{t("common.selectPrompt")}</option>
              {(sections.data ?? []).map((section) => (
                <option key={section.sectionId} value={section.sectionId}>
                  {section.label}
                </option>
              ))}
            </select>
          </Field>

          <Field label={t("import.file")} htmlFor="import-file">
            <input
              id="import-file"
              type="file"
              accept=".pdf,.xlsx,application/pdf,application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"
              onChange={(event) => setFile(event.target.files?.[0] ?? null)}
            />
          </Field>

          <div className="inline">
            <button
              type="submit"
              className="button"
              data-testid="upload-submit"
              disabled={createImport.isPending || !file || courseId === "" || sectionId === ""}
            >
              {createImport.isPending ? t("import.uploading") : t("import.upload")}
            </button>
          </div>
        </form>

        <div className="inline">
          <span className="muted" data-testid="import-courses-count">
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
        {sections.isError ? <ErrorState error={sections.error} /> : null}
        {uploadError ? (
          <Banner tone="error" testId="upload-error">
            <strong>{t("import.parseFailed")}</strong>
            <p>{describeApiError(uploadError)}</p>
          </Banner>
        ) : null}
        {uploadMessage ? <Banner tone="warn">{uploadMessage}</Banner> : null}
      </Panel>

      {importId === null ? null : previewQuery.isPending ? (
        <Spinner />
      ) : previewQuery.isError ? (
        <ErrorState error={previewQuery.error} />
      ) : preview ? (
        <Panel title={`${t("import.title")} · ${statusLabel(preview.status)}`}>
          <div className="inline">
            <StatusPill tone={STATUS_TONE[preview.status] ?? "idle"}>
              {statusLabel(preview.status)}
            </StatusPill>
            <span className="muted">
              {t("import.detectedColumns")}：{preview.detected_columns.join("、") || t("common.none")}
            </span>
          </div>

          {preview.status === "failed_parse" ? (
            <Banner tone="error">{t("import.parseFailed")}</Banner>
          ) : null}

          {needsMapping(preview.status) ? (
            <MappingForm preview={preview} />
          ) : isPreviewable(preview.status) || isCommitted(preview.status) ? (
            <PreviewAndConfirm preview={preview} />
          ) : preview.status === "rejected" ? (
            <Banner tone="error" testId="import-rejected">
              {t("import.status.rejected")}
            </Banner>
          ) : preview.status === "uploaded" ? (
            <Banner tone="info" testId="import-uploaded">
              {t("import.status.uploaded")}
            </Banner>
          ) : (
            <Banner tone="warn">{t("import.status.validating")}</Banner>
          )}
        </Panel>
      ) : null}
    </div>
  );
}
