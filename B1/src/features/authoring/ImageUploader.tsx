import { useRef, useState } from "react";
import { t } from "../../i18n";
import { Banner, Field } from "../../ui/components";
import { IMAGE_CONTENT_TYPES, MAX_IMAGE_BYTES, assetMarkdown } from "./draft";
import { useUploadQuestionAsset } from "./useAuthoring";

/**
 * 题干图片上传（RQ-B-3-10，`uploadQuestionAsset`）。
 *
 * 三条与契约对齐的行为：
 *
 * 1. **不上传服务令牌、不自算摘要**：请求体只有 `content_base64` / `content_type` / `alt_text`，
 *    摘要由 B3 从字节算出。前端把摘要当成"服务端告诉我的绑定值"，不参与生成它。
 * 2. **只在草稿版本上可用**：资源挂在题目上，已发布版本的内容是冻结的（服务端会 409）。
 *    因此题目尚未保存时这里的按钮是禁用的，并说明原因——不假装上传成功。
 * 3. **失败不插入**：只有拿到服务端返回的 `asset_id` 才拼受控 Markdown 地址；失败时表单原样保留，
 *    题干里不会出现一个坏 URL（那会让 P07 出现"图片暂时无法显示"而且没人知道为什么）。
 *
 * 插入的地址形如 `/api/v1/questions/{question_id}/assets/{asset_id}`：它是**受控资源路径**，
 * 由 `useQuestionAsset` 带令牌读取，`urlPolicy` 也只允许这一类同源地址。
 */
export function ImageUploader({
  questionId,
  onInsert,
  disabled,
  disabledReason,
}: {
  questionId: string | null;
  onInsert: (snippet: string) => void;
  disabled: boolean;
  disabledReason?: string;
}) {
  const upload = useUploadQuestionAsset(questionId);
  const inputRef = useRef<HTMLInputElement>(null);
  const [alt, setAlt] = useState("");
  const [error, setError] = useState<string | null>(null);

  const pick = (file: File | undefined) => {
    setError(null);
    if (!file) return;
    if (!IMAGE_CONTENT_TYPES.includes(file.type as (typeof IMAGE_CONTENT_TYPES)[number])) {
      setError(t("authoring.image.typeRejected", { types: IMAGE_CONTENT_TYPES.join(", ") }));
      return;
    }
    if (file.size > MAX_IMAGE_BYTES) {
      setError(t("authoring.image.tooLarge", { max: Math.round(MAX_IMAGE_BYTES / 1024 / 1024) }));
      return;
    }
    const reader = new FileReader();
    reader.onerror = () => setError(t("authoring.image.readFailed"));
    reader.onload = () => {
      const result = reader.result;
      if (typeof result !== "string") {
        setError(t("authoring.image.readFailed"));
        return;
      }
      // `readAsDataURL` 的产物是 `data:<type>;base64,<payload>`；只把 payload 上行。
      const base64 = result.includes(",") ? result.slice(result.indexOf(",") + 1) : "";
      if (base64 === "") {
        setError(t("authoring.image.readFailed"));
        return;
      }
      upload.mutate(
        { content_type: file.type, content_base64: base64, alt_text: alt.trim() === "" ? null : alt.trim() },
        {
          onSuccess: (asset) => {
            if (!questionId) return;
            onInsert(assetMarkdown(questionId, asset.asset_id, alt.trim() || asset.asset_id));
            setAlt("");
            if (inputRef.current) inputRef.current.value = "";
          },
        },
      );
    };
    reader.readAsDataURL(file);
  };

  return (
    <div className="stack" data-testid="image-uploader">
      <Field label={t("authoring.image.alt")} htmlFor="authoring-image-alt">
        <input
          id="authoring-image-alt"
          value={alt}
          onChange={(event) => setAlt(event.target.value)}
          placeholder={t("authoring.image.altPlaceholder")}
        />
      </Field>
      <div className="inline">
        <input
          ref={inputRef}
          id="authoring-image-file"
          type="file"
          accept={IMAGE_CONTENT_TYPES.join(",")}
          data-testid="image-file"
          disabled={disabled || upload.isPending}
          onChange={(event) => pick(event.target.files?.[0])}
        />
        {upload.isPending ? <span className="muted">{t("authoring.image.uploading")}</span> : null}
      </div>
      <p className="muted">{t("authoring.image.hint")}</p>
      {disabled && disabledReason ? (
        <Banner tone="info" testId="image-disabled">
          {disabledReason}
        </Banner>
      ) : null}
      {error ? (
        <Banner tone="error" testId="image-error">
          {error}
        </Banner>
      ) : null}
      {upload.isError ? (
        <Banner tone="error" testId="image-upload-error">
          {t("authoring.image.uploadFailed")}
        </Banner>
      ) : null}
    </div>
  );
}
