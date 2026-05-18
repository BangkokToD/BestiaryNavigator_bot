"""Общий модуль structured logging приложения."""

import json
import logging
import sys
from datetime import UTC, datetime
from types import TracebackType
from typing import Self


class JsonLogFormatter(logging.Formatter):
    """Форматирует запись лога в JSON."""

    def __init__(self, service_name: str) -> None:
        """Инициализирует formatter.

        Args:
            service_name: Название runtime-сервиса для поля `service`.
        """
        super().__init__()
        self.service_name = service_name

    def format(self, record: logging.LogRecord) -> str:
        """Возвращает JSON-представление записи лога.

        Args:
            record: Запись стандартного logging.

        Returns:
            JSON-строка для stdout.
        """
        payload: dict[str, object] = {
            "timestamp": datetime.fromtimestamp(record.created, tz=UTC).isoformat(),
            "level": record.levelname,
            "service": self.service_name,
            "logger": record.name,
            "message": record.getMessage(),
        }

        if record.exc_info:
            payload["exception"] = self.formatException(record.exc_info)

        if record.stack_info:
            payload["stack"] = self.formatStack(record.stack_info)

        return json.dumps(payload, ensure_ascii=False)


def configure_logging(service_name: str, level: str = "INFO") -> None:
    """Настраивает JSON-логирование в stdout.

    Args:
        service_name: Название runtime-сервиса.
        level: Минимальный уровень логирования.
    """
    normalized_level = logging.getLevelNamesMapping().get(level.upper(), logging.INFO)

    handler = logging.StreamHandler(sys.stdout)
    handler.setFormatter(JsonLogFormatter(service_name=service_name))

    root_logger = logging.getLogger()
    root_logger.handlers.clear()
    root_logger.setLevel(normalized_level)
    root_logger.addHandler(handler)


def get_logger(name: str) -> logging.Logger:
    """Возвращает logger по имени модуля.

    Args:
        name: Имя logger.

    Returns:
        Экземпляр стандартного logger.
    """
    return logging.getLogger(name)


class LogExceptionContext:
    """Контекстный helper для логирования неожиданных исключений."""

    def __init__(self, logger: logging.Logger, message: str) -> None:
        """Инициализирует helper.

        Args:
            logger: Logger, куда будет записано исключение.
            message: Сообщение для ошибки.
        """
        self.logger = logger
        self.message = message

    def __enter__(self) -> Self:
        """Возвращает текущий helper.

        Returns:
            Текущий экземпляр helper.
        """
        return self

    def __exit__(
        self,
        exc_type: type[BaseException] | None,
        exc_value: BaseException | None,
        traceback: TracebackType | None,
    ) -> bool:
        """Логирует исключение и не подавляет его.

        Args:
            exc_type: Тип исключения.
            exc_value: Экземпляр исключения.
            traceback: Traceback исключения.

        Returns:
            `False`, чтобы исключение продолжило обычный путь обработки.
        """
        if exc_value is not None:
            self.logger.exception(self.message)

        return False