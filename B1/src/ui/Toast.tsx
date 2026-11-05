import { useEffect, useRef } from "react";

/**
 * 屏幕中间一闪而过的红色提示（文档 2.0 第 3 条）。
 *
 * 原来这些提示常驻在页面顶部的白色框里，占了一大块版面；改成"出现一次、自动消失"之后，
 * 页面不再被常驻横幅占位，老师也不会因为一条已经读过的提示一直挂在那里而分心。
 *
 * 三条实现约束：
 *
 * 1. **一条就是一条**：同一条文案再次触发时要重新计时，所以调用方用 `key` 强制换一次挂载
 *    （见 `AuthoringPage` 的 toast 状态），这里不试图自己"去重"。
 * 2. **不挡住点击**：`pointer-events: none`，提示飘在内容上方但不吃鼠标事件。
 * 3. **对读屏可见**：`role="status"` + `aria-live="polite"`——它是一次真实的状态变化，
 *    不是纯装饰。
 */
export function Toast({
  message,
  onClose,
  timeoutMs = 2500,
}: {
  message: string | null;
  onClose: () => void;
  timeoutMs?: number;
}) {
  // 把回调放进 ref：父组件每次渲染都会传一个新函数，直接进依赖数组会让计时器永远重新开始。
  const closeRef = useRef(onClose);
  closeRef.current = onClose;

  useEffect(() => {
    if (message === null) return;
    const timer = window.setTimeout(() => closeRef.current(), timeoutMs);
    return () => window.clearTimeout(timer);
  }, [message, timeoutMs]);

  if (message === null) return null;
  return (
    <div className="toast" role="status" aria-live="polite" data-testid="toast">
      {message}
    </div>
  );
}
