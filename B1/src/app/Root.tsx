import { QueryClientProvider } from "@tanstack/react-query";
import { RouterProvider } from "@tanstack/react-router";
import { ErrorBoundary } from "./ErrorBoundary";
import { queryClient } from "./queryClient";
import { router } from "./router";
import { SessionProvider } from "../auth/SessionProvider";

/**
 * 应用根组件（只导出组件；启动副作用留在 `main.tsx`）。
 *
 * 背景光晕（`GeminiGlow`）**不在这里渲染**：它只在导览页（`/`）出现，由 `AppShell`
 * 的公开分支挂载。内部页面（作业、题库、成绩、复核、用户管理）与认证页都用 `body`
 * 上那层静态渐变做底色，不再带动画光晕——长时间停留在这些页面时不持续占用合成器，
 * 也不会把读数的注意力拉走。
 */
export function Root() {
  return (
    <ErrorBoundary>
      <QueryClientProvider client={queryClient}>
        <SessionProvider>
          <RouterProvider router={router} />
        </SessionProvider>
      </QueryClientProvider>
    </ErrorBoundary>
  );
}
