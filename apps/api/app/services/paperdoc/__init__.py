"""paperdoc/v2 —— 试卷导出的版式契约层。

这里只有「数据长什么样」和「什么算合法」，没有任何渲染逻辑。
渲染器（paperdoc/render.py，下一步再写）只允许读这套结构，不许自己猜尺寸。
"""

from typing import Any

_EXPORTS = {
    "SCHEMA_ID",
    "CONTENT_W",
    "FALLBACK_FIGURE_W",
    "PaperdocError",
    "check_doc",
    "load_doc",
    "validate_doc",
}

__all__ = sorted(_EXPORTS)


def __getattr__(name: str) -> Any:
    # 延迟导入：这样 `python -m app.services.paperdoc.schema` 不会触发
    # "found in sys.modules ... prior to execution" 的 RuntimeWarning。
    if name in _EXPORTS:
        from importlib import import_module

        return getattr(import_module(".schema", __name__), name)
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")
