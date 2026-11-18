import { render, screen } from "@testing-library/react";
import { describe, expect, it } from "vitest";
import { IoSpecPanel, IoTypeBadge } from "./IoTypeBadge";

describe("IoTypeBadge & IoSpecPanel", () => {
  it("renders Chinese labels for all three io_type values", () => {
    const { rerender } = render(<IoTypeBadge ioType="stdin" testId="b" />);
    expect(screen.getByTestId("b")).toHaveTextContent("命令行输入");

    rerender(<IoTypeBadge ioType="file" testId="b" />);
    expect(screen.getByTestId("b")).toHaveTextContent("文件读取");

    rerender(<IoTypeBadge ioType="callable" testId="b" />);
    expect(screen.getByTestId("b")).toHaveTextContent("接口调用");
  });

  it("renders stdin, file (with visible attachments), and callable (python/bash) specs", () => {
    const { rerender } = render(
      <IoSpecPanel ioType="stdin" language="python" testId="spec" />,
    );
    expect(screen.getByTestId("spec")).toHaveTextContent("python main.py");

    rerender(
      <IoSpecPanel
        ioType="file"
        language="bash"
        questionVersionId="00000000-0000-4000-8000-000000000001"
        ioSpec={{ expected_output_paths: ["out.txt"] }}
        visibleFixtures={[
          {
            fixture_id: "00000000-0000-4000-8000-0000000000f1",
            path: "sample.csv",
            content_sha256: "a".repeat(64),
            size_bytes: 128,
          },
        ]}
        testId="spec"
      />,
    );
    expect(screen.getByTestId("spec")).toHaveTextContent("out.txt");
    expect(screen.getByTestId("spec-attachments")).toHaveTextContent("sample.csv");
    expect(
      screen.getByTestId("download-fixture-00000000-0000-4000-8000-0000000000f1"),
    ).toBeInTheDocument();

    rerender(
      <IoSpecPanel
        ioType="callable"
        language="bash"
        ioSpec={{ callable_module: "solution", callable_attribute: "solve" }}
        testId="spec"
      />,
    );
    expect(screen.getByTestId("spec")).toHaveTextContent("solution.sh");
    expect(screen.getByTestId("spec")).toHaveTextContent("source ./solution.sh");
    expect(screen.getByTestId("spec")).toHaveTextContent("solve");
  });
});
