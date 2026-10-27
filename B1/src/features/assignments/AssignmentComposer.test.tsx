import { screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, describe, expect, it, vi } from "vitest";
import { renderWithRouter } from "../../testing/renderWithProviders";
import {
  installFetchStub,
  jsonResponse,
  problemResponse,
  type FetchStub,
  type StubbedRoute,
} from "../../testing/httpBoundary";
import {
  UUID_B,
  UUID_C,
  assignmentPublic,
  coursePage,
  courseSummary,
  questionPublic,
  sessionCreated,
} from "../../testing/contractFixtures";
import { signIn } from "../../auth/sessionStore";
import { AssignmentComposer } from "./AssignmentComposer";

/**
 * 新建作业抽屉（P04 的主操作）。
 *
 * 这里断言的是四个容易做错的点：
 * 1. 打开表单**不写库**（没有 POST）；
 * 2. 标题为空时**不发请求**，错误显示在表单上；
 * 3. 只列**已发布**版本（草稿不出现，因为引用草稿必然 409）；
 * 4. 只有二次确认后才 `POST` **一次**，且请求体带的是用户选中的版本 id。
 */

const PUBLISHED = "00000000-0000-4000-8000-000000000401";
const DRAFT = "00000000-0000-4000-8000-000000000402";
const QUESTION = "00000000-0000-4000-8000-000000000403";

let stub: FetchStub | null = null;

afterEach(() => {
  stub?.restore();
  stub = null;
  localStorage.clear();
});

function jwtFor(subject: string): string {
  const encode = (value: object) =>
    btoa(JSON.stringify(value)).replace(/\+/g, "-").replace(/\//g, "_").replace(/=+$/, "");
  return `${encode({ alg: "EdDSA" })}.${encode({ sub: subject })}.signature`;
}

function baseRoutes(overrides: { onPost?: () => Response } = {}): StubbedRoute[] {
  return [
    {
      method: "POST",
      path: "/api/v1/auth/session",
      respond: () =>
        jsonResponse(
          sessionCreated({
            roles: ["teacher"],
            scope: [{ course_id: UUID_C, section_id: UUID_B }],
            access_token: jwtFor(UUID_B),
          }),
        ),
    },
    {
      path: "/api/v1/courses",
      respond: () => jsonResponse(coursePage([courseSummary({ course_id: UUID_C })])),
    },
    {
      path: `/api/v1/courses/${UUID_C}/sections`,
      respond: () =>
        jsonResponse({
          items: [
            {
              section_id: UUID_B,
              course_id: UUID_C,
              section_code: "01",
              term: "2026F",
              created_at: null,
            },
          ],
        }),
    },
    {
      path: "/api/v1/questions",
      respond: () =>
        jsonResponse({
          items: [
            questionPublic({
              question_id: QUESTION,
              title: "进程与线程",
              versions: [
                {
                  question_version_id: PUBLISHED,
                  version: 1,
                  status: "published",
                  published_at: "2026-10-20T00:00:00Z",
                },
                {
                  question_version_id: DRAFT,
                  version: 2,
                  status: "draft",
                  published_at: null,
                },
              ],
            }),
          ],
          next_cursor: null,
        }),
    },
    {
      method: "POST",
      path: "/api/v1/assignments",
      respond:
        overrides.onPost ??
        (() =>
          jsonResponse(
            assignmentPublic({
              assignment_id: UUID_B,
              title: "第一次作业",
              question_version_ids: [PUBLISHED],
            }),
            201,
          )),
    },
  ];
}

function renderComposer() {
  const onCreated = vi.fn();
  const onClose = vi.fn();
  renderWithRouter(
    <AssignmentComposer onClose={onClose} onCreated={onCreated} />,
    { path: "/assignments", linkTargets: ["/assignments/$assignmentId"] },
  );
  return { onCreated, onClose };
}

function postCount(target: FetchStub | null): number {
  return (target?.requests ?? []).filter(
    (request) => request.method === "POST" && request.url.pathname === "/api/v1/assignments",
  ).length;
}

describe("新建作业（P04）", () => {
  it("打开表单不写库，且只列出已发布版本", async () => {
    stub = installFetchStub(baseRoutes());
    await signIn("teacher1", "pw");
    renderComposer();

    const list = await screen.findByTestId("compose-version-list");
    await waitFor(() => expect(within(list).getAllByRole("checkbox")).toHaveLength(1));
    // 草稿版本不出现：引用它必然 409，让用户选一个注定失败的选项是设计缺陷。
    expect(within(list).queryByText(/v2/)).toBeNull();
    expect(within(list).getByText(/进程与线程/)).toBeInTheDocument();
    expect(postCount(stub)).toBe(0);
  });

  it("标题为空时不发请求，并在表单上给出错误", async () => {
    stub = installFetchStub(baseRoutes());
    await signIn("teacher1", "pw");
    renderComposer();
    const user = userEvent.setup();

    await screen.findByTestId("compose-version-list");
    await user.selectOptions(screen.getByLabelText("课序（可选）"), UUID_B);
    await user.click(screen.getByRole("checkbox"));
    await user.click(screen.getByTestId("assignment-compose-preview"));

    expect(await screen.findByTestId("assignment-compose-error")).toHaveTextContent(
      "请输入作业名称。",
    );
    expect(postCount(stub)).toBe(0);
  });

  it("预览后必须二次确认才 POST 一次，请求体带选中的版本 id", async () => {
    stub = installFetchStub(baseRoutes());
    await signIn("teacher1", "pw");
    const { onCreated } = renderComposer();
    const user = userEvent.setup();

    await screen.findByTestId("compose-version-list");
    await user.type(screen.getByLabelText("作业名称"), "第一次作业");
    await user.selectOptions(screen.getByLabelText("课序（可选）"), UUID_B);
    await user.click(screen.getByRole("checkbox"));

    // 第一次点击只是打开预览：仍无写入。
    await user.click(screen.getByTestId("assignment-compose-preview"));
    const confirm = await screen.findByTestId("assignment-compose-confirm");
    expect(within(confirm).getByText("发布后学生可以开始作答。")).toBeInTheDocument();
    expect(postCount(stub)).toBe(0);

    await user.click(screen.getByTestId("assignment-compose-confirm-button"));
    await waitFor(() => expect(postCount(stub)).toBe(1));

    const request = (stub?.requests ?? []).find(
      (candidate) =>
        candidate.method === "POST" && candidate.url.pathname === "/api/v1/assignments",
    );
    expect(request?.body).toMatchObject({
      course_id: UUID_C,
      section_id: UUID_B,
      title: "第一次作业",
      question_version_ids: [PUBLISHED],
    });
    await waitFor(() => expect(onCreated).toHaveBeenCalledWith(UUID_B));
  });

  it("服务端 409 时保留表单并提示重新选择，不重复提交", async () => {
    stub = installFetchStub(
      baseRoutes({
        onPost: () =>
          problemResponse({
            status: 409,
            title: "Conflict",
            detail: "an assignment may only reference published versions",
            instance: "/api/v1/assignments",
          }),
      }),
    );
    await signIn("teacher1", "pw");
    const { onCreated } = renderComposer();
    const user = userEvent.setup();

    await screen.findByTestId("compose-version-list");
    await user.type(screen.getByLabelText("作业名称"), "第一次作业");
    await user.selectOptions(screen.getByLabelText("课序（可选）"), UUID_B);
    await user.click(screen.getByRole("checkbox"));
    await user.click(screen.getByTestId("assignment-compose-preview"));
    await user.click(screen.getByTestId("assignment-compose-confirm-button"));

    expect(await screen.findByTestId("assignment-compose-error")).toHaveTextContent(
      "所选题目版本已变化",
    );
    expect(postCount(stub)).toBe(1);
    expect(onCreated).not.toHaveBeenCalled();
    // 表单仍在：标题与所选版本都保留，用户不需要重新填。
    expect(screen.getByLabelText("作业名称")).toHaveValue("第一次作业");
    expect(screen.getByRole("checkbox")).toBeChecked();
  });
});
