import { StrictMode } from "react";
import { createRoot } from "react-dom/client";
import "./styles.css";
import { Root } from "./app/Root";
import { installSessionAuth, restoreSession } from "./auth/sessionStore";
import { installLogSinkFromEnv, log, newRequestId } from "./logging/logger";
import { t } from "./i18n";

/**
 * 应用启动：
 * 1. 把会话接进 HTTP 客户端（401 刷新一次后重放）；
 * 2. 安装可选日志上报通道；
 * 3. 尝试用 HttpOnly 刷新凭据恢复会话（拿不到角色就按最小权限显示）；
 * 4. 注册全局异常上报（含 request_id）。
 */

installSessionAuth();
installLogSinkFromEnv();

const bootRequestId = newRequestId();
void restoreSession().then((session) => {
  log({
    event: "app_bootstrapped",
    request_id: bootRequestId,
    session_status: session.status,
    roles_known: session.rolesKnown,
  });
});

window.addEventListener("error", (event) => {
  log({
    event: "render_error",
    level: "error",
    request_id: newRequestId(),
    message: event.message,
    source: "window.onerror",
  });
});

window.addEventListener("unhandledrejection", (event) => {
  log({
    event: "render_error",
    level: "error",
    request_id: newRequestId(),
    message: event.reason instanceof Error ? event.reason.message : "unhandled rejection",
    source: "unhandledrejection",
  });
});

const container = document.getElementById("root");
if (!container) {
  throw new Error(t("common.unknownError"));
}
createRoot(container).render(
  <StrictMode>
    <Root />
  </StrictMode>,
);
