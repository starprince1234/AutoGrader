import { useState } from "react";
import { apiUrl } from "../../api/client";
import type { IoType, Language, QuestionVersionPublic } from "../../api/types";
import { getAccessToken } from "../../auth/sessionStore";
import { Banner, Panel, StatusPill } from "../../ui/components";
import {
  formatFixtureBytes,
  ioTypeDescription,
  ioTypeEntryFilename,
  ioTypeLabel,
  ioTypeTone,
} from "./ioType";

export function IoTypeBadge({
  ioType,
  testId,
}: {
  ioType: IoType;
  testId?: string;
}) {
  return (
    <StatusPill tone={ioTypeTone(ioType)} testId={testId}>
      {ioTypeLabel(ioType)}
    </StatusPill>
  );
}

type VisibleFixture = NonNullable<QuestionVersionPublic["visible_fixtures"]>[number];

export function IoSpecPanel({
  ioType,
  language,
  questionVersionId,
  ioSpec,
  visibleFixtures = [],
  testId = "io-spec-panel",
}: {
  ioType: IoType;
  language: Language;
  questionVersionId?: string;
  ioSpec?: QuestionVersionPublic["io_spec"];
  visibleFixtures?: VisibleFixture[];
  testId?: string;
}) {
  const [downloadError, setDownloadError] = useState<string | null>(null);
  const [downloadingId, setDownloadingId] = useState<string | null>(null);

  const entryFile = ioTypeEntryFilename(ioType, language, ioSpec?.callable_module);
  const expectedOutputs = ioSpec?.expected_output_paths ?? [];

  async function handleDownload(fixture: VisibleFixture) {
    if (!questionVersionId) return;
    setDownloadError(null);
    setDownloadingId(fixture.fixture_id);
    try {
      const token = getAccessToken();
      const headers = new Headers();
      if (token) headers.set("Authorization", `Bearer ${token}`);
      const resp = await fetch(
        apiUrl(
          `/api/v1/question-versions/${questionVersionId}/fixtures/${fixture.fixture_id}/download`,
        ),
        { headers },
      );
      if (!resp.ok) {
        setDownloadError(`附件 ${fixture.path} 下载失败（HTTP ${resp.status}）`);
        return;
      }
      const blob = await resp.blob();
      const objectUrl = URL.createObjectURL(blob);
      const anchor = document.createElement("a");
      anchor.href = objectUrl;
      anchor.download = fixture.path.split("/").pop() ?? fixture.path;
      document.body.appendChild(anchor);
      anchor.click();
      anchor.remove();
      URL.revokeObjectURL(objectUrl);
    } catch {
      setDownloadError(`附件 ${fixture.path} 下载失败，请稍后重试`);
    } finally {
      setDownloadingId(null);
    }
  }

  return (
    <Panel title="IO 规范说明" testId={testId}>
      <div className="stack">
        <p className="muted" style={{ margin: 0 }}>
          <IoTypeBadge ioType={ioType} testId={`${testId}-badge`} />{" "}
          <span>{ioTypeDescription(ioType)}</span>
        </p>

        {ioType === "stdin" && (
          <ul style={{ margin: 0, paddingLeft: "1.25rem" }}>
            <li>
              运行命令：<code>{language === "python" ? `python ${entryFile}` : `bash ${entryFile}`}</code>
            </li>
            <li>输入方式：命令行参数（argv）与标准输入（stdin）</li>
            <li>输出方式：标准输出（stdout）</li>
          </ul>
        )}

        {ioType === "file" && (
          <div className="stack">
            <ul style={{ margin: 0, paddingLeft: "1.25rem" }}>
              <li>
                入口文件：<code>{entryFile}</code>（请按相对工作区根目录的路径读写文件）
              </li>
              {expectedOutputs.length > 0 && (
                <li>
                  期望产出文件：{" "}
                  {expectedOutputs.map((path, index) => (
                    <span key={path}>
                      {index > 0 ? "、" : ""}
                      <code>{path}</code>
                    </span>
                  ))}
                </li>
              )}
            </ul>

            {visibleFixtures.length > 0 && (
              <div className="stack" data-testid={`${testId}-attachments`}>
                <strong>学生可见样例附件</strong>
                {downloadError && <Banner tone="error">{downloadError}</Banner>}
                <ul style={{ margin: 0, paddingLeft: "1.25rem" }}>
                  {visibleFixtures.map((fixture) => (
                    <li
                      key={fixture.fixture_id}
                      style={{ display: "flex", alignItems: "center", gap: "0.5rem", flexWrap: "wrap" }}
                    >
                      <code>{fixture.path}</code>
                      <span className="muted">
                        ({formatFixtureBytes(fixture.size_bytes)}, sha256: {fixture.content_sha256.slice(0, 12)}…)
                      </span>
                      {questionVersionId && (
                        <button
                          type="button"
                          className="button button--ghost"
                          disabled={downloadingId === fixture.fixture_id}
                          onClick={() => void handleDownload(fixture)}
                          data-testid={`download-fixture-${fixture.fixture_id}`}
                        >
                          {downloadingId === fixture.fixture_id ? "下载中…" : "下载附件"}
                        </button>
                      )}
                    </li>
                  ))}
                </ul>
              </div>
            )}
          </div>
        )}

        {ioType === "callable" && (
          <ul style={{ margin: 0, paddingLeft: "1.25rem" }}>
            <li>
              必须提交的文件名：<code>{entryFile}</code>
            </li>
            <li>
              必须实现的{language === "python" ? "函数/可调用对象" : "Shell 函数"}：
              <code>{ioSpec?.callable_attribute ?? "未指定"}</code>
            </li>
            <li>
              调用方式：
              {language === "python"
                ? `平台将 import ${ioSpec?.callable_module ?? "main"} 并调用 ${ioSpec?.callable_attribute ?? "solve"}`
                : `平台将 source ./${entryFile} 并调用函数 ${ioSpec?.callable_attribute ?? "solve"}`}
            </li>
          </ul>
        )}
      </div>
    </Panel>
  );
}
