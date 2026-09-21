import { useEffect, useRef, useState } from "react";
import { t } from "../../i18n";
import type { Language } from "../../api/types";
import { loadMonacoEditor, type MonacoLoader } from "./monaco";

/**
 * Monaco 代码编辑器（RQ-B-1-50）。
 *
 * 两个刻意的设计：
 * 1. 加载器**可注入**：真实路径走动态 `import("monaco-editor")` 与官方 worker；
 *    测试注入桩件即可在 jsdom 下验证交互，不需要 canvas / Web Worker 这类不可控能力。
 * 2. 加载失败**降级为纯文本输入框**并显式提示，而不是把编辑器区域留白。
 */

const LANGUAGE_ID: Record<Language, string> = { python: "python", bash: "shell" };

type EditorHandle = {
  dispose: () => void;
  getValue: () => string;
  setValue: (value: string) => void;
  onDidChangeModelContent: (listener: () => void) => void;
};

export function CodeEditor({
  value,
  language,
  onChange,
  loader = loadMonacoEditor,
  testId = "code-editor",
}: {
  value: string;
  language: Language;
  onChange: (next: string) => void;
  loader?: MonacoLoader;
  testId?: string;
}) {
  const containerRef = useRef<HTMLDivElement | null>(null);
  const editorRef = useRef<EditorHandle | null>(null);
  const valueRef = useRef(value);
  const [failed, setFailed] = useState(false);

  useEffect(() => {
    let disposed = false;
    let handle: EditorHandle | null = null;

    void loader()
      .then((monaco) => {
        if (disposed || !containerRef.current) return;
        handle = monaco.editor.create(containerRef.current, {
          value: valueRef.current,
          language: LANGUAGE_ID[language],
          automaticLayout: true,
          minimap: { enabled: false },
          fontSize: 13,
          scrollBeyondLastLine: false,
          tabSize: 4,
        }) as unknown as EditorHandle;
        editorRef.current = handle;
        handle.onDidChangeModelContent(() => {
          const next = handle?.getValue() ?? "";
          valueRef.current = next;
          onChange(next);
        });
      })
      .catch(() => {
        if (!disposed) setFailed(true);
      });

    return () => {
      disposed = true;
      editorRef.current = null;
      handle?.dispose();
    };
    // 编辑器实例只在挂载/切换语言时创建；外部值变更由下面的 effect 同步。
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [language]);

  useEffect(() => {
    valueRef.current = value;
    const handle = editorRef.current;
    if (handle && handle.getValue() !== value) handle.setValue(value);
  }, [value]);

  if (failed) {
    return (
      <div className="stack">
        <p className="muted">{t("submit.editorFallback")}</p>
        <textarea
          data-testid={`${testId}-fallback`}
          value={value}
          spellCheck={false}
          aria-label={t("submit.title")}
          onChange={(event) => onChange(event.target.value)}
        />
      </div>
    );
  }

  return <div className="editor" ref={containerRef} data-testid={testId} />;
}
