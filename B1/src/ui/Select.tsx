import { Children, isValidElement, useEffect, useId, useMemo, useRef, useState } from "react";
import type { ReactNode, SelectHTMLAttributes } from "react";

/**
 * 下拉框：**原生 select 仍然是真的控件**，只是在它上面盖了一层自绘外观。
 *
 * 为什么这么做（而不是"自己写一个 listbox"）：
 * 1. 键盘、读屏、表单语义、`label for`、浏览器自动填充——全部继续由原生控件负责，
 *    这层皮只处理鼠标点开的样子；
 * 2. 原生 select 展开后的列表是操作系统画的，圆角和动画都改不了；
 *    所以鼠标按下时 `preventDefault()` 拦住系统菜单，改开我们自己渲染的菜单；
 * 3. 测试里 40 处 `userEvent.selectOptions(getByLabelText(...))` 一行都不用改——
 *    原生 select 还在原来的位置、还带 `id`，只是 `opacity: 0` 不可见。
 *
 * 无障碍：自绘的触发器与菜单是 `aria-hidden` 的装饰层；键盘用户 Tab 进来焦点落在
 * 原生 select 上，空格/回车仍然弹系统菜单——功能完全不受影响。要连键盘一起自绘，
 * 需要同时重做方向键、typeahead、读屏播报，那是另一轮的事。
 */
type SelectProps = SelectHTMLAttributes<HTMLSelectElement> & {
  /** 允许透传 data-*（少数调用点用 data-testid 定位）。 */
  [key: `data-${string}`]: string | number | undefined;
};

type OptionItem = { value: string; label: string; disabled: boolean };

function textOf(node: ReactNode): string {
  if (node === null || node === undefined || node === false || node === true) return "";
  if (typeof node === "string" || typeof node === "number") return String(node);
  if (Array.isArray(node)) return node.map(textOf).join("");
  if (isValidElement(node)) return textOf((node.props as { children?: ReactNode }).children);
  return "";
}

/** 只认 option 子节点；全仓没有 optgroup、也没有多选，这里不需要支持。 */
function collectOptions(children: ReactNode): OptionItem[] {
  const options: OptionItem[] = [];
  for (const child of Children.toArray(children)) {
    if (!isValidElement(child) || child.type !== "option") continue;
    const props = child.props as {
      value?: string | number;
      disabled?: boolean;
      children?: ReactNode;
    };
    const label = textOf(props.children).trim();
    options.push({
      value: props.value === undefined ? label : String(props.value),
      label,
      disabled: Boolean(props.disabled),
    });
  }
  return options;
}

/**
 * 让受控的 select 触发一次 React 的 change：
 * 直接派发原生 `change` 事件，React 的 `onChange` 会照常收到，
 * 调用点里 `(event) => setX(event.target.value)` 的写法完全不用改。
 */
function commitValue(select: HTMLSelectElement, next: string): void {
  const setter = Object.getOwnPropertyDescriptor(HTMLSelectElement.prototype, "value")?.set;
  setter?.call(select, next);
  select.dispatchEvent(new Event("change", { bubbles: true }));
}

export function Select({ children, className, onMouseDown, onKeyDown, ...rest }: SelectProps) {
  const selectRef = useRef<HTMLSelectElement>(null);
  const wrapRef = useRef<HTMLSpanElement>(null);
  const [open, setOpen] = useState(false);
  const menuId = useId();

  const options = useMemo(() => collectOptions(children), [children]);
  const value = rest.value === undefined ? "" : String(rest.value);
  const current = options.find((option) => option.value === value) ?? options[0];

  // 点外面关掉：用捕获阶段，避免被菜单内的 stopPropagation 影响。
  useEffect(() => {
    if (!open) return undefined;
    const onPointerDown = (event: PointerEvent) => {
      if (!wrapRef.current?.contains(event.target as Node)) setOpen(false);
    };
    document.addEventListener("pointerdown", onPointerDown, true);
    return () => document.removeEventListener("pointerdown", onPointerDown, true);
  }, [open]);

  const choose = (next: string) => {
    const select = selectRef.current;
    if (!select) return;
    commitValue(select, next);
    setOpen(false);
    select.focus();
  };

  return (
    <span className="combo" data-open={open ? "true" : "false"} ref={wrapRef}>
      <span className="combo__trigger" aria-hidden="true">
        <span className="combo__value">{current?.label ?? ""}</span>
        <span className="combo__chevron" />
      </span>

      <select
        {...rest}
        ref={selectRef}
        className={className ? `combo__native ${className}` : "combo__native"}
        onMouseDown={(event) => {
          onMouseDown?.(event);
          // 拦住系统下拉菜单，改用自绘的那个；键盘行为不受影响。
          event.preventDefault();
          if (rest.disabled) return;
          setOpen((previous) => !previous);
        }}
        onKeyDown={(event) => {
          onKeyDown?.(event);
          if (event.key === "Escape" && open) {
            event.preventDefault();
            setOpen(false);
          }
        }}
        onBlur={() => {
          // Tab 走开时收起；点菜单项不会走到这里（那里用 mousedown 拦截了默认行为）。
          setOpen(false);
        }}
      >
        {children}
      </select>

      {open ? (
        <ul className="combo__menu" id={menuId} role="listbox" aria-hidden="true">
          {options.map((option) => (
            <li key={option.value}>
              <button
                type="button"
                tabIndex={-1}
                disabled={option.disabled}
                className={option.value === value ? "combo__option is-selected" : "combo__option"}
                // 让焦点留在原生 select 上：否则 select 先失焦会把菜单关掉，点击落空。
                onMouseDown={(event) => event.preventDefault()}
                onClick={() => choose(option.value)}
              >
                {option.label}
              </button>
            </li>
          ))}
        </ul>
      ) : null}
    </span>
  );
}
