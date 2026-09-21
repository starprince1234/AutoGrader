# 事件：我发布什么 / 我订阅什么

## 1. 发布

**无。**

[module.yaml](../../module.yaml) 的 `events.publishes` 是空数组，运行期 `ModuleDescriptor.events`
也是空元组。这不是"暂未实现"，而是本模块的语义：报告是**被查询的**，不是**被广播的**。

理由：

- 没有下游：报告不参与任何业务流程，没有任何模块依赖它（无人 `requires: b5-conformance`）。
- 事件是持久化副作用：发事件意味着关心它的人没在场时也要送达，而"平台此刻是否一致"是一个
  时效性问题——昨天的报告没有意义，重放它更没有意义。想要当前答案的调用者应当查询端点。

## 2. 订阅

**无。**

- B5 不订阅任何 subject，因此没有去重键、没有重试策略、没有 DLQ、没有顺序假设需要声明。
- 空 `subscribes` 有一个真实后果：**B4 的注册表会如实报告 B5 不消费任何事件**，这与
  `descriptor.events == module.yaml.events.publishes ∪ subscribes` 的三方一致性校验一致
  （两侧都为空时，任何"多加一个 subject"的改动都会立刻使装载失败）。

## 3. 与 `MAIN/contracts/asyncapi/events.yaml` 的关系

`../../../MAIN/contracts/asyncapi/events.yaml` 定义了平台的 subject 与事件封套。
B5 在其中**没有任何 subject**：如果将来 B5 需要发布（例如"检测到不一致"告警），
必须先走 CHG 在 AsyncAPI 契约中登记 subject，再实现，并同步本文件与 `module.yaml`。

## 4. 幂等与顺序

不适用——没有事件流量。这也是为什么本文件比其它模块的同类文档短：写得更多就是在描述
不存在的行为（铁律二）。
