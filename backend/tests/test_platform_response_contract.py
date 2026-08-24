import importlib.util
from pathlib import Path

from fastapi import Response, status


def _load_platform_router():
    module_path = Path(__file__).resolve().parents[1] / "app" / "api" / "v1" / "platform.py"
    spec = importlib.util.spec_from_file_location("_vantacut_test_platform_router", module_path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module.router


router = _load_platform_router()


def test_revoke_platform_api_key_is_an_explicit_bodyless_204() -> None:
    route = next(
        route
        for route in router.routes
        if route.path == "/platform/v1/api-keys/{api_key_id}"
        and "DELETE" in route.methods
    )

    assert route.status_code == status.HTTP_204_NO_CONTENT
    assert route.response_model is None
    assert route.response_class is Response
