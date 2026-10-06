"""Background analysis jobs: durable on disk, independent of any browser session.

Jobs live under ``{data_cache_dir}/jobs/<job_id>/``. A detached
``python -m cli.job_worker <job_id>`` process runs the graph; any web session
can list, watch, or open results later.
"""

from __future__ import annotations

import json
import os
import signal
import subprocess
import sys
import uuid
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Literal

from cli.models import AnalystType
from tradingagents.default_config import build_default_config

JobStatus = Literal["queued", "running", "completed", "failed"]

_META = "meta.json"
_SELECTIONS = "selections.json"
_CONFIG = "config.json"
_SNAPSHOT = "snapshot.json"
_FINAL = "final_state.json"
_PID = "worker.pid"
_WORKER_LOG = "worker.log"


def jobs_root(config: dict | None = None) -> Path:
    """Directory that holds all background jobs."""
    cfg = config if config is not None else build_default_config()
    root = Path(cfg["data_cache_dir"]) / "jobs"
    root.mkdir(parents=True, exist_ok=True)
    return root


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _atomic_write(path: Path, data: dict | list) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(json.dumps(data, indent=2, default=str), encoding="utf-8")
    tmp.replace(path)


def _read_json(path: Path, default: Any = None) -> Any:
    if not path.is_file():
        return default
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError):
        return default


def serialize_selections(selections: dict) -> dict:
    """JSON-safe copy of a run's selections (AnalystType → str)."""
    out = dict(selections)
    out["analysts"] = [
        a.value if isinstance(a, AnalystType) else str(a) for a in selections["analysts"]
    ]
    return out


def deserialize_selections(data: dict) -> dict:
    """Restore AnalystType values from stored selections."""
    out = dict(data)
    out["analysts"] = [AnalystType(a) for a in data["analysts"]]
    return out


def _pid_alive(pid: int | None) -> bool:
    if not pid or pid <= 0:
        return False
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    except PermissionError:
        return True
    return True


@dataclass
class JobRecord:
    """In-memory view of one job's metadata."""

    job_id: str
    status: JobStatus
    ticker: str
    analysis_date: str
    created_at: str
    updated_at: str
    pid: int | None = None
    error: str | None = None
    report_path: str | None = None
    html_path: str | None = None
    asset_type: str = "stock"

    @property
    def label(self) -> str:
        return f"{self.ticker} @ {self.analysis_date}"


class JobStore:
    """Filesystem job store under ``data_cache_dir/jobs``."""

    def __init__(self, config: dict | None = None) -> None:
        self.config = config if config is not None else build_default_config()
        self.root = jobs_root(self.config)

    def job_dir(self, job_id: str) -> Path:
        return self.root / job_id

    def create(self, selections: dict, run_config: dict) -> JobRecord:
        job_id = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ") + "-" + uuid.uuid4().hex[:8]
        created = _now()
        meta = {
            "job_id": job_id,
            "status": "queued",
            "ticker": selections["ticker"],
            "analysis_date": selections["analysis_date"],
            "asset_type": selections.get("asset_type", "stock"),
            "created_at": created,
            "updated_at": created,
            "pid": None,
            "error": None,
            "report_path": None,
            "html_path": None,
        }
        d = self.job_dir(job_id)
        d.mkdir(parents=True, exist_ok=True)
        _atomic_write(d / _META, meta)
        _atomic_write(d / _SELECTIONS, serialize_selections(selections))
        # Config may contain non-JSON types; default=str keeps it loadable.
        _atomic_write(d / _CONFIG, run_config)
        _atomic_write(
            d / _SNAPSHOT,
            {
                "agent_status": {},
                "messages": [],
                "tool_calls": [],
                "current_report": "",
                "stats": {"llm_calls": 0, "tool_calls": 0, "tokens_in": 0, "tokens_out": 0},
                "updated_at": created,
            },
        )
        return self._record(meta)

    def _record(self, meta: dict) -> JobRecord:
        return JobRecord(
            job_id=meta["job_id"],
            status=meta["status"],
            ticker=meta["ticker"],
            analysis_date=meta["analysis_date"],
            created_at=meta["created_at"],
            updated_at=meta["updated_at"],
            pid=meta.get("pid"),
            error=meta.get("error"),
            report_path=meta.get("report_path"),
            html_path=meta.get("html_path"),
            asset_type=meta.get("asset_type", "stock"),
        )

    def get(self, job_id: str) -> JobRecord | None:
        meta = _read_json(self.job_dir(job_id) / _META)
        if not isinstance(meta, dict):
            return None
        record = self._record(meta)
        return self._reconcile(record)

    def _reconcile(self, record: JobRecord) -> JobRecord:
        """Mark orphaned workers (status running but PID gone) as failed."""
        if record.status in ("queued", "running") and record.pid and not _pid_alive(record.pid):
            self.update(
                record.job_id,
                status="failed",
                error="Worker process exited without finishing (browser-independent check).",
                pid=None,
            )
            refreshed = _read_json(self.job_dir(record.job_id) / _META) or {}
            return self._record(refreshed)
        if record.status == "queued" and record.pid is None:
            # Still waiting for spawn; leave as-is.
            pass
        return record

    def update(self, job_id: str, **fields: Any) -> JobRecord | None:
        path = self.job_dir(job_id) / _META
        meta = _read_json(path)
        if not isinstance(meta, dict):
            return None
        meta.update(fields)
        meta["updated_at"] = _now()
        _atomic_write(path, meta)
        return self._record(meta)

    def list_jobs(self, limit: int = 50) -> list[JobRecord]:
        rows: list[JobRecord] = []
        if not self.root.is_dir():
            return rows
        for child in sorted(self.root.iterdir(), reverse=True):
            if not child.is_dir():
                continue
            meta = _read_json(child / _META)
            if not isinstance(meta, dict):
                continue
            rows.append(self._reconcile(self._record(meta)))
            if len(rows) >= limit:
                break
        return rows

    def load_selections(self, job_id: str) -> dict | None:
        data = _read_json(self.job_dir(job_id) / _SELECTIONS)
        if not isinstance(data, dict):
            return None
        return deserialize_selections(data)

    def load_config(self, job_id: str) -> dict | None:
        data = _read_json(self.job_dir(job_id) / _CONFIG)
        return data if isinstance(data, dict) else None

    def write_snapshot(self, job_id: str, snapshot: dict) -> None:
        snapshot = dict(snapshot)
        snapshot["updated_at"] = _now()
        _atomic_write(self.job_dir(job_id) / _SNAPSHOT, snapshot)

    def read_snapshot(self, job_id: str) -> dict:
        data = _read_json(self.job_dir(job_id) / _SNAPSHOT, default={})
        return data if isinstance(data, dict) else {}

    def write_final_state(self, job_id: str, final_state: dict) -> None:
        _atomic_write(self.job_dir(job_id) / _FINAL, final_state)

    def read_final_state(self, job_id: str) -> dict | None:
        data = _read_json(self.job_dir(job_id) / _FINAL)
        return data if isinstance(data, dict) else None

    def write_pid(self, job_id: str, pid: int) -> None:
        (self.job_dir(job_id) / _PID).write_text(str(pid), encoding="utf-8")
        self.update(job_id, pid=pid, status="running")


def start_background_job(selections: dict, run_config: dict) -> JobRecord:
    """Create a job and spawn a detached worker that outlives the browser."""
    store = JobStore(run_config)
    record = store.create(selections, run_config)
    log_path = store.job_dir(record.job_id) / _WORKER_LOG
    log_fh = open(log_path, "a", encoding="utf-8")  # noqa: SIM115 - kept open for subprocess
    env = os.environ.copy()
    # Pin paths so the worker finds this job even if DEFAULT_CONFIG was imported
    # before .env was loaded in the parent process.
    env["TRADINGAGENTS_CACHE_DIR"] = str(Path(run_config["data_cache_dir"]))
    env["TRADINGAGENTS_RESULTS_DIR"] = str(Path(run_config["results_dir"]))
    try:
        proc = subprocess.Popen(
            [sys.executable, "-m", "cli.job_worker", record.job_id],
            stdout=log_fh,
            stderr=subprocess.STDOUT,
            stdin=subprocess.DEVNULL,
            start_new_session=True,
            close_fds=True,
            env=env,
        )
    except OSError as exc:
        log_fh.close()
        store.update(record.job_id, status="failed", error=f"Failed to start worker: {exc}")
        raise
    store.write_pid(record.job_id, proc.pid)
    # Parent no longer needs the log handle; child keeps the fd.
    log_fh.close()
    return store.get(record.job_id) or record


def stop_job(job_id: str, config: dict | None = None) -> bool:
    """Best-effort SIGTERM to a running worker. Returns True if a signal was sent."""
    store = JobStore(config)
    record = store.get(job_id)
    if record is None or not record.pid:
        return False
    try:
        os.kill(record.pid, signal.SIGTERM)
    except OSError:
        return False
    return True
