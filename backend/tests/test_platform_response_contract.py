from fastapi import Response, status

from app.api.v1.platform import router


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
