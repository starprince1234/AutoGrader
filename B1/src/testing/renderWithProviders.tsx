import type { ReactNode } from "react";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { render, type RenderResult } from "@testing-library/react";
import { SessionProvider } from "../auth/SessionProvider";

/**
 * 组件测试的 Provider 包装：真实的 QueryClient（关闭重试，让失败快速显现）
 * 与真实的会话 Provider（会话状态由 `signIn` 走真实代码路径建立，不做假状态）。
 */
export function renderWithProviders(ui: ReactNode): RenderResult {
  const client = new QueryClient({
    defaultOptions: {
      queries: { retry: false, gcTime: 0, staleTime: 0 },
      mutations: { retry: false },
    },
  });
  return render(
    <QueryClientProvider client={client}>
      <SessionProvider>{ui}</SessionProvider>
    </QueryClientProvider>,
  );
}
