"""Базовые web routes приложения."""

from fastapi import APIRouter

from app.web.admin_api_error_settings import router as admin_api_error_settings_router
from app.web.admin_api_errors import router as admin_api_errors_router
from app.web.admin_clan_settings import router as admin_clan_settings_router
from app.web.admin_clans import router as admin_clans_router
from app.web.admin_notification_routes import router as admin_notification_routes_router
from app.web.admin_telegram_settings import router as admin_telegram_settings_router
from app.web.auth import router as auth_router
from app.web.feature_pages import router as feature_pages_router

router = APIRouter(include_in_schema=False)
router.include_router(auth_router)
router.include_router(feature_pages_router)
router.include_router(admin_api_errors_router)
router.include_router(admin_api_error_settings_router)
router.include_router(admin_clan_settings_router)
router.include_router(admin_clans_router)
router.include_router(admin_telegram_settings_router)
router.include_router(admin_notification_routes_router)
