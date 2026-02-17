from __future__ import annotations


class UserInterventionRequired(RuntimeError):
    """자동으로 진행할 수 없어서 사용자 개입이 필요한 상태."""

    def __init__(self, message: str) -> None:
        super().__init__(message)

