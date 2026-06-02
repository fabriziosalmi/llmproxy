import yaml
import os
from .base import BaseRepository
from .store import SQLiteRepository


class StorageFactory:
    """Factory for dynamically creating storage repositories."""

    @staticmethod
    def get_repository(config_path: str = "config.yaml") -> BaseRepository:
        """Determines the correct repository based on configuration."""
        config = {}
        if os.path.exists(config_path):
            with open(config_path, "r") as f:
                config = yaml.safe_load(f)

        storage_type = config.get("server", {}).get("storage", {}).get("type", "sqlite")

        if storage_type == "sqlite":
            return SQLiteRepository()
        elif storage_type == "redis":
            # For future expansion: from .redis_store import RedisRepository
            # return RedisRepository()
            raise NotImplementedError(
                "Redis storage not yet implemented. Defaulting to SQLite."
            )
        elif storage_type == "postgres":
            try:
                from .pg_store import PostgresRepository
            except ImportError as e:
                raise ImportError(
                    "PostgreSQL storage requires the 'asyncpg' library. "
                    "Please run 'pip install asyncpg' to use PostgreSQL."
                ) from e

            # Read DSN from env var first, fallback to config value
            storage_cfg = config.get("server", {}).get("storage", {})
            dsn_env = storage_cfg.get("dsn_env", "DATABASE_URL")
            dsn = os.environ.get(dsn_env)
            if not dsn:
                dsn = storage_cfg.get(
                    "dsn", "postgresql://postgres:postgres@localhost:5432/llmproxy"
                )

            return PostgresRepository(dsn)

        return SQLiteRepository()
