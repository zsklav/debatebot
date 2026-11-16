"""Shared persistence errors and optional PostgreSQL JSON adaptation."""


class StorageUnavailable(RuntimeError):
    pass


class SessionConflict(RuntimeError):
    pass


def jsonb(value):
    try:
        from psycopg.types.json import Jsonb
    except ImportError as error:
        raise StorageUnavailable("Install requirements/storage.txt") from error
    return Jsonb(value)
