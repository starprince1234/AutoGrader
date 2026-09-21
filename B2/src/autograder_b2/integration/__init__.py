"""`b2` 与其它模块之间的**适配/装载**层。

本目录放的是"把库里的行、把配置里的标签变成冻结类型"这类**没有业务判定**的模块，由不同工作项各自
交付、共用同一个包名：

- `submission_loader`：从 `submissions`/`submission_files`/`artifacts` 重建冻结的
  `SubmissionEnvelope`（**唯一**的评测输入路径：冻结信封必须可往返，且装载自带摘要自校验）。
  它只依赖 `domain/*`、`db/schema.py` 与 `storage.paths` 的纯路径派生规则，不导入任何业务模块；
- `adapters` / `bindings`：评测链的适配器与绑定标签（另一工作项交付）。

本文件只重导出**装载器**的公开面（调用方因此不必知道它在哪个子模块里）；包内各模块之间互不依赖，
也不通过本文件互相引用，避免把不同工作项的导入顺序绑在一起。

这里**不做**接收、不做评测、不执行代码、不做授权判定——能不能看/评这份提交由调用方在路由层决定。
"""

from autograder_b2.integration.submission_loader import (
    SubmissionDigestMismatch,
    SubmissionInconsistent,
    SubmissionLoadError,
    SubmissionNotFound,
    load_submission_envelope,
)

__all__ = [
    "SubmissionDigestMismatch",
    "SubmissionInconsistent",
    "SubmissionLoadError",
    "SubmissionNotFound",
    "load_submission_envelope",
]
