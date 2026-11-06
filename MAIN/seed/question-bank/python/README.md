# Python 题库（课程「Python」/ 05）

三组、共 30 道命令行输入型 Python 题，难度统一为中等，每题 **5 个测试用例**（第 1 个学生可见的样例，
其余 4 个隐藏强测）。文件是 `MAIN/scripts/import_python_bank.py` 的输入格式。

| 文件 | 题数 | 状态 | 题干图片 |
|---|---|---|---|
| `01-draft-with-image.json` | 10 | 草稿 | 有（系统识别的图片标记） |
| `02-draft-plain.json` | 10 | 草稿 | 无 |
| `03-published.json` | 10 | 已发布（导入时先检验再发布） | 无 |

## 与既有题库文件格式的差别

沿用 `MAIN/seed/question-bank/*.json` 的数组格式，每题额外带三个字段：

- `difficulty`：平台没有"难度"列，所以难度同时以标签 `difficulty-medium` 表达（`difficulty`
  字段留给导入脚本与报告使用）。
- `publish`：`false` = 只建草稿；`true` = 检验通过后发布。旧脚本 `import_question_bank.py`
  会发布它看到的每一道题，因此草稿组用本目录配套的脚本导入。
- `image_placeholder`：为 `true` 时，题干里的图片标记是

  ```
  ![题干配图占位](/api/v1/questions/{question_id}/assets/{asset_id})
  ```

  这正是前端 `assetMarkdown()` 生成、`SafeMarkdown` 认可的形式；两个 id 在题目创建后才存在，
  所以导入脚本会先上传一张占位 PNG（640×200 灰底），再把真实 id 代进题干并 PATCH。

## 导入

```bash
BANK_TEACHER_USERNAME=… BANK_TEACHER_PASSWORD=… \
python MAIN/scripts/import_python_bank.py \
    --course-id 5b6466c0-347d-4867-9ea0-47d39341d121 \
    --base-url https://api.91idc.me
```

脚本对每道题调用 `POST /api/v1/reference-verifications`（在沙箱里真跑参考答案），
要求 `cases_passed == cases_total` 且已记录存证；`publish: true` 的题目随后发布。
任何一道不通过都会以非零退出码结束并列出失败项——**不通过就不入库**。

## 用例是怎么来的

`expected_stdout` 不是手写的：生成题库时用参考解逐条跑出输出并落库（见提交说明），
因此"参考答案能通过自己的用例"是构造出来的性质，不是事后核对出来的一句话。
