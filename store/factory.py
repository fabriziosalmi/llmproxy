import yaml
import os
from typing import Type
from .base import BaseRepository
from .store import SQLiteRepository

class StorageFactory:
    """Factory for dynamically creating storage repositories."""
    
    @staticmethod
    def get_repository(config_path: str = "config.yaml") -> BaseRepository:
        """Determines the correct repository based on configuration."""
        config = {}
        if os.path.exists(config_path):
            with open(config_path, 'r') as f:
                config = yaml.safe_load(f)
        
        storage_type = config.get("server", {}).get("storage", {}).get("type", "sqlite")
        
        if storage_type == "sqlite":
            return SQLiteRepository()
        elif storage_type == "redis":
            # For future expansion: from .redis_store import RedisRepository
            # return RedisRepository()
            raise NotImplementedError("Redis storage not yet implemented. Defaulting to SQLite.")
        elif storage_type == "postgres":
            # For future expansion: from .pg_store import PGRepository
            # return PGRepository()
            raise NotImplementedError("PostgreSQL storage not yet implemented. Defaulting to SQLite.")
        
        return SQLiteRepository()
