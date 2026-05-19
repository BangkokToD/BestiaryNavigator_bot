"""Доменные исключения приложения."""


class DomainValidationError(ValueError):
    """Базовая ошибка доменной валидации."""


class TagValidationError(DomainValidationError):
    """Ошибка нормализации или валидации Clash-тега."""
