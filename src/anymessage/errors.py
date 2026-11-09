"""Stable source failure codes shared by local readers."""


class SourceError(RuntimeError):
    def __init__(self, code: str, message: str):
        super().__init__(f"{code}: {message}")
        self.code = code
