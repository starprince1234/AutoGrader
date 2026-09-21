/**
 * 公开题目 DTO 的**双重校验**（RQ-B-1-40 验收 4）。
 *
 * 契约层已经保证公开 DTO 不含受保护内容（`b3.yaml` 的 `QuestionPublic` 是
 * `additionalProperties: false` 且没有这些字段）。B1 在渲染前再做一次运行时扫描：
 * 一旦响应里出现参考答案/隐藏用例/期望输出，**拒绝渲染**并大声失败——显示一个
 * 安静的"看起来正常"的页面会让契约违规变得不可见。
 */

const FORBIDDEN_KEYS = [
  "reference_answer",
  "hidden_cases",
  "hidden_tests",
  "expected_output",
  "expected_stdout",
  "expected_stderr",
  "cases",
];

export function findProtectedFields(value: unknown, path = "$"): string[] {
  const found: string[] = [];
  if (Array.isArray(value)) {
    value.forEach((item, index) => {
      found.push(...findProtectedFields(item, `${path}[${index}]`));
    });
    return found;
  }
  if (typeof value !== "object" || value === null) return found;

  for (const [key, item] of Object.entries(value as Record<string, unknown>)) {
    if (FORBIDDEN_KEYS.includes(key)) {
      found.push(`${path}.${key}`);
      continue;
    }
    found.push(...findProtectedFields(item, `${path}.${key}`));
  }
  return found;
}

export class ProtectedContentError extends Error {
  readonly paths: string[];

  constructor(paths: string[]) {
    super(
      `公开题目 DTO 中出现了受保护字段（${paths.join(", ")}）：契约要求公开 DTO 不含参考答案/隐藏用例/期望输出，B1 拒绝渲染。`,
    );
    this.name = "ProtectedContentError";
    this.paths = paths;
  }
}

export function assertNoProtectedFields(value: unknown): void {
  const paths = findProtectedFields(value);
  if (paths.length > 0) throw new ProtectedContentError(paths);
}
