import { afterEach, describe, expect, it } from "vitest";
import {
  createBrowserHistory,
  createRootRoute,
  createRoute,
  createRouter,
} from "@tanstack/react-router";

/**
 * 登录后回跳用的是 `navigate({ href })`（目标路径在运行期才知道）。生产部署在 `/app/`
 * 子路径下，因此这里单独验证：带 basepath 的 router 仍然把 `/grades` 解析成一条真实的
 * 站内路由，浏览器地址栏得到 `/app/grades`，而不是 `/app/app/grades`。
 */
describe("带 basepath 的动态回跳", () => {
  afterEach(() => {
    window.history.replaceState(null, "", "/");
  });

  it("href 使用站内绝对路径，路由匹配与最终 URL 都正确", async () => {
    window.history.replaceState(null, "", "/app/assignments");
    const rootRoute = createRootRoute();
    const assignmentsRoute = createRoute({
      getParentRoute: () => rootRoute,
      path: "/assignments",
    });
    const gradesRoute = createRoute({
      getParentRoute: () => rootRoute,
      path: "/grades",
    });
    const router = createRouter({
      routeTree: rootRoute.addChildren([assignmentsRoute, gradesRoute]),
      basepath: "/app",
      history: createBrowserHistory(),
    });
    await router.load();

    await router.navigate({ href: "/grades?course_id=abc", replace: true });

    expect(router.state.location.pathname).toBe("/grades");
    expect(router.state.location.search).toEqual({ course_id: "abc" });
    expect(window.location.pathname).toBe("/app/grades");
    expect(window.location.search).toBe("?course_id=abc");
  });
});
