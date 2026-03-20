import logging
import chromadb
from chromadb.config import Settings
from typing import Optional, Dict, Any
import os
import json
import uuid
import time
import hashlib
import numpy as np

logger = logging.getLogger(__name__)

# 11.3: Deterministic O(1) Bloom Filter for zero-latency negative cache lookups
class DeterministicBloomFilter:
    def __init__(self, size=10**7, hash_count=3, file_path="bloom.bin"):
        self.size = size
        self.hash_count = hash_count
        self.file_path = file_path
        if os.path.exists(file_path):
            with open(file_path, "rb") as f:
                self.bit_array = bytearray(f.read())
        else:
            self.bit_array = bytearray((size + 7) // 8)

    def save(self):
        with open(self.file_path, "wb") as f:
            f.write(self.bit_array)

    def _hashes(self, item):
        h = hashlib.md5(item.encode('utf-8')).hexdigest()
        h1 = int(h[:16], 16)
        h2 = int(h[16:], 16)
        for i in range(self.hash_count):
            yield (h1 + i * h2) % self.size

    def add(self, item):
        for index in self._hashes(item):
            self.bit_array[index // 8] |= (1 << (index % 8))

    def check(self, item):
        for index in self._hashes(item):
            if not (self.bit_array[index // 8] & (1 << (index % 8))):
                return False
        return True

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
        
        # Hydrate Bloom Filter
        self.bloom = DeterministicBloomFilter(file_path=os.path.join(db_path, "bloom.bin"))
        
        logger.info(f"SemanticCache: Initialized at {db_path} with threshold {threshold}")

    async def get(self, prompt: str) -> Optional[Dict[str, Any]]:
        """Retrieves a cached response if a semantically similar prompt exists."""
        if not self.assistant:
            return None
            
        try:
            # 11.3: O(1) Bloom Filter Bypass
            if not self.bloom.check(prompt):
                logger.debug("SemanticCache: BLOOM FILTER MISS (0ms latency)")
                return None
                
            # Generate embedding using LocalAssistant
            embedding = await self.assistant.get_embeddings(prompt)
            if not embedding:
                return None
                
            # 11.4: 1-Bit Vector Quantization
            # Binarize vectors to +1.0/-1.0, rendering Cosine Similarity mathematically equal to XOR Hamming Distance
            quantized_embedding = np.where(np.array(embedding) > 0, 1.0, -1.0).tolist()
                
            # Search ChromaDB
            results = self.collection.query(
                query_embeddings=[quantized_embedding],
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
                
            # 11.4: 1-Bit Vector Quantization
            quantized_embedding = np.where(np.array(embedding) > 0, 1.0, -1.0).tolist()
                
            cache_id = str(uuid.uuid4())
            combined_metadata = metadata or {}
            combined_metadata["response"] = json.dumps(response)
            combined_metadata["created_at"] = time.time()
            combined_metadata["ttl"] = 3600 # Configurable TTL
            
            self.collection.add(
                ids=[cache_id],
                embeddings=[quantized_embedding],
                documents=[prompt],
                metadatas=[combined_metadata]
            )
            
            # Update disk-backed Bloom Filter
            self.bloom.add(prompt)
            self.bloom.save()
            
            logger.info("SemanticCache: STORED new response (1-Bit Quantized)")
        except Exception as e:
            logger.error(f"SemanticCache ADD error: {e}")
