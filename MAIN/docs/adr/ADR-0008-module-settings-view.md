# ADR-0008 单进程多模块的配置视图：`<MODULE>_` 前缀覆盖同名通用变量

- **状态**：接受
- **影响范围**：MAIN host、B2、B3、B4
- **关联需求**：`RQ-B-0-20`（运行环境）、`RQ-B-4-10`（最小权限数据库角色）、`RQ-B-4-30`（表结构/所有权）
- **关联决策**：[ADR-0001](ADR-0001-plugin-composition-and-schema-ownership.md)（schema 所有权与最小权限）、[ADR-0003](ADR-0003-api-authority-and-runtime-registry.md)（运行期注册表）

> 本 ADR 是把**已经在代码中生效的约定**补成决策记录。此前它只写在
> `host/src/autograder_host/main.py` 的 docstring 里，因此 `main.py`、`B3/plugin.py`、
> `B4/plugin.py` 三处代码注释与一份阶段文档都引用了一个**不存在的 ADR-0008** ——
> 悬空引用本身是缺陷（见 `../phases/active/phase-01-integration-runtime.md` 的遗留项）。

---

## 背景

composition host 是**一个进程**装载 B2/B3/B4（ADR-0001 的微内核形态）。三个模块各自用
`Settings.from_environment()` 读**同一批通用变量名**：`DATABASE_URL`、`NATS_URL`、
`JWT_PRIVATE_KEY_FILE` …。于是产生一个必须回答的问题：**一个进程里，三个模块怎么拿到各自的
最小权限连接身份？**

被评估的方案：

| 方案 | 做法 | 结论与原因 |
|---|---|---|
| A. 全体读全局环境 | 三个模块都读 `DATABASE_URL` | **否**。第一次集成启动就是这样：全局 `DATABASE_URL` 是**平台超级用户**（迁移用），于是三个模块全部以超级用户连接。B4 的 `_reject_superuser` 正确地拒绝启动，而 B2/B3 即使启动也丧失了"每个模块一个最小权限角色"这条结构性保证（ADR-0001） |
| B. 每模块一个 `.env` 文件 | 由模块自己加载 `.env` | **否**。host 是单进程，"每模块一个文件"意味着配置文件位置成为装载前提，把配置推回到文件系统；而部署机制（容器/编排）注入的是环境变量，会造成两套配置来源 |
| C. **前缀视图**：`<MODULE>_X` 在该模块自己的设置视图里作为 `X` 暴露，且**前缀值优先** | host 为每个模块构造一份视图 | **是**（本 ADR 的决策） |
| C'. 前缀值仅"填补空缺"（`setdefault` 语义） | 同上但前缀不覆盖已有键 | **否**，且**已实际发生过**：`setdefault` 让全局 `DATABASE_URL`（超级用户）继续可见，B4 再次拒绝启动。守卫是对的，解析是错的。这是本 ADR 必须写明"覆盖语义"而非"填补语义"的原因 |
| D. 结构化配置对象 | host 构造 dataclass/dict 交给模块 | **否**。模块的 `Settings.from_environment` 已经成型；引入第二套配置系统意味着 host 必须知道每个模块的配置 schema —— 耦合方向反了（host 不应该知道业务模块有哪些配置项） |
| E. 由 host 按模块重命名全部变量 | host 读取模块清单后注入 | **否**。等于把"模块需要什么配置"编码进 host，新增模块要改 MAIN；且与"即插即用"目标冲突 |

---

## 决策

1. host 为每个模块构造一份**设置视图**：以进程环境为基底，把该模块的 `<MODULE>_` 前缀变量
   **去掉前缀后覆盖**同名通用变量。`B2_DATABASE_URL` 只在 B2 的视图里成为 `DATABASE_URL`；
   B3、B4 各自看到自己的。
2. **前缀值必须覆盖，不能仅填补空缺**（否决方案 C'）。没有前缀的模块仍回落到通用变量，
   因此单模块部署继续可用。
3. 视图通过 `ModuleContext.settings` 传给模块；模块**应当**优先读它，而不是直接读 `os.environ`。
4. 一个**空**视图表示"没有宿主"（例如模块在宿主之外被单独构造），此时模块回落到 `os.environ`；
   反过来，宿主给的视图**叠加**在进程环境之上，而不是整体替换它。
   > 这条是实测踩出来的：早期实现把空视图当成"环境为空"，于是 `Settings.from_environment({})`
   > 对一个完全有效的环境报 `DATABASE_URL is required`。
5. 命名口径：模块**不得**为同一个语义自造别名（例如 B3 读 `B4_JWKS_URL` 是因为 JWKS 由 B4 提供，
   变量名跟着**提供方**；而自造 `B3_JWKS_URL` 会让校验器静默不装配，表现为已认证路由返回 500）。

---

## 后果

**获得**

- 单进程内保持**每模块一个最小权限数据库角色**（ADR-0001 的结构性保证不因"共用一个进程"而失效）。
- 部署只需一套环境变量；不需要第二套配置文件或配置系统。
- 新增模块**不需要修改 MAIN 代码**：模块自己声明 `module.yaml`，运维按 `<MODULE>_` 前缀提供变量即可。
- 前缀规则是**单向**的（只在该模块自己的视图内生效），因此一个模块的配置不可能泄漏给另一个模块。

**付出**

- 配置调试多一层规则：看到 `DATABASE_URL` 时要先知道"这是谁在什么前缀下看到的"。
  运行手册必须写明这一点，否则会重复 C' 那次排查。
- 前缀是**字符串约定**，拼错前缀不会报错，只会静默回落到通用值 —— 这与"缺失能力必须响亮失败"
  的设计取向不一致（见遗留风险）。

**遗留风险**

- **拼写漂移无门禁**：`B2_B3_BASE_URL` 之类的前缀写错时，模块会拿到通用值或空值。
  当前没有任何门禁检查"模块声明的配置项都提供了值"。缓解：`.env.example` 与运行手册记录完整清单；
  `check_local_configuration_consistency()` 校验端口一致性但不校验前缀完整性。
- **`JWT_PRIVATE_KEY_FILE` 这类既非模块专属、又必须存在的变量**走的是通用名（B4 用），
  没有前缀；属于"平台级凭据"，本 ADR 未覆盖其轮换语义。
- **凭据可能出现在 `repr`**：模块需要自行 `repr=False`（B2 的 `Settings` 已这样做）。这不是本 ADR
  能强制的，属安全门禁范围。

---

## 关联

- 实现：`host/src/autograder_host/main.py`（`module_settings()`、`HostContext`）。
- 消费方：`B2/config.py`、`B3/plugin.py::_settings_from`、`B4/plugin.py::_context_environ`。
- 运行配置：`MAIN/.env.integration`（本地）、`MAIN/compose.yaml`（容器路径，见
  `../phases/active/phase-01-integration-runtime.md` 中关于容器路径未打通的记录）。
- [ADR-0001](ADR-0001-plugin-composition-and-schema-ownership.md)：本 ADR 是"最小权限"在
  **单进程装载**这一形态下的必要条件。
