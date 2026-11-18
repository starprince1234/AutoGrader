import type { IoType, Language } from "../../api/types";

export const IO_TYPE_OPTIONS: readonly IoType[] = ["stdin", "file", "callable"];

const IO_TYPE_LABELS: Record<IoType, string> = {
  stdin: "命令行输入",
  file: "文件读取",
  callable: "函数调用",
};

const IO_TYPE_DESCRIPTIONS: Record<IoType, string> = {
  stdin: "通过命令行参数与标准输入（stdin）接收测试数据，向标准输出（stdout）打印结果。",
  file: "从工作区相对路径读取输入文件，按要求在工作区写出产出文件或输出到 stdout。",
  callable: "按规定模块与函数名实现接口，由平台 harness import / source 后直接调用测试。",
};

const IO_TYPE_TONES: Record<IoType, "idle" | "warn" | "ok"> = {
  stdin: "idle",
  file: "warn",
  callable: "ok",
};

export function ioTypeLabel(ioType: IoType): string {
  return IO_TYPE_LABELS[ioType];
}

export function ioTypeDescription(ioType: IoType): string {
  return IO_TYPE_DESCRIPTIONS[ioType];
}

export function ioTypeTone(ioType: IoType): "idle" | "warn" | "ok" {
  return IO_TYPE_TONES[ioType];
}

export function ioTypeEntryFilename(
  ioType: IoType,
  language: Language,
  callableModule?: string | null,
): string {
  const ext = language === "python" ? "py" : "sh";
  if (ioType === "callable" && callableModule && callableModule.trim()) {
    return `${callableModule.trim().replaceAll(".", "/")}.${ext}`;
  }
  return `main.${ext}`;
}

export function formatFixtureBytes(bytes: number): string {
  if (bytes < 1024) return `${bytes} B`;
  if (bytes < 1024 * 1024) return `${(bytes / 1024).toFixed(1)} KiB`;
  return `${(bytes / (1024 * 1024)).toFixed(2)} MiB`;
}
