import type * as Monaco from "monaco-editor";

/**
 * Monaco 的加载与 worker 接线（非组件模块，便于 Fast Refresh 与测试注入）。
 *
 * 只导出加载器与类型，组件在 `CodeEditor.tsx`。
 */

export type MonacoNamespace = typeof Monaco;
export type MonacoLoader = () => Promise<MonacoNamespace>;

export const loadMonacoEditor: MonacoLoader = async () => {
  const [monaco, workerModule] = await Promise.all([
    import("monaco-editor"),
    import("monaco-editor/esm/vs/editor/editor.worker?worker"),
  ]);
  type MonacoEnvironment = { getWorker: () => Worker };
  (globalThis as unknown as { MonacoEnvironment?: MonacoEnvironment }).MonacoEnvironment = {
    getWorker: () => new workerModule.default(),
  };
  return monaco;
};
