from enum import StrEnum


class Platform(StrEnum):
    ALIBABA = "alibaba"
    GLOBALSOURCES = "globalsources"


class Channel(StrEnum):
    EMAIL = "email"
    PLATFORM = "platform"
