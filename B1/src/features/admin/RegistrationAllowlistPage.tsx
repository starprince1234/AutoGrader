import { useCallback, useEffect, useState } from "react";
import { apiRequest } from "../../api/client";
import { describeApiError } from "../../api/problem";
import { t } from "../../i18n";
import { Banner, Field, PageTitle, Panel } from "../../ui/components";

/**
 * 注册白名单（`POST|GET /api/v1/registration-allowlists`，仅管理员）。
 *
 * 一次上传就是一次**全量替换**：名单是某个班/某个学期的快照，没出现在文件里的学号从此刻起
 * 不能再注册。页面因此把"当前生效的批次"和"本次上传覆盖了多少行"都显示出来，
 * 而不是只弹一句"成功"。
 */

type AllowlistState = {
  batch_id: string | null;
  filename: string | null;
  row_count: number;
  uploaded_at: string | null;
};

type ReplaceResult = {
  batch_id: string;
  row_count: number;
  replaced_row_count: number;
  content_sha256: string;
};

export function RegistrationAllowlistPage() {
  const [state, setState] = useState<AllowlistState | null>(null);
  const [file, setFile] = useState<File | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [notice, setNotice] = useState<string | null>(null);
  const [pending, setPending] = useState(false);

  const load = useCallback(async () => {
    try {
      const current = await apiRequest<AllowlistState>("/api/v1/registration-allowlists", {
        method: "GET",
      });
      setState(current);
    } catch (loadError) {
      setError(describeApiError(loadError));
    }
  }, []);

  useEffect(() => {
    void load();
  }, [load]);

  return (
    <>
      <PageTitle>{t("allowlist.title")}</PageTitle>
      <Panel title={t("allowlist.current")}>
        {state === null ? (
          <p className="muted">{t("common.loading")}</p>
        ) : state.batch_id === null ? (
          <p className="muted" data-testid="allowlist-empty">
            {t("allowlist.none")}
          </p>
        ) : (
          <dl className="stack" data-testid="allowlist-current">
            <div>
              <dt className="muted">{t("allowlist.filename")}</dt>
              <dd>{state.filename}</dd>
            </div>
            <div>
              <dt className="muted">{t("allowlist.rowCount")}</dt>
              <dd>{state.row_count}</dd>
            </div>
            <div>
              <dt className="muted">{t("allowlist.uploadedAt")}</dt>
              <dd>{state.uploaded_at}</dd>
            </div>
          </dl>
        )}
      </Panel>

      <Panel title={t("allowlist.upload")}>
        <form
          className="stack"
          onSubmit={async (event) => {
            event.preventDefault();
            setError(null);
            setNotice(null);
            if (!file) {
              setError(t("allowlist.fileRequired"));
              return;
            }
            setPending(true);
            try {
              const formData = new FormData();
              formData.append("file", file);
              const result = await apiRequest<ReplaceResult>(
                "/api/v1/registration-allowlists",
                { method: "POST", formData },
              );
              setNotice(
                t("allowlist.replaced", {
                  rows: result.row_count,
                  replaced: result.replaced_row_count,
                }),
              );
              setFile(null);
              await load();
            } catch (uploadError) {
              setError(describeApiError(uploadError));
            } finally {
              setPending(false);
            }
          }}
        >
          <p className="muted">{t("allowlist.hint")}</p>
          <Field label={t("allowlist.chooseFile")} htmlFor="allowlist-file">
            <input
              id="allowlist-file"
              type="file"
              accept=".csv,text/csv"
              data-testid="allowlist-file"
              onChange={(event) => setFile(event.target.files?.[0] ?? null)}
            />
          </Field>
          {notice ? (
            <Banner tone="success" testId="allowlist-notice">
              {notice}
            </Banner>
          ) : null}
          {error ? (
            <Banner tone="error" testId="allowlist-error">
              {error}
            </Banner>
          ) : null}
          <button
            type="submit"
            className="button"
            data-testid="allowlist-submit"
            disabled={pending || file === null}
          >
            {pending ? t("allowlist.uploading") : t("allowlist.submit")}
          </button>
        </form>
      </Panel>
    </>
  );
}
