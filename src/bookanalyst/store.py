"""Small SQLite records; TeX, page images and model responses live in files."""

import asyncio, copy, hashlib, json, os, sqlite3, threading, time, uuid
from pathlib import Path
from contextlib import contextmanager, nullcontext
from .models import STAGES, WORKFLOW_VERSION


class WorkflowError(Exception):
    def __init__(self, code, message, status=409, review=False, retryable=False):
        super().__init__(message)
        self.code, self.message, self.status, self.review, self.retryable = (
            code,
            message,
            status,
            review,
            retryable,
        )


def encode(value):
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def digest(value):
    return hashlib.sha256(
        value if isinstance(value, bytes) else encode(value).encode()
    ).hexdigest()


def file_hash(path):
    with Path(path).open("rb") as f:
        return hashlib.file_digest(f, "sha256").hexdigest()


def atomic_json(path, value):
    atomic_text(path, encode(value) + "\n")


def atomic_text(path, text):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name("." + uuid.uuid4().hex + ".tmp")
    with tmp.open("w", encoding="utf-8", newline="\n") as f:
        f.write(text)
        f.flush()
        os.fsync(f.fileno())
    os.replace(tmp, path)


async def write_async(function, *args, **kwargs):
    """Move durable writes off-loop; finish them before cancellation releases locks."""
    pending = asyncio.create_task(asyncio.to_thread(function, *args, **kwargs))
    try:
        return await asyncio.shield(pending)
    except asyncio.CancelledError:
        await pending
        raise


class Store:
    def __init__(self, root):
        self.root = Path(root).resolve()
        self.root.mkdir(parents=True, exist_ok=True)
        self.db = self.root / "bookanalyst.sqlite3"
        self.lock = threading.RLock()
        self.output_lock = threading.Lock()
        with self.connection() as db:
            db.executescript("""PRAGMA journal_mode=WAL;
            CREATE TABLE IF NOT EXISTS objects(kind TEXT,id TEXT,payload TEXT,PRIMARY KEY(kind,id));
            CREATE TABLE IF NOT EXISTS operations(run_id TEXT,op_id TEXT,payload TEXT,PRIMARY KEY(run_id,op_id));
            CREATE TABLE IF NOT EXISTS calls(id TEXT PRIMARY KEY,run_id TEXT,revision INTEGER,kind TEXT,units INTEGER,state TEXT,metadata TEXT);
            CREATE TABLE IF NOT EXISTS tasks(run_id TEXT,id TEXT,stage TEXT,state TEXT,pages TEXT,error TEXT,PRIMARY KEY(run_id,id));
            CREATE INDEX IF NOT EXISTS calls_run ON calls(run_id);
            CREATE INDEX IF NOT EXISTS tasks_run ON tasks(run_id,stage);
            """)

    @contextmanager
    def connection(self, *, read_only=False):
        # WAL readers can see committed state while a writer is active. Do not
        # queue web status reads behind every model receipt/checkpoint write.
        with nullcontext() if read_only else self.lock:
            db = sqlite3.connect(self.db.as_uri() + "?mode=ro", uri=True, timeout=30) if read_only else sqlite3.connect(self.db, timeout=30)
            db.row_factory = sqlite3.Row
            try:
                if read_only:
                    db.execute("PRAGMA query_only=ON")
                with db:
                    yield db
            finally:
                db.close()

    def get(self, kind, key):
        with self.connection(read_only=True) as db:
            row = db.execute(
                "SELECT payload FROM objects WHERE kind=? AND id=?", (kind, key)
            ).fetchone()
            if not row:
                raise WorkflowError("NOT_FOUND", "记录不存在", 404)
            return json.loads(row[0])

    def put(self, kind, key, value):
        with self.connection() as db:
            db.execute(
                "INSERT OR REPLACE INTO objects VALUES(?,?,?)",
                (kind, key, encode(value)),
            )
        return value

    def update_book(self, key, **changes):
        with self.connection() as db:
            db.execute("BEGIN IMMEDIATE")
            row = db.execute("SELECT payload FROM objects WHERE kind='book' AND id=?", (key,)).fetchone()
            if not row:
                raise WorkflowError("NOT_FOUND", "书籍不存在", 404)
            book = json.loads(row[0])
            book.update(changes, updated_at=time.time())
            db.execute("UPDATE objects SET payload=? WHERE kind='book' AND id=?", (encode(book), key))
        return book

    def save_settings(self, value, credentials):
        with self.connection() as db:
            db.execute("BEGIN IMMEDIATE")
            db.execute(
                "INSERT OR REPLACE INTO objects VALUES(?,?,?)",
                ("settings", "main", encode(value)),
            )
            for name, key in credentials.items():
                db.execute(
                    "INSERT OR REPLACE INTO objects VALUES(?,?,?)",
                    ("credential", name, encode({"api_key": key})),
                )

    def list(self, kind, limit=100):
        with self.connection(read_only=True) as db:
            return [
                json.loads(r[0])
                for r in db.execute(
                    "SELECT payload FROM objects WHERE kind=? ORDER BY rowid DESC LIMIT ?",
                    (kind, limit),
                )
            ]

    def create_run(self, config, book):
        key = uuid.uuid4().hex
        now = time.time()
        run = dict(
            id=key,
            revision=1,
            created_at=now,
            updated_at=now,
            workflow_version=WORKFLOW_VERSION,
            state="PENDING",
            config=config,
            source=book,
            stage="setup",
            stages={s: "PENDING" for s in STAGES},
            usage={"llm": 0},
            error=None,
            pause_requested=False,
        )
        self.put("run", key, run)
        pages = list(range(config["start_page"], config["end_page"] + 1))
        size = config["pages_per_task"]
        for n, start in enumerate(range(0, len(pages), size)):
            self.task(
                key,
                f"batch-{n + 1:04d}",
                "convert",
                "PENDING",
                pages[start : start + size],
            )
        return run

    def _change(self, db, rid, callback, revision=None):
        row = db.execute(
            "SELECT payload FROM objects WHERE kind='run' AND id=?", (rid,)
        ).fetchone()
        if not row:
            raise WorkflowError("NOT_FOUND", "运行不存在", 404)
        run = json.loads(row[0])
        if revision is not None and revision != run["revision"]:
            raise WorkflowError("STALE_REVISION", "运行已更新，请刷新")
        result = callback(run)
        run["updated_at"] = time.time()
        db.execute(
            "UPDATE objects SET payload=? WHERE kind='run' AND id=?", (encode(run), rid)
        )
        return copy.deepcopy(result if result is not None else run)

    def change(self, rid, callback, revision=None):
        with self.connection() as db:
            db.execute("BEGIN IMMEDIATE")
            return self._change(db, rid, callback, revision)

    def operation(self, rid, command, callback, *, abandon_unknown=False):
        with self.connection() as db:
            db.execute("BEGIN IMMEDIATE")
            row = db.execute(
                "SELECT payload FROM operations WHERE run_id=? AND op_id=?",
                (rid, command.operation_id),
            ).fetchone()
            if row:
                return json.loads(row[0])
            result = self._change(db, rid, callback, command.revision)
            if abandon_unknown:
                calls = db.execute(
                    "SELECT id,state,metadata FROM calls WHERE run_id=? AND state IN ('RESERVED','RESULT_UNKNOWN')",
                    (rid,),
                ).fetchall()
                for call in calls:
                    metadata = json.loads(call["metadata"]) | {
                        "previous_state": call["state"],
                        "upstream_outcome": "unknown",
                        "retry_authorized": True,
                        "retry_authorized_at": time.time(),
                        "retry_operation_id": command.operation_id,
                    }
                    db.execute(
                        "UPDATE calls SET state='ABANDONED',metadata=? WHERE id=?",
                        (encode(metadata), call["id"]),
                    )
            db.execute(
                "INSERT INTO operations VALUES(?,?,?)",
                (rid, command.operation_id, encode(result)),
            )
            return result

    def task(self, rid, tid, stage, state, pages, error=None):
        with self.connection() as db:
            db.execute(
                "INSERT OR REPLACE INTO tasks VALUES(?,?,?,?,?,?)",
                (rid, tid, stage, state, encode(pages), encode(error)),
            )
        self.change(rid, lambda r: None)

    def tasks(self, rid, stage=None):
        with self.connection(read_only=True) as db:
            rows = db.execute(
                "SELECT * FROM tasks WHERE run_id=?"
                + (" AND stage=?" if stage else "")
                + " ORDER BY id",
                (rid, stage) if stage else (rid,),
            ).fetchall()
        return [
            dict(r) | {"pages": json.loads(r["pages"]), "error": json.loads(r["error"])}
            for r in rows
        ]

    def summary(self, run):
        old = run.get("workflow_version") != WORKFLOW_VERSION
        return {
            k: run.get(k)
            for k in (
                "id",
                "revision",
                "created_at",
                "updated_at",
                "state",
                "usage",
                "error",
                "retention_error",
            )
        } | {
            "title": run["source"]["title"],
            "book_id": run["source"]["id"],
            "kind": run.get("kind", "conversion"),
            "template": run.get("template"),
            "image_repair": run.get("image_repair"),
            "legacy": old,
            "pages": [run["config"]["start_page"], run["config"]["end_page"]],
            "stage": run.get("stage"),
            "stages": run.get("stages") if not old else {},
            "pages_per_task": run["config"].get("pages_per_task"),
            "concurrency": run["config"]["llm_concurrency"],
            "model": run["config"].get("model"),
            "structure_effort": run["config"].get("structure_effort"),
            "stage_models": run["config"].get("stage_models"),
            "model_refresh_pending": run.get("model_refresh_pending", False),
            "pending_llm_concurrency": run.get("pending_llm_concurrency"),
            "model_settings_updated_at": run.get("model_settings_updated_at"),
        }

    def reserve(self, rid, revision, kind, units, metadata):
        key = uuid.uuid4().hex
        with self.connection() as db:
            db.execute("BEGIN IMMEDIATE")
            self._change(
                db,
                rid,
                lambda r: r["usage"].update(llm=r["usage"].get("llm", 0) + 1),
            )
            db.execute(
                "INSERT INTO calls VALUES(?,?,?,?,?,?,?)",
                (
                    key,
                    rid,
                    revision,
                    kind,
                    units,
                    "RESERVED",
                    encode(metadata | {"started_at": time.time()}),
                ),
            )
        return key

    def finish_call(self, key, state, **metadata):
        with self.connection() as db:
            row = db.execute("SELECT metadata FROM calls WHERE id=?", (key,)).fetchone()
            meta = json.loads(row[0]) | metadata | {"finished_at": time.time()}
            db.execute(
                "UPDATE calls SET state=?,metadata=? WHERE id=?",
                (state, encode(meta), key),
            )

    def record_validation(self, rid, task_id, candidate, error=None):
        """Attach content validation to its receipt; preserve request state and timing."""
        if candidate is None:
            return
        with self.connection() as db:
            row = db.execute(
                "SELECT id,metadata FROM calls WHERE run_id=? AND state='COMPLETED' "
                "AND json_extract(metadata,'$.task_id')=? "
                "AND json_extract(metadata,'$.output_hash')=? ORDER BY rowid DESC LIMIT 1",
                (rid, task_id, digest(candidate)),
            ).fetchone()
            if row is None:
                return  # Mock providers and old caches may have no matching receipt.
            meta = json.loads(row["metadata"])
            meta["validation_state"] = "FAILED" if error else "PASSED"
            if error:
                meta["validation_error"] = {"code": error.code, "message": error.message}
            db.execute("UPDATE calls SET metadata=? WHERE id=?", (encode(meta), row["id"]))

    def calls(self, rid):
        with self.connection(read_only=True) as db:
            rows = db.execute(
                "SELECT * FROM calls WHERE run_id=? ORDER BY rowid", (rid,)
            ).fetchall()
        return [dict(r) | {"metadata": json.loads(r["metadata"])} for r in rows]

    def recover(self):
        for run in self.list("run", 10000):
            if (
                run.get("workflow_version") == WORKFLOW_VERSION
                and run["state"] == "RUNNING"
            ):
                self.change(
                    run["id"],
                    lambda r: r.update(
                        state="PAUSED",
                        error={
                            "code": "INTERRUPTED",
                            "message": "服务已重启，可以恢复",
                        },
                    ),
                )

    def directory(self, rid):
        if len(rid) != 32 or any(c not in "0123456789abcdef" for c in rid):
            raise WorkflowError("INVALID_PATH", "运行 ID 无效")
        version = self.get("run", rid).get("workflow_version")
        return self.root / "runs" / rid / ("v6" if version == "0.6" else "v7")
