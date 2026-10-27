import type { ReactNode } from "react";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { render, type RenderResult } from "@testing-library/react";
import {
  Outlet,
  RouterProvider,
  createMemoryHistory,
  createRootRoute,
  createRoute,
  createRouter,
} from "@tanstack/react-router";
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

/**
 * 需要 `Link`/`useNavigate` 的页面测试：把页面挂进一条真实的最小路由。
 *
 * 页面里的导航链接必须指向已注册的路由，否则路由器会报"找不到路由"——
 * 这些占位路由只为满足注册表，不参与断言。
 */
export function renderWithRouter(
  ui: ReactNode,
  options: { path: string; linkTargets?: string[] },
): RenderResult {
  const rootRoute = createRootRoute({ component: () => <Outlet /> });
  const pageRoute = createRoute({
    getParentRoute: () => rootRoute,
    path: options.path,
    component: () => <>{ui}</>,
  });
  const linkRoutes = (options.linkTargets ?? []).map((path) =>
    createRoute({
      getParentRoute: () => rootRoute,
      path,
      component: () => <span>目标页占位</span>,
    }),
  );
  const router = createRouter({
    routeTree: rootRoute.addChildren([pageRoute, ...linkRoutes]),
    history: createMemoryHistory({ initialEntries: [options.path] }),
  });
  return renderWithProviders(<RouterProvider router={router} />);
}
