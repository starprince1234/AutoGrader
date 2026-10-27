import {
  cloneElement,
  isValidElement,
  useEffect,
  useId,
  useRef,
  useState,
  type ReactElement,
  type ReactNode,
} from "react";
import { t } from "../i18n";
import { describeApiError, isApiError } from "../api/problem";
import { CONTRACT_VERSION } from "../app/contractVersion";
import { redactRecord } from "../logging/redaction";

/**
 * UI 原语（共享文件：修改前在 `docs/phases/active/` 登记）。
 * 只放跨泳道复用的展示件；业务语义留在各 feature 目录。
 */

/**
 * 页面级标题。每页恰好一个 `h1`：面板标题是 `h2`、卡片内标题是 `h3`，
 * 层级因此始终是 `h1 → h2 → h3`，而不是从 `h2` 开始（读屏用户失去页面标题）。
 * 首页的 hero 标题本身就是该页的 `h1`，不套这一层。
 */
export function PageTitle({ children }: { children: ReactNode }) {
  return <h1 className="page-title">{children}</h1>;
}

/**
 * 游标分页列表的「加载更多 / 已到末页」控件（题目、成绩、名单、课程下拉共用）。
 *
 * 只取第一页且不给出"还有更多"的信号，会让后面的行**静默消失**：教师看到 200 行会以为
 * 那就是全班。因此每个走游标分页的列表都必须渲染它——到末页时给出明确终点，而不是留白。
 */
export function LoadMoreActions({
  loadedCount,
  hasNextPage,
  isFetchingNextPage,
  onLoadMore,
  testId,
}: {
  loadedCount: number;
  hasNextPage: boolean;
  isFetchingNextPage: boolean;
  onLoadMore: () => void;
  /** 测试标识前缀：渲染为 `<testId>-load-more` / `<testId>-end`。 */
  testId: string;
}) {
  if (hasNextPage) {
    return (
      <button
        type="button"
        className="button button--ghost"
        data-testid={`${testId}-load-more`}
        disabled={isFetchingNextPage}
        onClick={onLoadMore}
      >
        {isFetchingNextPage ? t("common.loading") : t("common.loadMore")}
      </button>
    );
  }
  if (loadedCount === 0) return null;
  return (
    <span className="muted" data-testid={`${testId}-end`}>
      {t("common.endOfList")}
    </span>
  );
}

export function Spinner({ label }: { label?: string }) {
  return (
    <p className="muted" role="status">
      {label ?? t("common.loading")}
    </p>
  );
}

export function Banner({
  tone,
  children,
  testId,
}: {
  tone: "info" | "warn" | "error" | "success";
  children: ReactNode;
  testId?: string;
}) {
  return (
    <div className={`banner banner--${tone}`} role={tone === "error" ? "alert" : "note"} data-testid={testId}>
      {children}
    </div>
  );
}

export function Panel({
  title,
  actions,
  children,
  testId,
  titleLevel = 2,
  className,
}: {
  title?: ReactNode;
  actions?: ReactNode;
  children: ReactNode;
  testId?: string;
  /** 当这张卡片的标题**就是**页面标题时用 `1`（登录、作答页）；否则保持 `2`。 */
  titleLevel?: 1 | 2;
  /** 附加类名，用于"这一类面板看起来就该不一样"（例如模型建议的 advisory 外框）。 */
  className?: string;
}) {
  return (
    <section className={className ? `panel ${className}` : "panel"} data-testid={testId}>
      {(title || actions) && (
        <header className="panel__head">
          {title ? (
            titleLevel === 1 ? (
              <h1 className="panel__title">{title}</h1>
            ) : (
              <h2 className="panel__title">{title}</h2>
            )
          ) : (
            <span />
          )}
          {actions ? <div className="panel__actions">{actions}</div> : null}
        </header>
      )}
      {children}
    </section>
  );
}

export function StatusPill({
  tone,
  children,
  testId,
}: {
  tone: "ok" | "warn" | "bad" | "idle";
  children: ReactNode;
  testId?: string;
}) {
  return (
    <span className={`pill pill--${tone}`} data-testid={testId}>
      {children}
    </span>
  );
}

export function EmptyState({ children }: { children?: ReactNode }) {
  return <p className="muted">{children ?? t("common.empty")}</p>;
}

/**
 * 待复制的诊断对象：前端 `request_id`（与后端日志同源）+ ProblemDetails + 契约版本 + 页面路径。
 * 只用 `redactRecord` 处理它，**不手拼字符串**——服务端 `detail` 里可能带 PII。
 * `pathname` 不含查询串：查询串可能携带用户可控内容，不该被粘贴出去。
 */
function diagnosticsText(error: unknown): string {
  const payload: Record<string, unknown> = {
    contract_version: CONTRACT_VERSION,
    path: globalThis.location?.pathname ?? null,
    request_id: isApiError(error) ? error.requestId : null,
    problem: isApiError(error) ? error.problem : null,
    error: isApiError(error)
      ? null
      : error instanceof Error
        ? error.message
        : String(error),
  };
  return JSON.stringify(redactRecord(payload), null, 2);
}

/** 剪贴板不可用（无权限、非安全上下文、jsdom）时如实返回 `manual`，不假装复制成功。 */
async function copyToClipboard(text: string): Promise<"ok" | "manual"> {
  const clipboard = globalThis.navigator?.clipboard;
  if (!clipboard || typeof clipboard.writeText !== "function") return "manual";
  try {
    await clipboard.writeText(text);
    return "ok";
  } catch {
    return "manual";
  }
}

export function ErrorState({ error, testId }: { error: unknown; testId?: string }) {
  const requestId = isApiError(error) ? error.requestId : null;
  const [copyState, setCopyState] = useState<"idle" | "ok" | "manual">("idle");
  const diagnostics = diagnosticsText(error);

  return (
    <Banner tone="error" testId={testId ?? "error-state"}>
      <strong>{t("common.errorTitle")}</strong>
      <p>{describeApiError(error)}</p>
      {requestId ? <p className="muted">{t("common.requestId", { id: requestId })}</p> : null}
      <div className="inline">
        <button
          type="button"
          className="button button--ghost"
          data-testid="copy-diagnostics"
          onClick={() => {
            void copyToClipboard(diagnostics).then(setCopyState);
          }}
        >
          {t("common.copyDiagnostics")}
        </button>
        {copyState === "ok" ? (
          <span className="muted" data-testid="diagnostics-copied">
            {t("common.diagnosticsCopied")}
          </span>
        ) : null}
      </div>
      {copyState === "manual" ? (
        <div>
          <p className="muted">{t("common.diagnosticsManualCopy")}</p>
          <textarea
            className="diagnostics"
            data-testid="diagnostics-text"
            aria-label={t("common.copyDiagnostics")}
            readOnly
            rows={10}
            value={diagnostics}
          />
        </div>
      ) : null}
    </Banner>
  );
}

export type Column<T> = {
  key: string;
  header: ReactNode;
  render: (row: T, index: number) => ReactNode;
  align?: "left" | "right";
  /**
   * 列的排序状态（`aria-sort`）。**只有真正的排序键**才设置它；可排序但当前未排序的列用
   * `"none"`。页面若把行排过序却不声明，读屏用户无从知道"顺序是有意义的"。
   */
  sort?: "ascending" | "descending" | "none";
};

export function Table<T>({
  columns,
  rows,
  getRowKey,
  caption,
  label,
  testId,
}: {
  columns: Column<T>[];
  rows: T[];
  getRowKey: (row: T, index: number) => string;
  caption?: string;
  /** 表格的可访问名称（`aria-label`）：表头本身读不出"这是哪张表"。 */
  label?: string;
  testId?: string;
}) {
  return (
    <div className="table__wrap">
      <table className="table" aria-label={label} data-testid={testId}>
        {caption ? <caption className="muted">{caption}</caption> : null}
        <thead>
          <tr>
            {columns.map((column) => (
              <th
                key={column.key}
                scope="col"
                aria-sort={column.sort}
                className={column.align === "right" ? "is-right" : undefined}
              >
                {column.header}
              </th>
            ))}
          </tr>
        </thead>
        <tbody>
          {rows.map((row, index) => (
            <tr key={getRowKey(row, index)}>
              {columns.map((column) => (
                <td key={column.key} className={column.align === "right" ? "is-right" : undefined}>
                  {column.render(row, index)}
                </td>
              ))}
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  );
}

/**
 * 字段外壳：label 与控件显式关联，错误/提示通过 `aria-describedby` 挂到控件上，
 * 而不是只在视觉上贴近。这样读屏与键盘用户都能知道"这条错误属于哪个输入"。
 * 控件由调用方提供（input/select/textarea）；本组件只补充可访问性关系，不改写控件行为。
 */
export function Field({
  label,
  htmlFor,
  error,
  hint,
  children,
}: {
  label: ReactNode;
  htmlFor: string;
  error?: string | null;
  /** 常驻说明（例如"学号需与课程名单一致"）。只放真正需要一直可见的规则。 */
  hint?: ReactNode;
  children: ReactNode;
}) {
  const errorId = `${htmlFor}-error`;
  const hintId = `${htmlFor}-hint`;
  const describedBy = [hint ? hintId : null, error ? errorId : null]
    .filter((value): value is string => value !== null)
    .join(" ");

  const control = isValidElement(children)
    ? (() => {
        const props = children.props as Record<string, unknown>;
        const described = [props["aria-describedby"], describedBy]
          .filter((value) => typeof value === "string" && value !== "")
          .join(" ");
        const accessibility: Record<string, unknown> = {};
        if (error) accessibility["aria-invalid"] = true;
        if (described !== "") accessibility["aria-describedby"] = described;
        return cloneElement(children as ReactElement<Record<string, unknown>>, accessibility);
      })()
    : children;

  return (
    <div className="field">
      <label htmlFor={htmlFor}>{label}</label>
      {control}
      {hint ? (
        <p className="field__hint" id={hintId}>
          {hint}
        </p>
      ) : null}
      {error ? (
        <p className="field__error" id={errorId} role="alert">
          {error}
        </p>
      ) : null}
    </div>
  );
}

/**
 * 加载/错误/空态的统一外壳。避免每个页面各写一遍三态分支，
 * 也保证"失败不静默"（错误一定渲染出可读原因与 request_id）。
 */
export function AsyncSection<T>({
  isPending,
  error,
  data,
  isEmpty,
  children,
  onRetry,
}: {
  isPending: boolean;
  error: unknown;
  data: T | undefined;
  isEmpty?: (data: T) => boolean;
  children: (data: T) => ReactNode;
  onRetry?: () => void;
}) {
  if (isPending) return <Spinner />;
  if (error) {
    return (
      <div>
        <ErrorState error={error} />
        {onRetry ? (
          <button type="button" className="button" onClick={onRetry}>
            {t("common.retry")}
          </button>
        ) : null}
      </div>
    );
  }
  if (data === undefined) return <EmptyState />;
  if (isEmpty?.(data)) return <EmptyState />;
  return <>{children(data)}</>;
}

export type TabItem = { id: string; label: string; content: ReactNode };

/**
 * 选项卡（WAI-ARIA Tabs 模式）：`tablist` + `tab` + `tabpanel` 角色、`aria-selected`、
 * 方向键在选项卡之间移动、未选中的选项卡不进入 Tab 序列（roving tabindex）。
 * 用于"同一筛选条件下的两个视图"（成绩明细 / 谁还没交），避免为此新增顶级导航项。
 */
export function Tabs({
  label,
  tabs,
  activeId,
  onChange,
  testId,
}: {
  label: string;
  tabs: TabItem[];
  activeId: string;
  onChange: (id: string) => void;
  testId: string;
}) {
  const baseId = useId();
  const activeIndex = Math.max(
    0,
    tabs.findIndex((tab) => tab.id === activeId),
  );
  const active = tabs[activeIndex];

  const handleKeyDown = (event: React.KeyboardEvent<HTMLDivElement>) => {
    const delta = event.key === "ArrowRight" ? 1 : event.key === "ArrowLeft" ? -1 : 0;
    if (delta === 0 || tabs.length === 0) return;
    event.preventDefault();
    const next = tabs[(activeIndex + delta + tabs.length) % tabs.length];
    onChange(next.id);
    document.getElementById(`${baseId}-tab-${next.id}`)?.focus();
  };

  return (
    <>
      <div
        className="tabs"
        role="tablist"
        aria-label={label}
        data-testid={testId}
        onKeyDown={handleKeyDown}
      >
        {tabs.map((tab) => (
          <button
            key={tab.id}
            type="button"
            role="tab"
            id={`${baseId}-tab-${tab.id}`}
            aria-selected={tab.id === active.id}
            aria-controls={`${baseId}-panel-${tab.id}`}
            tabIndex={tab.id === active.id ? 0 : -1}
            className={`button ${tab.id === active.id ? "" : "button--ghost"}`}
            onClick={() => onChange(tab.id)}
          >
            {tab.label}
          </button>
        ))}
      </div>
      <div
        role="tabpanel"
        id={`${baseId}-panel-${active.id}`}
        aria-labelledby={`${baseId}-tab-${active.id}`}
        data-testid={`${testId}-panel`}
      >
        {active.content}
      </div>
    </>
  );
}

/** 可聚焦元素（用于把 Tab 关在弹窗内）。`tabindex="-1"` 的元素不在 Tab 序列里。 */
function focusableWithin(root: HTMLElement): HTMLElement[] {
  const selector =
    'a[href], button:not([disabled]), input:not([disabled]), select:not([disabled]), textarea:not([disabled]), [tabindex]:not([tabindex="-1"])';
  return Array.from(root.querySelectorAll<HTMLElement>(selector));
}

/**
 * 弹窗。可访问性最小集（WCAG 2.2）：
 * - 打开时焦点进入弹窗本体，关闭后**归还**给触发元素（否则键盘用户被丢回页面开头）；
 * - `Escape` 关闭（由 `keydown` 冒泡到弹窗外壳，焦点在弹窗内时必定命中）；
 * - `Tab` / `Shift+Tab` 在弹窗内循环，不会走到背景内容上。
 * 出口有两条（Escape 与关闭按钮），因此不存在"把用户困在弹窗里"的单点故障。
 */
export function Modal({
  title,
  onClose,
  children,
  testId,
}: {
  title: ReactNode;
  onClose: () => void;
  children: ReactNode;
  testId?: string;
}) {
  const dialogRef = useRef<HTMLDivElement>(null);
  const returnFocusRef = useRef<HTMLElement | null>(null);
  const titleId = useId();

  useEffect(() => {
    returnFocusRef.current =
      document.activeElement instanceof HTMLElement ? document.activeElement : null;
    dialogRef.current?.focus();
    return () => {
      returnFocusRef.current?.focus();
    };
  }, []);

  const handleKeyDown = (event: React.KeyboardEvent<HTMLDivElement>) => {
    if (event.key === "Escape") {
      event.stopPropagation();
      onClose();
      return;
    }
    if (event.key !== "Tab") return;
    const dialog = dialogRef.current;
    if (!dialog) return;
    const items = focusableWithin(dialog);
    if (items.length === 0) {
      // 没有可聚焦元素时不能让 Tab 跑出弹窗（否则焦点落到背景内容上）。
      event.preventDefault();
      return;
    }
    const first = items[0];
    const last = items[items.length - 1];
    const active = document.activeElement;
    if (event.shiftKey && (active === first || active === dialog)) {
      event.preventDefault();
      last.focus();
      return;
    }
    if (!event.shiftKey && active === last) {
      event.preventDefault();
      first.focus();
    }
  };

  return (
    <div className="modal__backdrop" role="presentation" onKeyDown={handleKeyDown}>
      <div
        className="modal"
        role="dialog"
        aria-modal="true"
        aria-labelledby={titleId}
        tabIndex={-1}
        ref={dialogRef}
        data-testid={testId}
      >
        <header className="modal__head">
          <h3 id={titleId}>{title}</h3>
          <button type="button" className="button button--ghost" onClick={onClose}>
            {t("common.close")}
          </button>
        </header>
        <div className="modal__body">{children}</div>
      </div>
    </div>
  );
}

/**
 * 抽屉（小屏导航）。与弹窗同一套可访问性最小集：
 * 打开时焦点进入抽屉、Escape 关闭、`Tab` 在抽屉内循环、关闭后焦点归还触发按钮；
 * 另外打开时锁定背景滚动，避免"内容还在下面滑"。
 *
 * 三种关闭方式都有真实行为：Escape、点击遮罩、抽屉里的导航项（由调用方在选页后关闭）。
 */
export function Drawer({
  title,
  onClose,
  children,
  testId,
  hideCloseButton = false,
}: {
  title: ReactNode;
  onClose: () => void;
  children: ReactNode;
  testId?: string;
  /** 抽屉内不再重复导航的关闭控件时置 true；Esc 与遮罩点击仍可关闭。 */
  hideCloseButton?: boolean;
}) {
  const panelRef = useRef<HTMLDivElement>(null);
  const returnFocusRef = useRef<HTMLElement | null>(null);
  const titleId = useId();

  useEffect(() => {
    returnFocusRef.current =
      document.activeElement instanceof HTMLElement ? document.activeElement : null;
    const previousOverflow = document.body.style.overflow;
    document.body.style.overflow = "hidden";
    panelRef.current?.focus();
    return () => {
      document.body.style.overflow = previousOverflow;
      returnFocusRef.current?.focus();
    };
  }, []);

  const handleKeyDown = (event: React.KeyboardEvent<HTMLDivElement>) => {
    if (event.key === "Escape") {
      event.stopPropagation();
      onClose();
      return;
    }
    if (event.key !== "Tab") return;
    const panel = panelRef.current;
    if (!panel) return;
    const items = focusableWithin(panel);
    if (items.length === 0) {
      event.preventDefault();
      return;
    }
    const first = items[0];
    const last = items[items.length - 1];
    const active = document.activeElement;
    if (event.shiftKey && (active === first || active === panel)) {
      event.preventDefault();
      last.focus();
      return;
    }
    if (!event.shiftKey && active === last) {
      event.preventDefault();
      first.focus();
    }
  };

  return (
    <div
      className="drawer__backdrop"
      role="presentation"
      data-testid={testId ? `${testId}-backdrop` : undefined}
      onClick={(event) => {
        if (event.target === event.currentTarget) onClose();
      }}
    >
      <div
        className="drawer"
        role="dialog"
        aria-modal="true"
        aria-labelledby={titleId}
        tabIndex={-1}
        ref={panelRef}
        data-testid={testId}
        onKeyDown={handleKeyDown}
      >
        <header className="drawer__head">
          <h2 className="drawer__title" id={titleId}>
            {title}
          </h2>
          {hideCloseButton ? null : (
            <button type="button" className="button button--ghost" onClick={onClose}>
              {t("nav.close")}
            </button>
          )}
        </header>
        <div className="drawer__body">{children}</div>
      </div>
    </div>
  );
}
