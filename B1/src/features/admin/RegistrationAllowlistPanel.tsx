import { useRef, useState, type FormEvent } from "react";
import { describeApiError, isApiError } from "../../api/problem";
import { t } from "../../i18n";
import { Banner, EmptyState, ErrorState, Field, Modal, Panel, Spinner } from "../../ui/components";
import { formatDateTime } from "../../ui/format";
import { useRegistrationAllowlist, useReplaceRegistrationAllowlist } from "./useAllowlist";

/**
 * 注册名单标签（P14 的另一半）：读取当前生效批次 + 上传 CSV 做**全量替换**。
 *
 * 这个上传是全站唯一一处"一个文件决定谁能注册"的写操作，因此顺序被写死为
 * 选择文件 → 说明影响 → 管理员确认 → 一个 multipart 请求。选择文件、打开确认框都**不发请求**，
 * 确认框里展示的是当前批次与本次文件名，而不是浏览器解析出来的学生明细。
 *
 * 失败面也按语义分开：403 是权限、413 是体积、422 是文件内容、5xx 是服务端故障；连接中断则
 * 既不报成功也不报失败——先 GET 当前批次，再让管理员决定是否重试（不自动重放写请求）。
 */

/** B4 的默认上传上限（`config.DEFAULT_REGISTRATION_ALLOWLIST_MAX_BYTES`）。仅用于提示。 */
const DEFAULT_MAX_BYTES = 2 * 1024 * 1024;

function formatBytes(bytes: number): string {
  if (bytes < 1024) return `${bytes} B`;
  if (bytes < 1024 * 1024) return `${(bytes / 1024).toFixed(1)} KiB`;
  return `${(bytes / (1024 * 1024)).toFixed(2)} MiB`;
}

/**
 * 只按扩展名预检——**内容**由 B4 判定（编码、表头、13 位学号、重复行、空名单）。
 * 前端不做"看起来像 CSV"的猜测：猜错的代价是把一份非法文件说成合法。
 */
function isCsv(file: File): boolean {
  return file.name.toLowerCase().endsWith(".csv");
}

export function RegistrationAllowlistPanel() {
  const current = useRegistrationAllowlist();
  const replace = useReplaceRegistrationAllowlist();
  const inputRef = useRef<HTMLInputElement>(null);

  const [file, setFile] = useState<File | null>(null);
  const [fileError, setFileError] = useState<string | null>(null);
  const [confirmOpen, setConfirmOpen] = useState(false);
  const [replacement, setReplacement] = useState<{
    row_count: number;
    replaced_row_count: number;
  } | null>(null);
  const [uncertain, setUncertain] = useState(false);

  const batch = current.data ?? null;
  const hasBatch = batch !== null && batch.batch_id !== null;

  const clearFile = () => {
    setFile(null);
    if (inputRef.current) inputRef.current.value = "";
  };

  const openConfirm = (event: FormEvent) => {
    event.preventDefault();
    if (replace.isPending) return;
    if (file === null) {
      setFileError(t("allowlist.fileRequired"));
      return;
    }
    setFileError(null);
    setConfirmOpen(true);
  };

  const submit = () => {
    if (file === null || replace.isPending) return;
    setUncertain(false);
    replace.mutate(file, {
      onSuccess: (result) => {
        setReplacement({
          row_count: result.row_count,
          replaced_row_count: result.replaced_row_count,
        });
        setConfirmOpen(false);
        clearFile();
      },
      onError: (error) => {
        // 连接中断（不是 ProblemDetails）意味着服务端可能已经提交：页面不宣称失败，也不重发。
        if (!isApiError(error)) setUncertain(true);
        setConfirmOpen(false);
      },
    });
  };

  return (
    <div className="stack" data-testid="allowlist-panel">
      <Panel
        title={t("allowlist.current")}
        actions={
          <button
            type="button"
            className="button button--ghost"
            data-testid="allowlist-refresh"
            disabled={current.isFetching}
            onClick={() => {
              void current.refetch();
            }}
          >
            {current.isFetching ? t("common.loading") : t("allowlist.refresh")}
          </button>
        }
      >
        {current.isPending ? (
          <Spinner />
        ) : current.isError ? (
          <AllowlistLoadError error={current.error} onRetry={() => void current.refetch()} />
        ) : hasBatch && batch !== null ? (
          <dl className="facts" data-testid="allowlist-current">
            <div className="facts__row">
              <dt>{t("allowlist.filename")}</dt>
              <dd>{batch.filename ?? t("common.none")}</dd>
            </div>
            <div className="facts__row">
              <dt>{t("allowlist.rowCount")}</dt>
              <dd data-testid="allowlist-row-count">{batch.row_count}</dd>
            </div>
            <div className="facts__row">
              <dt>{t("allowlist.uploadedAt")}</dt>
              <dd data-testid="allowlist-uploaded-at">
                {formatDateTime(batch.uploaded_at, t("common.none"))}
              </dd>
            </div>
          </dl>
        ) : (
          <div className="stack" data-testid="allowlist-empty">
            <EmptyState>{t("allowlist.none")}</EmptyState>
            <p className="muted">{t("allowlist.noneHint")}</p>
          </div>
        )}
      </Panel>

      <Panel title={t("allowlist.upload")}>
        <form className="stack" onSubmit={openConfirm} data-testid="allowlist-form">
          <p className="muted" data-testid="allowlist-impact">
            {t("allowlist.impact")}
          </p>
          <p className="muted">{t("allowlist.impactDetail")}</p>
          <p className="muted">{t("allowlist.hint")}</p>

          <Field label={t("allowlist.chooseFile")} htmlFor="allowlist-file" error={fileError}>
            <input
              id="allowlist-file"
              ref={inputRef}
              type="file"
              accept=".csv,text/csv"
              data-testid="allowlist-file"
              onChange={(event) => {
                setReplacement(null);
                setUncertain(false);
                replace.reset();
                const picked = event.target.files?.[0] ?? null;
                if (picked !== null && !isCsv(picked)) {
                  setFileError(t("allowlist.wrongType"));
                  setFile(null);
                  return;
                }
                setFileError(null);
                setFile(picked);
              }}
            />
          </Field>

          {file ? (
            <p className="muted" data-testid="allowlist-selected">
              {t("allowlist.selected", {
                name: file.name,
                size: formatBytes(file.size),
              })}
            </p>
          ) : null}
          {file && file.size > DEFAULT_MAX_BYTES ? (
            <Banner tone="warn" testId="allowlist-size-warning">
              {t("allowlist.sizeWarning", {
                size: formatBytes(file.size),
                limit: formatBytes(DEFAULT_MAX_BYTES),
              })}
            </Banner>
          ) : null}

          <div className="inline">
            <button
              type="submit"
              className="button"
              data-testid="allowlist-continue"
              disabled={file === null || replace.isPending || fileError !== null}
            >
              {t("allowlist.continue")}
            </button>
          </div>
        </form>

        {replacement ? (
          <Banner tone="success" testId="allowlist-notice">
            {t("allowlist.replaced", {
              rows: replacement.row_count,
              replaced: replacement.replaced_row_count,
            })}
          </Banner>
        ) : null}

        {uncertain ? (
          <Banner tone="warn" testId="allowlist-uncertain">
            <p>{t("allowlist.uncertainTitle")}</p>
            <p>{t("allowlist.uncertain")}</p>
            <div className="inline">
              <button
                type="button"
                className="button"
                disabled={current.isFetching}
                onClick={() => {
                  void current.refetch();
                }}
              >
                {t("allowlist.refresh")}
              </button>
            </div>
          </Banner>
        ) : null}

        {replace.isError && !uncertain ? (
          <AllowlistUploadError
            error={replace.error}
            onRefresh={() => {
              void current.refetch();
            }}
          />
        ) : null}
      </Panel>

      {confirmOpen && file ? (
        <Modal
          title={t("allowlist.confirmTitle")}
          testId="allowlist-confirm-modal"
          onClose={() => {
            if (replace.isPending) return;
            setConfirmOpen(false);
          }}
        >
          <div className="stack">
            <dl className="facts">
              <div className="facts__row">
                <dt>{t("allowlist.confirmCurrentLabel")}</dt>
                <dd data-testid="allowlist-confirm-current">
                  {batch !== null && batch.batch_id !== null
                    ? t("allowlist.confirmCurrent", {
                        filename: batch.filename ?? t("common.none"),
                        rows: batch.row_count,
                        time: formatDateTime(batch.uploaded_at, t("common.none")),
                      })
                    : t("allowlist.confirmNoCurrent")}
                </dd>
              </div>
              <div className="facts__row">
                <dt>{t("allowlist.confirmFileLabel")}</dt>
                <dd data-testid="allowlist-confirm-file">
                  {t("allowlist.selected", {
                    name: file.name,
                    size: formatBytes(file.size),
                  })}
                </dd>
              </div>
            </dl>
            <Banner tone="warn">{t("allowlist.impact")}</Banner>
            <p className="muted">{t("allowlist.impactDetail")}</p>
            <p className="muted">{t("allowlist.confirmOnce")}</p>

            <div className="inline">
              <button
                type="button"
                className="button button--danger"
                data-testid="allowlist-confirm"
                disabled={replace.isPending}
                onClick={submit}
              >
                {replace.isPending ? t("allowlist.uploading") : t("allowlist.confirmSubmit")}
              </button>
              <button
                type="button"
                className="button button--ghost"
                data-testid="allowlist-cancel"
                disabled={replace.isPending}
                onClick={() => setConfirmOpen(false)}
              >
                {t("common.cancel")}
              </button>
            </div>
            {replace.isPending ? (
              <p className="muted" role="status" data-testid="allowlist-pending">
                {t("allowlist.uploading")}
              </p>
            ) : null}
          </div>
        </Modal>
      ) : null}
    </div>
  );
}

/** 读取批次失败：403 与一般故障分开，二者都不显示"尚未导入注册名单"。 */
function AllowlistLoadError({ error, onRetry }: { error: unknown; onRetry: () => void }) {
  if (isApiError(error) && error.isForbidden) {
    return (
      <Banner tone="error" testId="allowlist-forbidden">
        {t("allowlist.forbidden")}
      </Banner>
    );
  }
  return (
    <div className="stack">
      <ErrorState error={error} testId="allowlist-load-error" />
      <div className="inline">
        <button
          type="button"
          className="button"
          data-testid="allowlist-retry-load"
          onClick={onRetry}
        >
          {t("common.retry")}
        </button>
      </div>
    </div>
  );
}

/**
 * 上传失败的可读反馈。
 *
 * 每一类都写清"当前生效名单没有被改"——除了 5xx，那里服务端可能已提交也可能没有，
 * 所以只说明"未确认"，并要求刷新当前批次核对。
 */
function AllowlistUploadError({ error, onRefresh }: { error: unknown; onRefresh: () => void }) {
  if (!isApiError(error)) return null;
  if (error.status === 413) {
    return (
      <Banner tone="error" testId="allowlist-too-large">
        <p>{t("allowlist.errorTooLarge")}</p>
        <p className="muted">{describeApiError(error)}</p>
      </Banner>
    );
  }
  if (error.isForbidden) {
    return (
      <Banner tone="error" testId="allowlist-error-forbidden">
        <p>{t("allowlist.errorForbidden")}</p>
      </Banner>
    );
  }
  if (error.isUnprocessable) {
    return (
      <Banner tone="error" testId="allowlist-error-invalid">
        <p>{t("allowlist.errorInvalid")}</p>
        <p className="muted">{describeApiError(error)}</p>
        <p className="muted">{t("allowlist.errorKept")}</p>
      </Banner>
    );
  }
  if (error.status >= 500) {
    return (
      <div className="stack">
        <ErrorState error={error} testId="allowlist-error-server" />
        <Banner tone="warn">
          <p>{t("allowlist.errorServer")}</p>
          <div className="inline">
            <button type="button" className="button" onClick={onRefresh}>
              {t("allowlist.refresh")}
            </button>
          </div>
        </Banner>
      </div>
    );
  }
  return <ErrorState error={error} testId="allowlist-error" />;
}
