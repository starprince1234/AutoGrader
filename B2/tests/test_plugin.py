"""插件声明（descriptor）与 `module.yaml` 的一致性。

`module.yaml` 是**声明**、`descriptor` 是**运行时事实**：registry 在宿主启动时比较两侧的事件集合
（`publishes ∪ subscribes`）并要求相等，因此"文档里写一致"不算数，这里把它钉成测试。
"""

from __future__ import annotations

import pathlib

import yaml

from autograder_b2.plugin import create_module

B2_ROOT = pathlib.Path(__file__).resolve().parents[1]


def test_descriptor_is_contract_bound():
    """`descriptor.contract_version` 必须等于 `module.yaml` 的 `contract_version`。

    **版本号不写死在断言里**（2026-09-18 更正）：它由 MAIN 的契约流程推进（本文件曾钉死 `0.1.0`，
    MAIN 升级契约并同步重写 `module.yaml` 与本模块 descriptor 后，断言立刻变成假失败）。真正要钉的是
    registry 在宿主启动时比较的那条不变量：**运行时事实 == 声明**，否则宿主拒绝启动。
    """
    manifest = yaml.safe_load((B2_ROOT / "module.yaml").read_text(encoding="utf-8"))
    descriptor = create_module().descriptor
    assert descriptor.module == "B2"
    assert descriptor.contract_version == manifest["contract_version"]


def test_descriptor_events_equal_module_yaml_publishes_and_subscribes():
    """T-EVT-019：`descriptor.events` == `module.yaml` 的 publishes ∪ subscribes（数量与集合都相等）。

    SDK 的 `ModuleDescriptor.events` 是扁平元组、不区分方向，所以 6 个 subject（5 publishes + 1
    subscribes）必须全部并进来：漏掉任何一个，registry 都会判 "descriptor.events omits ... declared
    in module.yaml" 并中止宿主启动。骨架期只列了 2 个事件，`docs/api/events.md` §4 因此长期记着
    "不一致（阻塞）"——把口径钉成测试，避免声明再次静默漂移。
    """
    declared_manifest = yaml.safe_load((B2_ROOT / "module.yaml").read_text(encoding="utf-8"))
    publishes = declared_manifest["events"]["publishes"]
    subscribes = declared_manifest["events"]["subscribes"]
    declared = set(publishes) | set(subscribes)
    assert len(declared) == len(publishes) + len(subscribes) == 6, "两个方向不得重叠、数量应为 5 + 1"

    descriptor_events = tuple(create_module().descriptor.events)
    assert set(descriptor_events) == declared, (
        f"descriptor.events 与 module.yaml 声明不一致：多={set(descriptor_events) - declared} "
        f"少={declared - set(descriptor_events)}"
    )
    assert len(descriptor_events) == len(set(descriptor_events)), "descriptor.events 不得有重复项"
