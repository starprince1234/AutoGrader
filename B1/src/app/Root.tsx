import { QueryClientProvider } from "@tanstack/react-query";
import { RouterProvider } from "@tanstack/react-router";
import { ErrorBoundary } from "./ErrorBoundary";
import { queryClient } from "./queryClient";
import { router } from "./router";
import { SessionProvider } from "../auth/SessionProvider";

/** 应用根组件（只导出组件；启动副作用留在 `main.tsx`）。 */
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
