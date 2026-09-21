# 题库（100 道已验证题目）

面向「操作系统 / Unix 作业」课程的题目集，用于把平台填成有内容可用的状态。
**每道题的参考答案都真实执行过，并且在题目自己声明的比较策略下与期望输出一致**——
没有这一步，导入的就只是看起来像题的文本。

| 文件 | 题数 | 语言 / adapter | 主题 |
|---|---|---|---|
| `01-text-tools.json` | 18 | python / `stdin` | Unix 文本与行处理语义（`wc`、`head`/`tail`、`uniq`、`cut`、`sort -k`、`join`、`comm` …） |
| `02-file-io.json` | 12 | python / **`file`** | 从工作区文件读取并计算（`RQ-B-3-50` 的文件读取框架，含 13 个 input fixture） |
| `03-process-scheduling.json` | 16 | python / `stdin` | 进程调度、页面置换、磁盘调度、死锁与内存分配 |
| `04-data-structures.json` | 18 | python / `stdin` | 系统向数据结构与算法（LRU/FIFO、位图与伙伴分配、inode 块映射、路径规范化 …） |
| `05-encoding-parsing.json` | 18 | python / `stdin` | 编码、解析与校验（进制、权限位、CRC-32、Adler-32、varint、CIDR …） |
| `06-shell.json` | 18 | **bash** / `stdin` | POSIX shell 脚本 |
| `00-selftest.json` | 4 | python + bash | 校验器自检夹具，**不导入**（其中一题与种子题重复） |

合计 **100 道导入题 + 4 道自检题**，377 个用例（每题 2–4 个：第 0 个可见并作为题面示例，其余隐藏）。

## 题面来源

题目是**为本项目原创编写**的：题面文字、输入输出格式与示例都是自己写的。选用的都是不受版权
保护的经典概念（"实现 `wc -l` 的语义"、"FIFO 页面置换有多少次缺页"），没有复制教材、OJ、
LeetCode、Project Euler 或 Rosetta Code 的题面文字。隐藏用例的输入与期望输出不会出现在题面里
（导入时由 `checks.py` 之外的过程校验，逐题机检过）。

## 一题从草稿到发布的完整路径

`CHG-0008` 之后，**发布需要一份参考解答验证存证**，且存证的摘要必须与 B3 重算的版本内容摘要相等。
这条门禁是**真的**：没有存证的版本发不出去（`422`），摘要不符会被拒（`409`）。

它带来一个必须讲清的后果：**验证是一个工具侧动作，不是 UI 里的一个按钮。**

原因不是偷懒，而是 `ADR-0009`/`CHG-0008` 的核心设计：**执行者与记录者必须是不同组件**。
B3 禁止执行任何东西（`B3/AGENTS.md`），所以它只**记录并强制**；真正跑参考答案的是 MAIN 的题库工具。
如果让客户端自己声明"我验证过了"，存证就退化成自我声明，门禁也就没有意义了。

因此实际流程是：

| 步骤 | 谁做 | 命令 / 端点 |
|---|---|---|
| 1. 建草稿、编辑题干/用例/标签/修订说明 | 教师（B1 出题界面，或直接调 API） | `POST /api/v1/questions?course_id=…`、`PATCH /api/v1/questions/{id}` |
| 2. **真的跑一遍参考答案** | MAIN 题库工具 | `scripts/verify_question_bank.py` |
| 3. 记录存证 | MAIN 导入器（读 **B3 的** `content_digest`） | `scripts/import_question_bank.py` 内部完成 |
| 4. 发布 | B3 | `POST /api/v1/question-versions/{id}/publish` |

**导入器把这三步合成一步**：`verify → attest → publish`。顺序不可颠倒 —— 参考答案与期望输出对不上的
题目**在发出任何 HTTP 请求之前**就被拒（实测：正常题目 4/4 通过；把参考答案换成别的 → 0/4 并在联网前
以 `422` 拒绝）。一道没人能通过的题，比一道缺失的题更糟。

**UI 侧的诚实表述**：B1 的出题界面**不提供发布按钮**，而是把草稿显示为"待验证后才能发布"并说明
原因。一个总是失败的按钮比没有按钮更糟。若日后要让教师自助发布，正确的做法是**增加一个平台侧
的验证编排**（由能执行代码的组件跑、由 B3 记录），而不是让前端自己声明。

## 两道门

```powershell
cd MAIN

# 1. 真实性门：真的跑一遍每份参考答案，验证期望输出
.\.venv\Scripts\python.exe scripts\verify_question_bank.py
#    == 01-text-tools.json: 18 problems
#      [PASS] 统计行数、词数与字节数  (4 cases)
#      ...
#    104/104 passed

# 2. 导入门：只走公开 HTTP API（不直接写库）
.\.venv\Scripts\python.exe scripts\import_question_bank.py `
    --course-id <course_uuid> --section-id <section_uuid>
#    --dry-run 预览；--only 01-text-tools 只导一份
```

导入按文件分组，每份生成一个 `assignment`（例如「进程调度与内存置换」16 题），
而不是把 100 道题塞进一个作业——后者在课程视图里没法用。

## 校验器为什么必须"照抄"平台

`verify_question_bank.py` 的比较逻辑与环境是 `B2` 判题机的**忠实移植**，不是近似：

- `normalize_trailing_whitespace` 逐行 `rstrip`（与 `B2/.../runner/comparator.py` 一致）。
  早先版本只对整串 `rstrip`，比权威更严，产生了幻影失败。
- 子进程环境镜像 `B2/.../runner/policy.py::SANDBOX_ENV_FIXED`
  （`LANG`/`LC_ALL=C.UTF-8`、`PYTHONIOENCODING=utf-8`）。这一条不是装饰：本机 Python 是
  GBK（cp936），`len("中文输入")` 在沙箱里是 **4**、在 GBK 下是 **7**——不镜像环境，
  含非 ASCII 的题目可能"本地通过、判题机上不一致"。

## 出题时的硬约束（踩过的坑）

1. **LF 规范输出。** Windows 文本模式会把 `\n` 写成 `\r\n`。参考答案用
   `sys.stdout.reconfigure(newline="\n")` 或 `sys.stdout.buffer.write(text.encode("utf-8"))`
   固定 LF，期望输出也存 LF——这样本机与 Linux 判题机产出同样的字节。
2. **非 ASCII 输入显式按 UTF-8 解码**（`sys.stdin.buffer.read().decode("utf-8")`），
   并在题面里写明，别依赖平台默认编码。
3. **shell 的 `args` 别放 `|`、`;`、`&`、`$`。** 本地校验经 `bash.exe`（WSL）调用，
   它的 Windows→Linux 互操作层会**重新解析**命令行；Linux 判题机直接传 argv，没有这个限制。
   这是本地校验工具的限制，不是平台的——但题库因此只用了惰性字符做参数。
4. **client 声明的 `content_sha256` 不被信任。** `file` 类题目在**创建时**就要在
   `adapter.files[]` 里声明摘要（那时还没有 version id 可上传），导入脚本自己算 sha256，
   服务端在 publish 时用自己算的摘要校验（`assert_fixture_binding`）；对不上就发布失败，
   而不是悄悄绑定错内容。
5. **确定性**：不用随机（除非固定种子并写进题面）、不把时间/主机名/路径写进输出、
   需要顺序的地方显式排序。集合与字典的迭代顺序若影响输出必须先排序。
