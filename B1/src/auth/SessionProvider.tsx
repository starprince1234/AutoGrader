import { useSyncExternalStore, type ReactNode } from "react";
import { getSessionState, subscribeSession } from "./sessionStore";
import { SessionContext } from "./sessionContext";

/** 把框架无关的会话状态机接进 React（`useSyncExternalStore` 保证并发渲染下一致）。 */
export function SessionProvider({ children }: { children: ReactNode }) {
  const state = useSyncExternalStore(
    subscribeSession,
    getSessionState,
    getSessionState,
  );
  return <SessionContext.Provider value={state}>{children}</SessionContext.Provider>;
}
