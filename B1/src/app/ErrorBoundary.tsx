import { Component, type ErrorInfo, type ReactNode } from "react";
import { log, newRequestId } from "../logging/logger";
import { t } from "../i18n";

/**
 * 渲染异常边界（RQ-B-1-99 验收 2）：未捕获异常上报且**带 request_id**，
 * 便于与后端日志关联。异常不会把整个应用打白屏，也不会被吞掉。
 */
export class ErrorBoundary extends Component<
  { children: ReactNode },
  { error: Error | null; requestId: string | null }
> {
  state: { error: Error | null; requestId: string | null } = {
    error: null,
    requestId: null,
  };

  static getDerivedStateFromError(error: Error) {
    return { error, requestId: newRequestId() };
  }

  componentDidCatch(error: Error, info: ErrorInfo): void {
    log({
      event: "render_error",
      level: "error",
      request_id: this.state.requestId,
      message: error.message,
      component_stack: info.componentStack ?? null,
    });
  }

  render() {
    if (this.state.error) {
      return (
        <section className="panel" role="alert" data-testid="render-error">
          <h2>{t("common.errorTitle")}</h2>
          <p>{this.state.error.message}</p>
          {this.state.requestId ? (
            <p className="muted">{t("common.requestId", { id: this.state.requestId })}</p>
          ) : null}
        </section>
      );
    }
    return this.props.children;
  }
}
