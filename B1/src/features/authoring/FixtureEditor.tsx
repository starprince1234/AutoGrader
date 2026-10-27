import { useRef, useState } from "react";
import { t } from "../../i18n";
import { isApiError } from "../../api/problem";
import { Banner, Field } from "../../ui/components";
import {
  MAX_FIXTURE_BYTES,
  isValidFixturePath,
  type FixtureForm,
  type FixtureMount,
} from "./draft";
import { useUploadFixture } from "./useAuthoring";

/** 文件大小的可读形式。只用在本页的夹具表里，因此留在本页而不是共享格式模块。 */
function formatBytes(bytes: number): string {
  if (bytes < 1024) return `${bytes} B`;
  if (bytes < 1024 * 1024) return `${(bytes / 1024).toFixed(1)} KiB`;
  return `${(bytes / 1024 / 1024).toFixed(1)} MiB`;
}

/** 摘要的短码：完整值仍在 `title` 与可选中文本里，表格不被 64 字符撑开。 */
function shortenDigest(digest: string): string {
  return digest.length > 12 ? `${digest.slice(0, 12)}…` : digest;
}

/**
 * 夹具编辑器（`adapter.files[]` + `POST /question-versions/{id}/fixtures`，RQ-B-3-50）。
 *
 * 为什么这里有几步而不是"选个文件就完事"：
 *
 * 1. **必须先有版本 id**：夹具挂在版本上，所以要么题目已保存，要么先保存草稿。未保存时本控件
 *    禁用并说明原因——上传失败却显示"已保存"是最容易糊弄自己的一步。
 * 2. **逻辑路径由教师写**：文件在沙箱工作区里的相对路径（`data/in.txt`）。绝对路径、盘符、
 *    `..` 段与 `~` 一律拒绝，与服务端的 `validate_workspace_path` 同一条规则。
 * 3. **摘要只能来自服务端**：上传成功后 B3 返回 `content_sha256`，本控件显示它的短码并把它作为
 *    `adapter.files[]` 的声明值。前端从不自己算 SHA-256——那不是浏览器该有的权威。
 * 4. **移除是本地声明层面的移除**：把这条声明从 manifest 里去掉。已上传的字节不会被删除
 *    （服务端没有删除端点的契约面），因此"移除"只影响这次保存提交的 manifest。这一点要如实说明。
 */
export function FixtureEditor({
  versionId,
  questionId,
  fixtures,
  onChange,
  disabled,
  disabledReason,
  onlyInputMounts,
}: {
  versionId: string | null;
  questionId: string | null;
  fixtures: FixtureForm[];
  onChange: (next: FixtureForm[]) => void;
  disabled: boolean;
  disabledReason?: string;
  /** `stdin`/`callable` 只能挂 input：选择期望输出会被服务端拒绝，此处先提示。 */
  onlyInputMounts: boolean;
}) {
  const upload = useUploadFixture(versionId, questionId);
  const inputRef = useRef<HTMLInputElement>(null);
  const [path, setPath] = useState("");
  const [mount, setMount] = useState<FixtureMount>("input");
  const [error, setError] = useState<string | null>(null);
  const [note, setNote] = useState<string | null>(null);

  const pathIssue = path !== "" && !isValidFixturePath(path) ? t("authoring.fixture.pathInvalid") : null;

  const pick = (file: File | undefined) => {
    setError(null);
    setNote(null);
    if (!file) return;
    if (path === "") {
      setError(t("authoring.fixture.pathRequired"));
      return;
    }
    if (!isValidFixturePath(path)) {
      setError(t("authoring.fixture.pathInvalid"));
      return;
    }
    if (file.size > MAX_FIXTURE_BYTES) {
      setError(t("authoring.fixture.tooLarge", { max: Math.round(MAX_FIXTURE_BYTES / 1024) }));
      return;
    }
    if (!versionId) {
      setError(t("authoring.fixture.needVersion"));
      return;
    }
    const duplicate = fixtures.some((item) => item.path === path && item.mount === mount);
    if (duplicate) {
      setError(t("authoring.fixture.duplicate", { path, mount }));
      return;
    }
    const reader = new FileReader();
    reader.onerror = () => setError(t("authoring.fixture.readFailed"));
    reader.onload = () => {
      const result = reader.result;
      if (typeof result !== "string" || !result.includes(",")) {
        setError(t("authoring.fixture.readFailed"));
        return;
      }
      const base64 = result.slice(result.indexOf(",") + 1);
      upload.mutate(
        { path, mount, content_base64: base64 },
        {
          onSuccess: (fixture) => {
            onChange([
              ...fixtures,
              {
                fixtureId: fixture.fixture_id,
                path: fixture.path,
                mount: fixture.mount,
                // 服务端算出的摘要，原样使用。
                contentSha256: fixture.content_sha256,
                sizeBytes: fixture.size_bytes,
                fileName: file.name,
              },
            ]);
            setPath("");
            setNote(t("authoring.fixture.uploadedNote", { path: fixture.path }));
            if (inputRef.current) inputRef.current.value = "";
          },
        },
      );
    };
    reader.readAsDataURL(file);
  };

  return (
    <div className="stack" data-testid="fixture-editor">
      {disabled ? (
        <Banner tone="info" testId="fixture-disabled">
          {disabledReason ?? t("authoring.fixture.needVersion")}
        </Banner>
      ) : null}

      <div className="form-grid">
        <Field label={t("authoring.fixture.path")} htmlFor="fixture-path" error={pathIssue}>
          <input
            id="fixture-path"
            value={path}
            placeholder="data/input.txt"
            onChange={(event) => setPath(event.target.value)}
          />
        </Field>
        <Field label={t("authoring.fixture.mount")} htmlFor="fixture-mount">
          <select
            id="fixture-mount"
            value={mount}
            onChange={(event) => setMount(event.target.value as FixtureMount)}
          >
            <option value="input">input</option>
            {onlyInputMounts ? null : <option value="expected_output">expected_output</option>}
          </select>
        </Field>
      </div>
      <p className="muted">{t("authoring.fixture.pathHint")}</p>

      <div className="inline">
        <input
          ref={inputRef}
          id="fixture-file"
          type="file"
          data-testid="fixture-file"
          disabled={disabled || upload.isPending}
          onChange={(event) => pick(event.target.files?.[0])}
        />
        {upload.isPending ? (
          <span className="muted">{t("authoring.fixture.uploading")}</span>
        ) : null}
      </div>
      <p className="muted">{t("authoring.fixture.digestHint")}</p>

      {error ? (
        <Banner tone="error" testId="fixture-error">
          {error}
        </Banner>
      ) : null}
      {note ? (
        <Banner tone="success" testId="fixture-note">
          {note}
        </Banner>
      ) : null}
      {upload.isError ? (
        <Banner tone="error" testId="fixture-upload-error">
          {isApiError(upload.error) && upload.error.problem?.detail
            ? upload.error.problem.detail
            : t("authoring.fixture.uploadFailed")}
        </Banner>
      ) : null}

      {fixtures.length === 0 ? (
        <p className="muted" data-testid="fixture-empty">
          {t("authoring.fixture.none")}
        </p>
      ) : (
        <table className="table" aria-label={t("authoring.fixture.list")} data-testid="fixture-table">
          <thead>
            <tr>
              <th scope="col">{t("authoring.fixture.path")}</th>
              <th scope="col">{t("authoring.fixture.mount")}</th>
              <th scope="col">{t("authoring.fixture.size")}</th>
              <th scope="col">{t("authoring.fixture.digest")}</th>
              <th scope="col">{t("authoring.fixture.actions")}</th>
            </tr>
          </thead>
          <tbody>
            {fixtures.map((fixture) => (
              <tr key={`${fixture.path}-${fixture.mount}`}>
                <td>
                  <code>{fixture.path}</code>
                  <div className="muted">{fixture.fileName}</div>
                </td>
                <td>{fixture.mount}</td>
                <td>{formatBytes(fixture.sizeBytes)}</td>
                <td>
                  <span className="mono-id" title={fixture.contentSha256}>
                    {shortenDigest(fixture.contentSha256)}
                  </span>
                </td>
                <td>
                  <button
                    type="button"
                    className="button button--ghost"
                    data-testid={`fixture-remove-${fixture.path}`}
                    onClick={() =>
                      onChange(
                        fixtures.filter(
                          (item) =>
                            !(item.path === fixture.path && item.mount === fixture.mount),
                        ),
                      )
                    }
                  >
                    {t("authoring.fixture.remove")}
                  </button>
                </td>
              </tr>
            ))}
          </tbody>
        </table>
      )}
      <p className="muted" data-testid="fixture-remove-note">
        {t("authoring.fixture.removeNote")}
      </p>
    </div>
  );
}
