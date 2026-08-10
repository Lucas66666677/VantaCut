from enum import Enum


FREE_MAX_RENDER_SECONDS = 5 * 60


class RenderEntitlementError(PermissionError):
    pass


def validate_render_entitlement(
    tier: str | Enum,
    duration_seconds: float,
    resolution: str,
) -> None:
    if _tier_value(tier) != "free":
        return
    if duration_seconds > FREE_MAX_RENDER_SECONDS:
        raise RenderEntitlementError("免費方案僅支援輸出最長 5 分鐘的影片，請升級 Pro。")
    if resolution == "4k":
        raise RenderEntitlementError("4K 導出僅限 Pro 方案，請升級後重試。")


def requires_watermark(tier: str | Enum) -> bool:
    return _tier_value(tier) == "free"


def _tier_value(tier: str | Enum) -> str:
    return str(tier.value if isinstance(tier, Enum) else tier).lower()
