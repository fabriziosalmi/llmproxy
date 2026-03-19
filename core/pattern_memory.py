import chromadb
import uuid
from typing import Optional, Dict, Any

class PatternMemory:
    """Stores and retrieves site interaction patterns to predict adapters."""
    
    def __init__(self, persist_directory: str = "./pattern_db"):
        self.client = chromadb.PersistentClient(path=persist_directory)
        self.collection = self.client.get_or_create_collection(name="site_patterns")

    def remember(self, url: str, html_features: str, adapter_template: Dict[str, Any]):
        """Indexes a site pattern for future prediction."""
        self.collection.add(
            documents=[html_features],
            metadatas=[{"url": url, "template": str(adapter_template)}],
            ids=[str(uuid.uuid4())]
        )

    def predict(self, html_features: str) -> Optional[Dict[str, Any]]:
        """Finds the most similar site pattern and returns its adapter template."""
        results = self.collection.query(
            query_texts=[html_features],
            n_results=1
        )
        
        if results["documents"] and results["distances"][0][0] < 0.3: # Similarity threshold
            import ast
            metadata = results["metadatas"][0][0]
            return ast.literal_eval(metadata["template"])
        
        return None
