/**
 * 页面切换的交叉淡化（原生 View Transitions API）。
 *
 * 灵感来源：GSAP 文档里经典的 page-transition overlay（旧页淡出、新页淡入）与
 * uiverse 上大量"过渡条/交叉淡化"的写法。这里不引任何库，只用浏览器自己的
 * `document.startViewTransition`：它会先给旧页面拍一张快照，等我们的回调把内容换完，
 * 再给新页面拍一张，然后由合成器把两张图交叉淡化——整个过程不碰布局、不重绘。
 *
 * 为什么要手动接：`@tanstack/react-router@1.170` 这个版本既没有 `viewTransition`
 * 选项，也没有内建的 `startViewTransition` 调用（只在类型注释里提了一句）。
 * 所以这里用"捕获阶段监听同源链接点击"的方式，在路由更新**之前**开启转场。
 *
 * 三个安全边界：
 * 1. 不支持 View Transitions 的浏览器（含 jsdom）直接不安装，页面行为与之前完全一致；
 * 2. `prefers-reduced-motion: reduce` 时同样不安装（不闪、不淡入）；
 * 3. 等待路由变化最多 300ms，之后立刻放行——即使点到的链接根本没跳转，
 *    页面也只会停留一瞬，不会卡住。
 */

type ViewTransitionDocument = Document & {
  startViewTransition?: (callback: () => Promise<void>) => { finished: Promise<void> };
};

/** 等到下一帧：让 React 提交 + 浏览器绘制完成，快照才拿得到新内容。 */
function nextFrame(): Promise<void> {
  return new Promise((resolve) => {
    globalThis.requestAnimationFrame(() => resolve());
  });
}

/** 等 URL 真的变了（最多 300ms），再补两帧确保新内容已经绘制。 */
async function waitForRouteChange(fromHref: string): Promise<void> {
  const deadline = performance.now() + 300;
  while (globalThis.location.href === fromHref && performance.now() < deadline) {
    await nextFrame();
  }
  await nextFrame();
  await nextFrame();
}

/** 返回安装时的清理函数；不支持/低动态偏好时是个空操作。 */
export function installViewTransitions(): () => void {
  const doc = document as ViewTransitionDocument;
  if (typeof doc.startViewTransition !== "function") return () => {};

  const reduceMotion = globalThis.matchMedia?.("(prefers-reduced-motion: reduce)");
  if (reduceMotion?.matches) return () => {};

  let running = false;

  const begin = (wait: () => Promise<void>) => {
    if (running) return;
    running = true;
    try {
      const transition = doc.startViewTransition?.(wait);
      transition?.finished.finally(() => {
        running = false;
      });
    } catch {
      // 上一次转场还没结束、或浏览器拒绝：静默退回普通切换。
      running = false;
    }
  };

  const onClick = (event: MouseEvent) => {
    if (event.defaultPrevented || event.button !== 0) return;
    if (event.metaKey || event.ctrlKey || event.shiftKey || event.altKey) return;
    const target = event.target as Element | null;
    const anchor = target?.closest?.("a[href]") as HTMLAnchorElement | null;
    if (!anchor) return;
    if (anchor.getAttribute("target") === "_blank") return;
    if (anchor.hasAttribute("download")) return;
    const href = anchor.getAttribute("href") ?? "";
    // 只接站内绝对路径；外链、锚点、mailto 等一律不碰。
    if (!href.startsWith("/")) return;
    if (anchor.href === globalThis.location.href) return;
    // 不 preventDefault：让路由照常处理，转场只负责"拍快照"。
    begin(() => waitForRouteChange(globalThis.location.href));
  };

  const onPopState = () => {
    // 前进/后退时 URL 已经变了，等两帧让路由把内容渲染出来再拍新快照。
    begin(async () => {
      await nextFrame();
      await nextFrame();
    });
  };

  document.addEventListener("click", onClick, true);
  globalThis.addEventListener("popstate", onPopState);
  return () => {
    document.removeEventListener("click", onClick, true);
    globalThis.removeEventListener("popstate", onPopState);
  };
}
