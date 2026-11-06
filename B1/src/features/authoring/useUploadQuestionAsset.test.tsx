import { afterEach, describe, expect, it, vi } from "vitest";
import { render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { installFetchStub, jsonResponse, type FetchStub } from "../../testing/httpBoundary";
import { UUID_A, sessionCreated } from "../../testing/contractFixtures";
import { signIn } from "../../auth/sessionStore";
import { useUploadQuestionAsset } from "./useAuthoring";

let stub: FetchStub | null = null;

afterEach(() => {
  stub?.restore();
  stub = null;
});

/**
 * 上传题干配图之后，题库列表必须重取。
 *
 * 列表的「图片」列读的是 `stem_assets`：老师传完图回到列表却看到"无"，原因不只是服务端没把
 * 草稿的图算进去（B3 已修），前端还必须在**上传成功后**把 `["questions"]` 标脏——否则
 * 那份列表缓存仍然是上传前的 DTO。这里断言的就是这个失效动作。
 */
describe("useUploadQuestionAsset", () => {
  it("上传成功后把题库列表与题目详情标脏（回到列表即为最新）", async () => {
    stub = installFetchStub([
      {
        method: "POST",
        path: "/api/v1/auth/session",
        respond: () => jsonResponse(sessionCreated({ roles: ["teacher"] })),
      },
      {
        method: "POST",
        path: `/api/v1/questions/${UUID_A}/assets`,
        respond: () =>
          jsonResponse({
            asset_id: "00000000-0000-4000-8000-0000000000c1",
            content_type: "image/png",
            size_bytes: 724,
            content_sha256: "a".repeat(64),
          }),
      },
    ]);
    await signIn("teacher1", "pw");

    const client = new QueryClient({
      defaultOptions: { queries: { retry: false }, mutations: { retry: false } },
    });
    // 直接看失效动作：没有挂载的列表订阅时，缓存本身会被回收，断言 queryState 不可靠。
    const invalidate = vi.spyOn(client, "invalidateQueries");

    function Probe() {
      const upload = useUploadQuestionAsset(UUID_A);
      return (
        <button
          type="button"
          onClick={() => {
            void upload.mutateAsync({
              content_type: "image/png",
              content_base64: "aGVsbG8=",
            });
          }}
        >
          上传
        </button>
      );
    }

    const user = userEvent.setup();
    render(
      <QueryClientProvider client={client}>
        <Probe />
      </QueryClientProvider>,
    );

    await user.click(screen.getByRole("button", { name: "上传" }));

    await waitFor(() => expect(invalidate).toHaveBeenCalled());
    const keys = invalidate.mock.calls.map((call) => call[0]?.queryKey);
    expect(keys).toContainEqual(["questions"]);
    expect(keys).toContainEqual(["question", UUID_A]);
  });
});
