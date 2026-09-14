"""CMS-specific parsing adapters for the shared department crawler."""

from .base import DepartmentCMSAdapter
from .numeric_cms import NumericCMSAdapter
from .query_view_do import QueryViewDoAdapter
from .query_view_legacy import QueryViewLegacyAdapter
from .query_mcode import QueryMcodeAdapter
from .legacy_php import LegacyPHPAdapter
from .html_php import HtmlPHPAdapter


ADAPTERS = {
    "numeric_cms": NumericCMSAdapter(),
    "query_view_do": QueryViewDoAdapter(),
    "query_view_legacy": QueryViewLegacyAdapter(),
    "query_mcode": QueryMcodeAdapter(),
    "legacy_php": LegacyPHPAdapter(),
    "html_php": HtmlPHPAdapter(),
}


def get_adapter(name: str) -> DepartmentCMSAdapter:
    try:
        return ADAPTERS[name]
    except KeyError as exc:
        raise ValueError(f"unsupported department CMS adapter: {name!r}") from exc


__all__ = ["DepartmentCMSAdapter", "get_adapter"]
