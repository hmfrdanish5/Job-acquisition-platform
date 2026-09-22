"""Acquisition errors. Adapters must not import the dashboard or JobStore."""


class AcquisitionError(Exception):
    def __init__(self, message: str, *, blocked: bool = False) -> None:
        super().__init__(message)
        self.blocked = blocked
