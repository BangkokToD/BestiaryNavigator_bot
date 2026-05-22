"""Routes и зависимости Telegram Login для web UI."""

from collections.abc import AsyncIterator
from typing import Annotated, Protocol

from fastapi import APIRouter, Depends, HTTPException, Request, status
from fastapi.responses import RedirectResponse, Response
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.settings import Settings, get_settings
from app.db.models import TelegramUser
from app.db.session import get_db_session
from app.services import TelegramUserService
from app.web.security import (
    TelegramLoginVerificationError,
    build_admin_cookie_value,
    verify_telegram_login_payload,
)

_ADMIN_COOKIE_MAX_AGE_SECONDS = 10 * 365 * 24 * 60 * 60
_ADMIN_COOKIE_PATH = "/"

router = APIRouter()


class TelegramLoginUserService(Protocol):
    """Минимальный contract сервиса Telegram-пользователей для web-auth."""

    async def upsert_telegram_user(
        self,
        *,
        telegram_id: int,
        username: str | None,
        display_name: str | None,
    ) -> TelegramUser:
        """Создаёт или обновляет Telegram-пользователя.

        Args:
            telegram_id: Telegram ID пользователя.
            username: Telegram username.
            display_name: Отображаемое имя.

        Returns:
            Модель TelegramUser.
        """

    def has_admin_access(self, telegram_user: TelegramUser | None) -> bool:
        """Проверяет admin-доступ пользователя.

        Args:
            telegram_user: Модель TelegramUser или `None`.

        Returns:
            `True`, если пользователь является админом.
        """


def get_web_auth_settings() -> Settings:
    """Возвращает settings для web-auth routes.

    Returns:
        Runtime settings приложения.
    """
    return get_settings()


async def get_telegram_login_user_service(
    session: Annotated[AsyncSession, Depends(get_db_session)],
    settings: Annotated[Settings, Depends(get_web_auth_settings)],
) -> AsyncIterator[TelegramLoginUserService]:
    """Создаёт TelegramUserService с транзакционным commit/rollback.

    Args:
        session: Async SQLAlchemy session.
        settings: Runtime settings приложения.

    Yields:
        Сервис Telegram-пользователей для web-auth.
    """
    service = TelegramUserService.from_session(
        session=session,
        settings=settings,
    )

    try:
        yield service
        await session.commit()
    except Exception:
        await session.rollback()
        raise


@router.get("/auth/telegram", response_class=RedirectResponse)
async def telegram_login(
    request: Request,
    settings: Annotated[Settings, Depends(get_web_auth_settings)],
    telegram_users: Annotated[
        TelegramLoginUserService,
        Depends(get_telegram_login_user_service),
    ],
) -> RedirectResponse:
    """Обрабатывает callback Telegram Login Widget.

    Args:
        request: FastAPI request с query params Telegram Login.
        settings: Runtime settings.
        telegram_users: Сервис Telegram-пользователей.

    Returns:
        Redirect на Dashboard.

    Raises:
        HTTPException: Если Telegram Login payload невалиден.
    """
    try:
        payload = verify_telegram_login_payload(
            dict(request.query_params),
            bot_token=settings.telegram_bot_token,
        )
    except TelegramLoginVerificationError as exc:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=str(exc),
        ) from exc

    telegram_user = await telegram_users.upsert_telegram_user(
        telegram_id=payload.telegram_id,
        username=payload.username,
        display_name=payload.display_name,
    )

    response = _redirect_to_dashboard()
    if telegram_users.has_admin_access(telegram_user):
        _set_admin_cookie(
            response=response,
            settings=settings,
            telegram_id=telegram_user.telegram_id,
        )
    else:
        _delete_admin_cookie(response=response, settings=settings)

    return response


@router.api_route("/auth/logout", methods=["GET", "POST"], response_class=RedirectResponse)
async def telegram_logout(
    settings: Annotated[Settings, Depends(get_web_auth_settings)],
) -> RedirectResponse:
    """Очищает admin-cookie и возвращает пользователя на Dashboard.

    Args:
        settings: Runtime settings.

    Returns:
        Redirect на Dashboard без admin-cookie.
    """
    response = _redirect_to_dashboard()
    _delete_admin_cookie(response=response, settings=settings)

    return response


def _redirect_to_dashboard() -> RedirectResponse:
    """Создаёт redirect на Dashboard.

    Returns:
        Redirect response.
    """
    return RedirectResponse(
        url="/",
        status_code=status.HTTP_303_SEE_OTHER,
    )


def _set_admin_cookie(
    *,
    response: Response,
    settings: Settings,
    telegram_id: int,
) -> None:
    """Устанавливает подписанную admin-cookie.

    Args:
        response: Response, куда добавляется cookie.
        settings: Runtime settings с именем cookie и secret.
        telegram_id: Telegram ID администратора.
    """
    response.set_cookie(
        key=settings.web_admin_cookie_name,
        value=build_admin_cookie_value(
            telegram_id=telegram_id,
            secret=settings.web_session_secret,
        ),
        max_age=_ADMIN_COOKIE_MAX_AGE_SECONDS,
        path=_ADMIN_COOKIE_PATH,
        httponly=True,
        secure=_should_use_secure_cookie(settings),
        samesite="lax",
    )


def _delete_admin_cookie(*, response: Response, settings: Settings) -> None:
    """Удаляет admin-cookie.

    Args:
        response: Response, куда добавляется Set-Cookie на удаление.
        settings: Runtime settings с именем cookie.
    """
    response.delete_cookie(
        key=settings.web_admin_cookie_name,
        path=_ADMIN_COOKIE_PATH,
        secure=_should_use_secure_cookie(settings),
        httponly=True,
        samesite="lax",
    )


def _should_use_secure_cookie(settings: Settings) -> bool:
    """Определяет флаг Secure для admin-cookie.

    Args:
        settings: Runtime settings приложения.

    Returns:
        `True` только для production-окружения.
    """
    return settings.app_env == "prod"


__all__ = [
    "TelegramLoginUserService",
    "get_telegram_login_user_service",
    "get_web_auth_settings",
    "router",
]
