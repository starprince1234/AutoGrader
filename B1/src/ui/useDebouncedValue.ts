import { useEffect, useState } from "react";

/**
 * 把快速变化的输入压成"停手 delayMs 之后的值"。
 *
 * 搜索框要"边输入边刷新列表"，但每个按键都发一次请求既浪费、也会让列表闪。防抖后：
 * 停手 `delayMs` 再请求；值没变时不重设定时器。
 */
export function useDebouncedValue<T>(value: T, delayMs: number): T {
  const [debounced, setDebounced] = useState(value);
  useEffect(() => {
    if (value === debounced) return;
    const timer = setTimeout(() => setDebounced(value), delayMs);
    return () => clearTimeout(timer);
  }, [value, delayMs, debounced]);
  return debounced;
}
