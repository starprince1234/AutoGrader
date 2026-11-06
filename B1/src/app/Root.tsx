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
      {/*
        gooey 按钮用的滤镜：三团"液滴"各自是圆，靠这个模糊 + 提高对比的矩阵糊成一整块。
        放在根组件里只定义一次，导览页与内部页面的按钮都能引用 `url(#goo)`。
        尺寸为 0、不参与布局，也不进读屏。
      */}
      <svg className="goo-defs" aria-hidden="true" focusable="false" width="0" height="0">
        <defs>
          <filter id="goo">
            <feGaussianBlur in="SourceGraphic" stdDeviation="8" result="blur" />
            <feColorMatrix
              in="blur"
              mode="matrix"
              values="1 0 0 0 0  0 1 0 0 0  0 0 1 0 0  0 0 0 19 -9"
              result="goo"
            />
            <feComposite in="SourceGraphic" in2="goo" operator="atop" />
          </filter>
        </defs>
      </svg>
      <QueryClientProvider client={queryClient}>
        <SessionProvider>
          <RouterProvider router={router} />
        </SessionProvider>
      </QueryClientProvider>
    </ErrorBoundary>
  );
}
