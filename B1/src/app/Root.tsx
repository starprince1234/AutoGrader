import { QueryClientProvider } from "@tanstack/react-query";
import { RouterProvider } from "@tanstack/react-router";
import { ErrorBoundary } from "./ErrorBoundary";
import { queryClient } from "./queryClient";
import { router } from "./router";
import { SessionProvider } from "../auth/SessionProvider";
import { GeminiGlow } from "../ui/GeminiGlow";

/**
 * 应用根组件（只导出组件；启动副作用留在 `main.tsx`）。
 *
 * `GeminiGlow` 放在**所有页面之外**：它常驻、不需要触发，路由切换与登录/退出都不影响它，
 * 因此导览页、认证页、业务页共享同一层背景光。
 */
export function Root() {
  return (
    <ErrorBoundary>
      <GeminiGlow />
      <QueryClientProvider client={queryClient}>
        <SessionProvider>
          <RouterProvider router={router} />
        </SessionProvider>
      </QueryClientProvider>
    </ErrorBoundary>
  );
}
