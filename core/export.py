"""
LLMPROXY — Dataset Export Module (Session 7.5)

Async JSONL writer with optional Parquet conversion.
PII-free export for training dataset curation.

Features:
  - Async JSONL append (non-blocking I/O)
  - Daily rotation with compression (.tar.zst)
  - PII scrubbing (emails, IPs, API keys)
  - Optional Parquet conversion (if pyarrow available)
"""

import os
import re
import json
import gzip
import logging
import asyncio
import aiofiles
from datetime import datetime, date
from typing import Dict, Any, Optional, List
from pathlib import Path

logger = logging.getLogger(__name__)

# PII patterns to scrub
PII_PATTERNS = [
    (re.compile(r'[a-zA-Z0-9._%+-]+@[a-zA-Z0-9.-]+\.[a-zA-Z]{2,}'), '<EMAIL>'),
    (re.compile(r'\b\d{1,3}\.\d{1,3}\.\d{1,3}\.\d{1,3}\b'), '<IP>'),
    (re.compile(r'sk-[a-zA-Z0-9]{20,}'), '<API_KEY>'),
    (re.compile(r'Bearer\s+[a-zA-Z0-9._\-]+'), 'Bearer <REDACTED>'),
    (re.compile(r'eyJ[a-zA-Z0-9_\-]+\.eyJ[a-zA-Z0-9_\-]+\.[a-zA-Z0-9_\-]+'), '<JWT>'),
]


def scrub_pii(text: str) -> str:
    """Remove PII patterns from text."""
    for pattern, replacement in PII_PATTERNS:
        text = pattern.sub(replacement, text)
    return text


def scrub_dict(d: Dict[str, Any]) -> Dict[str, Any]:
    """Recursively scrub PII from a dictionary."""
    result = {}
    for k, v in d.items():
        # Skip known sensitive fields entirely
        if k.lower() in ('authorization', 'api_key', 'token', 'password', 'secret'):
            result[k] = '<REDACTED>'
        elif isinstance(v, str):
            result[k] = scrub_pii(v)
        elif isinstance(v, dict):
            result[k] = scrub_dict(v)
        elif isinstance(v, list):
            result[k] = [scrub_dict(i) if isinstance(i, dict) else (scrub_pii(i) if isinstance(i, str) else i) for i in v]
        else:
            result[k] = v
    return result


class DatasetExporter:
    """
    Async JSONL writer for training dataset export.

    Usage:
        exporter = DatasetExporter(output_dir="exports")
        await exporter.record({"messages": [...], "model": "gpt-4o", "latency_ms": 120})
    """

    def __init__(
        self,
        output_dir: str = "exports",
        scrub: bool = True,
        compress_on_rotate: bool = True,
    ):
        self.output_dir = Path(output_dir)
        self.output_dir.mkdir(parents=True, exist_ok=True)
        self.scrub = scrub
        self.compress_on_rotate = compress_on_rotate
        self._current_date: Optional[date] = None
        self._file_handle: Optional[aiofiles.threadpool.text.AsyncTextIOWrapper] = None
        self._lock = asyncio.Lock()

    def _get_filepath(self, d: date) -> Path:
        return self.output_dir / f"llmproxy_export_{d.isoformat()}.jsonl"

    async def _ensure_file(self):
        """Open or rotate the output file based on current date."""
        today = date.today()
        if self._current_date == today and self._file_handle:
            return

        # Close previous file
        if self._file_handle:
            await self._file_handle.close()
            # Compress previous day's file
            if self.compress_on_rotate and self._current_date:
                prev_path = self._get_filepath(self._current_date)
                if prev_path.exists():
                    await self._compress(prev_path)

        self._current_date = today
        filepath = self._get_filepath(today)
        self._file_handle = await aiofiles.open(filepath, mode='a', encoding='utf-8')
        logger.info(f"Export: Writing to {filepath}")

    async def _compress(self, filepath: Path):
        """Compress a JSONL file with gzip (synchronous, run in executor)."""
        gz_path = filepath.with_suffix('.jsonl.gz')
        def _do_compress():
            try:
                with open(filepath, 'rb') as f_in:
                    with gzip.open(gz_path, 'wb', compresslevel=6) as f_out:
                        f_out.write(f_in.read())
                filepath.unlink()
                logger.info(f"Export: Compressed {filepath.name} → {gz_path.name}")
            except Exception as e:
                logger.error(f"Export: Compression failed for {filepath}: {e}")

        loop = asyncio.get_event_loop()
        await loop.run_in_executor(None, _do_compress)

    async def record(self, entry: Dict[str, Any]):
        """
        Append a record to the current day's JSONL export file.

        Entry should contain:
          - messages: list of {role, content}
          - model: str
          - latency_ms: float
          - tokens: {prompt, completion}
          - cost_usd: float
          - timestamp: str (auto-added if missing)
        """
        async with self._lock:
            await self._ensure_file()

            # Add timestamp if missing
            if 'timestamp' not in entry:
                entry['timestamp'] = datetime.utcnow().isoformat() + 'Z'

            # Scrub PII
            if self.scrub:
                entry = scrub_dict(entry)

            line = json.dumps(entry, ensure_ascii=False, separators=(',', ':'))
            await self._file_handle.write(line + '\n')
            await self._file_handle.flush()

    async def export_parquet(self, jsonl_path: Optional[str] = None) -> Optional[str]:
        """
        Convert a JSONL file to Parquet format (requires pyarrow).
        Returns output path or None if pyarrow not available.
        """
        try:
            import pyarrow as pa
            import pyarrow.parquet as pq
        except ImportError:
            logger.warning("Export: pyarrow not installed, Parquet export unavailable")
            return None

        if jsonl_path is None:
            jsonl_path = str(self._get_filepath(date.today()))

        if not os.path.exists(jsonl_path):
            logger.error(f"Export: File not found: {jsonl_path}")
            return None

        records: List[Dict] = []
        with open(jsonl_path, 'r') as f:
            for line in f:
                line = line.strip()
                if line:
                    records.append(json.loads(line))

        if not records:
            return None

        # Flatten to tabular structure
        table = pa.Table.from_pylist(records)
        parquet_path = jsonl_path.replace('.jsonl', '.parquet')
        pq.write_table(table, parquet_path, compression='zstd')
        logger.info(f"Export: Wrote {len(records)} records to {parquet_path}")
        return parquet_path

    async def close(self):
        """Flush and close the current file."""
        if self._file_handle:
            await self._file_handle.flush()
            await self._file_handle.close()
            self._file_handle = None
