import { createContext, useContext } from "react";
import { getSessionState, type SessionState } from "./sessionStore";

/**
 * 会话 Context 与读取 Hook。
 *
 * 该文件**只导出非组件**（context 与 hook），组件（`SessionProvider`）单独放在
 * `SessionProvider.tsx`：这样 React Fast Refresh 不需要因为"同文件混导出"而放弃
 * 热更新（eslint `react-refresh/only-export-components`）。
 */
export const SessionContext = createContext<SessionState>(getSessionState());

export function useSession(): SessionState {
  return useContext(SessionContext);
}
