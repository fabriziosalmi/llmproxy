from core.base_agent import BaseAgent
from store.store import EndpointStore
from fastapi import FastAPI, Request
from fastapi.responses import HTMLResponse, JSONResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates
import uvicorn
import os

class AdminAgent(BaseAgent):
    """Serves the Admin Dashboard UI."""
    def __init__(self, store: EndpointStore, port: int = 8081):
        super().__init__("admin")
        self.store = store
        self.port = port
        self.app = FastAPI(title="LLM Proxy Admin")
        self._setup_routes()

    def _setup_routes(self):
        @self.app.get("/", response_class=HTMLResponse)
        async def index(request: Request):
            # This would ideally serve a Jinja template or a static HTML
            with open("test_ui/admin.html", "r") as f:
                return f.read()

        @self.app.post("/api/endpoints")
        async def register_endpoint(request: Request):
            try:
                data = await request.json()
                from models import LLMEndpoint, EndpointStatus
                import uuid
                endpoint = LLMEndpoint(
                    id=str(uuid.uuid4()),
                    url=data["url"],
                    status=EndpointStatus.DISCOVERED,
                    metadata={}
                )
                await self.store.add_endpoint(endpoint)
                return JSONResponse(content={
                    "id": endpoint.id,
                    "url": str(endpoint.url),
                    "status": endpoint.status.value,
                    "metadata": endpoint.metadata
                }, status_code=201)
            except Exception as e:
                raise HTTPException(status_code=500, detail=str(e))

        @self.app.get("/api/endpoints")
        async def get_endpoints():
            endpoints = await self.store.get_all()
            return JSONResponse(content=[{
                "id": e.id,
                "url": str(e.url),
                "status": e.status.value,
                "metadata": e.metadata
            } for e in endpoints])

    async def run(self):
        self.logger.info(f"Starting Admin Dashboard on port {self.port}...")
        config = uvicorn.Config(self.app, host="0.0.0.0", port=self.port, log_level="error")
        server = uvicorn.Server(config)
        await server.serve()
