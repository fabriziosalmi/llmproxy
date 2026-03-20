import logging
import chromadb
from chromadb.config import Settings
from typing import Optional, Dict, Any
import os
import json
import uuid
import time

logger = logging.getLogger(__name__)

class SemanticCache:
    """Persistent semantic cache using ChromaDB for vector similarity search."""
    
    def __init__(self, assistant=None, db_path: str = "cache_db", threshold: float = 0.9):
        self.assistant = assistant
        self.threshold = threshold
        self.db_path = db_path
        
        # Initialize ChromaDB
        self.client = chromadb.PersistentClient(path=db_path)
        self.collection = self.client.get_or_create_collection(
            name="semantic_cache",
            metadata={"hnsw:space": "cosine"}
        )
        logger.info(f"SemanticCache: Initialized at {db_path} with threshold {threshold}")

    async def get(self, prompt: str) -> Optional[Dict[str, Any]]:
        """Retrieves a cached response if a semantically similar prompt exists."""
        if not self.assistant:
            return None
            
        try:
            # Generate embedding using LocalAssistant
            embedding = await self.assistant.get_embeddings(prompt)
            if not embedding:
                return None
                
            # Search ChromaDB
            results = self.collection.query(
                query_embeddings=[embedding],
                n_results=1,
                include=["documents", "metadatas", "distances"]
            )
            
            if results["ids"] and results["distances"][0]:
                distance = results["distances"][0][0]
                similarity = 1 - distance # Cosine similarity
                
                if similarity >= self.threshold:
                    metadata = results["metadatas"][0][0]
                    ttl = metadata.get("ttl", 3600) # Default 1h
                    created_at = metadata.get("created_at", 0)
                    
                    if time.time() - created_at > ttl:
                        logger.info(f"SemanticCache: EXPIRED (age: {time.time() - created_at:.1f}s)")
                        return None
                        
                    logger.info(f"SemanticCache HIT: Similarity {similarity:.4f}")
                    # The response body was stored as a JSON string in 'response'
                    return json.loads(metadata.get("response", "{}"))
                    
            logger.info("SemanticCache: MISS")
            return None
        except Exception as e:
            logger.error(f"SemanticCache GET error: {e}")
            return None

    async def add(self, prompt: str, response: Dict[str, Any], metadata: Optional[Dict[str, Any]] = None):
        """Stores a prompt-response pair in the semantic cache."""
        if not self.assistant:
            return
            
        try:
            embedding = await self.assistant.get_embeddings(prompt)
            if not embedding:
                return
                
            cache_id = str(uuid.uuid4())
            combined_metadata = metadata or {}
            combined_metadata["response"] = json.dumps(response)
            combined_metadata["created_at"] = time.time()
            combined_metadata["ttl"] = 3600 # Configurable TTL
            
            self.collection.add(
                ids=[cache_id],
                embeddings=[embedding],
                documents=[prompt],
                metadatas=[combined_metadata]
            )
            logger.info("SemanticCache: STORED new response")
        except Exception as e:
            logger.error(f"SemanticCache ADD error: {e}")
