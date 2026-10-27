import { useMemo, useState } from "react";
import { t, type MessageKey } from "../../i18n";
import type { RosterImportPreview, RosterRowPreview } from "../../api/types";
import { collectColumns, type OpaqueRow } from "../../api/opaque";
import { maskCellValue } from "../../ui/masking";
import {
  Banner,
  ErrorState,
  Field,
  Panel,
  Spinner,
  StatusPill,
  Table,
} from "../../ui/components";
import { describeApiError, isApiError } from "../../api/problem";
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

/**
 * 名单导入（P12 的第二个标签，不再是独立页面）。
 *
 * 流程身份是服务端的 `roster_import_id`，由调用方放进 URL：
 * 刷新、复制链接、浏览器后退都能恢复同一个任务，而组件里的 state 只保存还没提交的文件。
 * 确认前的一切（解析、列映射、逐行校验）都只写任务暂存区，不写正式名单。
 */

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

function RosterRowsTable({ rows, rejected }: { rows: RosterRowPreview[]; rejected: boolean }) {
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
        { key: "row_number", header: t("import.rowNumber"), render: (row) => row.row_number },
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
              t("import.error.unknownColumn", { column: validation.unknownColumns.join("、") }),
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
            <Field
              key={field}
              label={t("import.mappingTarget", { field: t(fieldMessageKey(field)) })}
              htmlFor={selectId}
            >
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

      <Panel title={t("import.acceptedRows", { count: preview.accepted_rows?.length ?? 0 })}>
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

export function RosterImportPanel({
  courseId,
  sectionId,
  importId,
  onImportId,
}: {
  courseId: string;
  sectionId: string;
  importId: string | null;
  /** 任务 ID 由 URL 持有：这里只把服务端返回的 id 交回去写进 query。 */
  onImportId: (id: string | null) => void;
}) {
  const [file, setFile] = useState<File | null>(null);
  const [uploadError, setUploadError] = useState<unknown>(null);
  const createImport = useCreateRosterImport();
  const previewQuery = useRosterImport(importId);
  const preview = previewQuery.data;
  const ready = courseId !== "" && sectionId !== "";

  const expired =
    previewQuery.isError &&
    isApiError(previewQuery.error) &&
    previewQuery.error.status === 404;

  return (
    <div className="stack">
      <Panel title={t("import.uploadTitle")}>
        {ready ? null : (
          <Banner tone="warn" testId="import-needs-scope">
            {t("import.needsScope")}
          </Banner>
        )}

        <form
          className="form-grid"
          data-testid="upload-form"
          onSubmit={(event) => {
            event.preventDefault();
            setUploadError(null);
            if (!file || !ready) return;
            createImport.mutate(
              { file, courseId, sectionId },
              {
                onSuccess: (created) => onImportId(created.roster_import_id),
                onError: (error) => setUploadError(error),
              },
            );
          }}
        >
          <Field label={t("import.file")} htmlFor="import-file">
            <input
              id="import-file"
              type="file"
              accept=".csv,.xlsx,text/csv,application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"
              onChange={(event) => setFile(event.target.files?.[0] ?? null)}
            />
          </Field>

          <div className="inline">
            <button
              type="submit"
              className="button"
              data-testid="upload-submit"
              disabled={createImport.isPending || !file || !ready}
            >
              {createImport.isPending ? t("import.uploading") : t("import.upload")}
            </button>
            {importId ? (
              <button
                type="button"
                className="button button--ghost"
                data-testid="import-reset"
                onClick={() => {
                  setFile(null);
                  setUploadError(null);
                  onImportId(null);
                }}
              >
                {t("import.startOver")}
              </button>
            ) : null}
          </div>
        </form>

        {uploadError ? (
          <Banner tone="error" testId="upload-error">
            <strong>{t("import.parseFailed")}</strong>
            <p>{describeApiError(uploadError)}</p>
          </Banner>
        ) : null}
      </Panel>

      {importId === null ? null : previewQuery.isPending ? (
        <Spinner label={t("import.loading")} />
      ) : expired ? (
        <Banner tone="warn" testId="import-expired">
          {t("import.expired")}
        </Banner>
      ) : previewQuery.isError ? (
        <ErrorState error={previewQuery.error} testId="import-error" />
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
