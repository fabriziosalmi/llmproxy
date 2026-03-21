import sqlite3
import json
from models import LLMEndpoint, EndpointStatus

def persist_adapter():
    conn = sqlite3.connect('endpoints.db')
    cursor = conn.cursor()
    
    # Create table if not exists (already done by the system, but for demo safety)
    cursor.execute("""
        CREATE TABLE IF NOT EXISTS endpoints (
            id TEXT PRIMARY KEY,
            url TEXT NOT NULL,
            status TEXT NOT NULL,
            metadata TEXT,
            last_verified TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        )
    """)

    adapter_data = {
        "target_name": "DuckDuckGo AI",
        "tier": "Optimized",
        "adapter_type": "SOTA_SNIFFED",
        "auth": {"header": "x-vqd-hash-1", "value": "eyJzZXJ2ZXJfaGFzaGVzIjpbInNtMUhk..."},
        "schema": {"messages": "list", "model": "string"}
    }

    cursor.execute(
        "INSERT OR REPLACE INTO endpoints (id, url, status, metadata) VALUES (?, ?, ?, ?)",
        ("duckchat-sota", "https://duck.ai/duckchat/v1/chat", "verified", json.dumps(adapter_data))
    )
    conn.commit()
    print("[PERSISTENCE] SOTA Adapter 'duckchat-sota' added to SQLite tracked pool.")
    conn.close()

if __name__ == "__main__":
    persist_adapter()
