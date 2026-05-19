"""DTO и typed results Clash API."""

from collections.abc import Mapping
from dataclasses import dataclass
from typing import Self

from app.domain.tags import normalize_player_tag


@dataclass(frozen=True, slots=True)
class VerifyPlayerTokenResult:
    """Результат проверки владения игровым аккаунтом.

    Поле `token` из ответа Clash API намеренно не сохраняется в result: для
    доменного сценария достаточно тега и статуса, а одноразовый token не должен
    жить дольше проверки.
    """

    player_tag: str
    status: str

    @property
    def is_successful(self) -> bool:
        """Проверяет успешность verifytoken.

        Returns:
            `True`, если Clash API вернул статус `ok`.
        """
        return self.status == "ok"

    @classmethod
    def from_payload(cls, payload: Mapping[str, object]) -> Self:
        """Создаёт result из JSON payload Clash API.

        Args:
            payload: JSON object ответа `verifytoken`.

        Returns:
            Typed result проверки владения аккаунтом.

        Raises:
            ValueError: Если payload не содержит обязательных строковых полей.
        """
        raw_tag = payload.get("tag")
        raw_status = payload.get("status")

        if not isinstance(raw_tag, str):
            raise ValueError("Verifytoken response должен содержать строковое поле tag.")

        if not isinstance(raw_status, str):
            raise ValueError("Verifytoken response должен содержать строковое поле status.")

        normalized_status = raw_status.strip().lower()
        if not normalized_status:
            raise ValueError("Verifytoken response содержит пустой status.")

        return cls(
            player_tag=normalize_player_tag(raw_tag),
            status=normalized_status,
        )


__all__ = [
    "VerifyPlayerTokenResult",
]
