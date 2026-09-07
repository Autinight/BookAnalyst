"""Small SQLite records; TeX, page images and model responses live in files."""

import copy, hashlib, json, os, sqlite3, threading, time, uuid
from pathlib import Path
from contextlib import contextmanager
from .models import STAGES


class WorkflowError(Exception):
    def __init__(self, code, message, status=409, review=False):
        super().__init__(message)
        self.code, self.message, self.status, self.review = (
            code,
            message,
            status,
            review,
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


class Store:
    def __init__(self, root):
        self.root = Path(root).resolve()
        self.root.mkdir(parents=True, exist_ok=True)
        self.db = self.root / "bookanalyst.sqlite3"
        self.lock = threading.RLock()
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
    def connection(self):
        with self.lock:
            db = sqlite3.connect(self.db, timeout=30)
            db.row_factory = sqlite3.Row
            try:
                with db:
                    yield db
            finally:
                db.close()

    def get(self, kind, key):
        with self.connection() as db:
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

    def list(self, kind, limit=100):
        with self.connection() as db:
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
            workflow_version="0.6",
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

    def operation(self, rid, command, callback):
        with self.connection() as db:
            db.execute("BEGIN IMMEDIATE")
            row = db.execute(
                "SELECT payload FROM operations WHERE run_id=? AND op_id=?",
                (rid, command.operation_id),
            ).fetchone()
            if row:
                return json.loads(row[0])
            result = self._change(db, rid, callback, command.revision)
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
        with self.connection() as db:
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
        old = run.get("workflow_version") != "0.6"
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
            )
        } | {
            "title": run["source"]["title"],
            "book_id": run["source"]["id"],
            "legacy": old,
            "pages": [run["config"]["start_page"], run["config"]["end_page"]],
            "stage": run.get("stage"),
            "stages": run.get("stages") if not old else {},
            "pages_per_task": run["config"].get("pages_per_task"),
            "concurrency": run["config"]["llm_concurrency"],
        }

    def reserve(self, rid, revision, kind, units, metadata):
        key = uuid.uuid4().hex
        with self.connection() as db:
            db.execute("BEGIN IMMEDIATE")
            self._change(
                db,
                rid,
                lambda r: r["usage"].update(llm=r["usage"].get("llm", 0) + 1),
                revision,
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

    def calls(self, rid):
        with self.connection() as db:
            rows = db.execute(
                "SELECT * FROM calls WHERE run_id=? ORDER BY rowid", (rid,)
            ).fetchall()
        return [dict(r) | {"metadata": json.loads(r["metadata"])} for r in rows]

    def recover(self):
        for run in self.list("run", 10000):
            if run.get("workflow_version") == "0.6" and run["state"] == "RUNNING":
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
        return self.root / "runs" / rid / "v6"
