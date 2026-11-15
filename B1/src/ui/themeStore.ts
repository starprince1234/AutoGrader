import { useCallback, useSyncExternalStore } from "react";

export type ThemeMode = "light" | "dark";

export const THEME_STORAGE_KEY = "autograder.theme";

type Listener = () => void;

const listeners = new Set<Listener>();

function readStoredTheme(): ThemeMode | null {
  if (typeof window === "undefined") return null;
  try {
    const raw = window.localStorage?.getItem(THEME_STORAGE_KEY);
    return raw === "light" || raw === "dark" ? raw : null;
  } catch {
    return null;
  }
}

function writeStoredTheme(theme: ThemeMode | null): void {
  if (typeof window === "undefined") return;
  try {
    if (theme === null) {
      window.localStorage?.removeItem(THEME_STORAGE_KEY);
    } else {
      window.localStorage?.setItem(THEME_STORAGE_KEY, theme);
    }
  } catch {
    // 隐私模式或受限环境可能禁止写入 storage，保留内存与 DOM 切换即可。
  }
}

function readSystemTheme(): ThemeMode {
  if (typeof window !== "undefined" && typeof window.matchMedia === "function") {
    try {
      return window.matchMedia("(prefers-color-scheme: dark)").matches ? "dark" : "light";
    } catch {
      return "light";
    }
  }
  return "light";
}

function applyThemeToDocument(theme: ThemeMode | null): void {
  if (typeof document === "undefined" || !document.documentElement) return;
  if (theme === null) {
    delete document.documentElement.dataset.theme;
    document.documentElement.style.colorScheme = "";
    return;
  }
  document.documentElement.dataset.theme = theme;
  document.documentElement.style.colorScheme = theme;
}

const initialStored = readStoredTheme();
let currentTheme: ThemeMode = initialStored ?? readSystemTheme();
if (initialStored !== null) {
  applyThemeToDocument(initialStored);
}

function notify(): void {
  for (const listener of listeners) {
    listener();
  }
}

export function getTheme(): ThemeMode {
  return currentTheme;
}

export function setTheme(next: ThemeMode): void {
  currentTheme = next;
  writeStoredTheme(next);
  applyThemeToDocument(next);
  notify();
}

export function toggleTheme(): ThemeMode {
  const next: ThemeMode = currentTheme === "dark" ? "light" : "dark";
  setTheme(next);
  return next;
}

export function resetTheme(): void {
  writeStoredTheme(null);
  applyThemeToDocument(null);
  currentTheme = readSystemTheme();
  notify();
}

export function subscribeTheme(listener: Listener): () => void {
  listeners.add(listener);
  return () => {
    listeners.delete(listener);
  };
}

export function useTheme(): readonly [ThemeMode, (next: ThemeMode) => void, () => ThemeMode] {
  const theme = useSyncExternalStore(subscribeTheme, getTheme, getTheme);
  const update = useCallback((next: ThemeMode) => {
    setTheme(next);
  }, []);
  const toggle = useCallback(() => toggleTheme(), []);
  return [theme, update, toggle] as const;
}
