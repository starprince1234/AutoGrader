import { useRef, useState } from "react";
import { t } from "../../i18n";
import { Banner, Field } from "../../ui/components";
import { IMAGE_CONTENT_TYPES, MAX_IMAGE_BYTES, assetMarkdown } from "./draft";
import { useUploadQuestionAsset } from "./useAuthoring";

/**
 * 题干图片上传（RQ-B-3-10，`uploadQuestionAsset`）。
 *
 * 教师视角只有一件事：**选一张图，它就出现在题干里**——不需要先手动保存草稿。
 *
 * 但契约里资源挂在题目上（`/questions/{id}/assets`），没有题目 id 就没有上传目标。折中放在这里：
 * **选完文件之后**再向父页面要一次题目 id（`ensureQuestionId`，父页面负责把表单存成一份最小草稿），
 * 拿到 id 立刻上传并插入。于是：
 *
 * 1. 点【选择文件】永远能打开文件对话框（它是用户手势，不依赖任何保存状态）；
 * 2. "先存草稿"是后台的一步，不是教师必须理解的前置条件；
 * 3. 缺课程/标题/题干这类真的拦得住的情况，用红色提示说明缺什么，而不是静默失败。
 *
 * 其余三条与契约对齐的行为不变：
 * - **不上传服务令牌、不自算摘要**：请求体只有 `content_base64` / `content_type` / `alt_text`，
 *   摘要由 B3 从字节算出；前端把摘要当成"服务端告诉我的绑定值"，不参与生成它。
 * - **已发布版本的内容是冻结的**：`frozen` 时按钮禁用并说明原因，不假装上传成功。
 * - **失败不插入**：只有拿到 `asset_id` 才拼受控 Markdown 地址；题干里不会出现一个坏 URL。
 *
 * 插入的地址形如 `/api/v1/questions/{question_id}/assets/{asset_id}`：它是**受控资源路径**，
 * 由 `useQuestionAsset` 带令牌读取，`urlPolicy` 也只允许这一类同源地址。
 */
export function ImageUploader({
  questionId,
  onInsert,
  ensureQuestionId,
  frozen = false,
}: {
  questionId: string | null;
  onInsert: (snippet: string) => void;
  /** 还没有题目 id 时向父页面要一个（父页面会存一份最小草稿）；返回 `null` 表示没拿到。 */
  ensureQuestionId?: () => Promise<string | null>;
  /** 已发布版本：内容冻结，服务端会 409。 */
  frozen?: boolean;
}) {
  const upload = useUploadQuestionAsset(questionId);
  const inputRef = useRef<HTMLInputElement>(null);
  const [alt, setAlt] = useState("");
  const [error, setError] = useState<string | null>(null);
  const [preparing, setPreparing] = useState(false);

  const pick = async (file: File | undefined) => {
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

    // 先把题目 id 定下来（必要时由父页面在后台存一份最小草稿），再读字节并上传。
    let targetId = questionId;
    if (targetId === null) {
      setPreparing(true);
      try {
        targetId = ensureQuestionId ? await ensureQuestionId() : null;
      } finally {
        setPreparing(false);
      }
      if (targetId === null) {
        if (inputRef.current) inputRef.current.value = "";
        return;
      }
    }

    const base64 = await readBase64(file).catch(() => "");
    if (base64 === "") {
      setError(t("authoring.image.readFailed"));
      return;
    }
    upload.mutate(
      {
        questionId: targetId,
        content_type: file.type,
        content_base64: base64,
        alt_text: alt.trim() === "" ? null : alt.trim(),
      },
      {
        onSuccess: (asset) => {
          onInsert(assetMarkdown(targetId as string, asset.asset_id, alt.trim() || asset.asset_id));
          setAlt("");
          if (inputRef.current) inputRef.current.value = "";
        },
      },
    );
  };

  const busy = upload.isPending || preparing;

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
        {/*
          原生 file 控件自带一行「未选择文件」，既不可去掉也不可样式化，所以把它藏起来、
          只留一个可见按钮。点按钮直接打开同一个文件对话框——不需要先保存草稿。
        */}
        <input
          ref={inputRef}
          id="authoring-image-file"
          type="file"
          accept={IMAGE_CONTENT_TYPES.join(",")}
          data-testid="image-file"
          hidden
          disabled={frozen || busy}
          onChange={(event) => {
            void pick(event.target.files?.[0]);
          }}
        />
        <button
          type="button"
          className="button button--ghost"
          data-testid="image-pick"
          disabled={frozen || busy}
          onClick={() => inputRef.current?.click()}
        >
          {t("authoring.image.pick")}
        </button>
        {busy ? <span className="muted">{t("authoring.image.uploading")}</span> : null}
      </div>
      <p className="muted">{t("authoring.image.hint")}</p>
      {frozen ? (
        <Banner tone="info" testId="image-disabled">
          {t("authoring.image.publishedFrozen")}
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

/** 读成 base64（只取 `data:` 里的 payload）。失败时抛出，由调用方转成一句人话。 */
function readBase64(file: File): Promise<string> {
  return new Promise((resolve, reject) => {
    const reader = new FileReader();
    reader.onerror = () => reject(new Error("read failed"));
    reader.onload = () => {
      const result = reader.result;
      if (typeof result !== "string") {
        reject(new Error("read failed"));
        return;
      }
      const comma = result.indexOf(",");
      resolve(comma === -1 ? "" : result.slice(comma + 1));
    };
    reader.readAsDataURL(file);
  });
}
