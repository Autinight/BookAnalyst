"""SQLite command/usage ledger plus immutable, content-hashed stage artifacts."""
import copy
import hashlib
import json
import os
from pathlib import Path
import sqlite3
import threading
import time
import uuid
from contextlib import contextmanager

from .models import STAGES

TRANSITIONS = {
    "PENDING": {"RUNNING", "STALE"},
    "RUNNING": {"PASSED", "FAILED", "NEEDS_REVIEW", "RETRY_WAIT", "STALE"},
    "RETRY_WAIT": {"RUNNING", "FAILED", "STALE"},
    "PASSED": {"STALE"},
    "FAILED": {"PENDING", "STALE"},
    "NEEDS_REVIEW": {"PENDING", "FAILED", "STALE"},
    "STALE": {"PENDING"},
}


class WorkflowError(Exception):
    def __init__(self, code, message, status=409, review=False):
        super().__init__(message)
        self.code, self.message, self.status, self.review = code, message, status, review


def encode(value):
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def digest(value):
    return hashlib.sha256(value if isinstance(value, bytes) else encode(value).encode()).hexdigest()


def file_hash(path):
    with Path(path).open("rb") as stream:
        return hashlib.file_digest(stream, "sha256").hexdigest()


def atomic_json(path, value):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name("." + uuid.uuid4().hex + ".tmp")
    with temporary.open("w", encoding="utf-8", newline="\n") as stream:
        stream.write(encode(value) + "\n")
        stream.flush()
        os.fsync(stream.fileno())
    os.replace(temporary, path)


class Store:
    def __init__(self, root):
        self.root = Path(root).resolve()
        self.root.mkdir(parents=True, exist_ok=True)
        self.lock = threading.RLock()
        self.db = self.root / "bookanalyst.sqlite3"
        with self.connection() as db:
            db.executescript("""
                PRAGMA journal_mode=WAL;
                CREATE TABLE IF NOT EXISTS objects (
                    kind TEXT NOT NULL, id TEXT NOT NULL, payload TEXT NOT NULL,
                    PRIMARY KEY(kind,id));
                CREATE TABLE IF NOT EXISTS operations (
                    run_id TEXT, op_id TEXT, payload TEXT, PRIMARY KEY(run_id,op_id));
                CREATE TABLE IF NOT EXISTS calls (
                    id TEXT PRIMARY KEY, run_id TEXT, revision INTEGER,
                    kind TEXT, units INTEGER, state TEXT, metadata TEXT);
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
            row = db.execute("SELECT payload FROM objects WHERE kind=? AND id=?", (kind, key)).fetchone()
            if row is None:
                raise WorkflowError("NOT_FOUND", "记录不存在", 404)
            return json.loads(row[0])

    def list(self, kind):
        with self.connection() as db:
            return [json.loads(r[0]) for r in db.execute(
                "SELECT payload FROM objects WHERE kind=? ORDER BY rowid DESC", (kind,))]

    def put(self, kind, key, value):
        with self.connection() as db:
            db.execute("INSERT OR REPLACE INTO objects VALUES (?,?,?)", (kind, key, encode(value)))
        return value

    def create_run(self, config, book, parser_settings):
        key = uuid.uuid4().hex
        scope = "fixture" if config["profile"] == "offline_fixture" else (
            "full" if config["profile"] == "book" else "sample")
        run = dict(id=key, revision=1, created_at=time.time(), updated_at=time.time(),
                   state="PENDING", workflow_version="0.5", scope=scope, config=config, source=book,
                   parser_settings=parser_settings, stages={s: {"state": "PENDING"} for s in STAGES},
                   usage={"llm": 0, "parse": 0, "pages": 0}, findings=[], corrections=[],
                   events=[], repair_counts={}, result=None)
        return self.put("run", key, run)

    def change(self, run_id, callback, revision=None):
        with self.connection() as db:
            db.execute("BEGIN IMMEDIATE")
            row = db.execute("SELECT payload FROM objects WHERE kind='run' AND id=?", (run_id,)).fetchone()
            if not row:
                raise WorkflowError("NOT_FOUND", "运行不存在", 404)
            run = json.loads(row[0])
            if revision is not None and run["revision"] != revision:
                raise WorkflowError("STALE_REVISION", "运行已有新修订，请刷新后查看")
            result = callback(run)
            run["updated_at"] = time.time()
            db.execute("UPDATE objects SET payload=? WHERE kind='run' AND id=?", (encode(run), run_id))
            return copy.deepcopy(result if result is not None else run)

    def operation(self, run_id, revision, op_id, callback):
        with self.connection() as db:
            db.execute("BEGIN IMMEDIATE")
            existing = db.execute("SELECT payload FROM operations WHERE run_id=? AND op_id=?",
                                  (run_id, op_id)).fetchone()
            if existing:
                return json.loads(existing[0])
            row = db.execute("SELECT payload FROM objects WHERE kind='run' AND id=?", (run_id,)).fetchone()
            if not row:
                raise WorkflowError("NOT_FOUND", "运行不存在", 404)
            run = json.loads(row[0])
            if revision != run["revision"]:
                raise WorkflowError("STALE_REVISION", "运行已有新修订，请刷新后操作")
            callback(run)
            run["updated_at"] = time.time()
            db.execute("UPDATE objects SET payload=? WHERE kind='run' AND id=?", (encode(run), run_id))
            db.execute("INSERT INTO operations VALUES (?,?,?)", (run_id, op_id, encode(run)))
            return run

    def transition(self, run_id, revision, stage, state, **details):
        def apply(run):
            current = run["stages"][stage]["state"]
            if state not in TRANSITIONS[current]:
                raise WorkflowError("INVALID_TRANSITION", f"{stage}: {current} → {state} 不合法")
            if state == "RUNNING" and any(run["stages"][s]["state"] != "PASSED"
                                          for s in STAGES[:STAGES.index(stage)]):
                raise WorkflowError("PRECONDITION", "前置阶段尚未通过")
            run["stages"][stage].update(state=state, **details)
            run["events"].append(dict(time=time.time(), stage=stage, state=state))
        return self.change(run_id, apply, revision)

    def reserve(self, run_id, revision, kind, units, metadata):
        with self.connection() as db:
            db.execute("BEGIN IMMEDIATE")
            row = db.execute("SELECT payload FROM objects WHERE kind='run' AND id=?", (run_id,)).fetchone()
            run = json.loads(row[0])
            if run["revision"] != revision:
                raise WorkflowError("STALE_REVISION", "旧修订不能发起请求")
            if run["config"]["profile"] == "offline_fixture":
                raise WorkflowError("NETWORK_DISABLED", "离线夹具禁止真实调用")
            usage, config = run["usage"], run["config"]
            if kind == "llm" and config["max_llm_requests"] is not None and usage["llm"] + 1 > config["max_llm_requests"]:
                raise WorkflowError("BUDGET_EXHAUSTED", "LLM 请求预算已耗尽")
            if kind == "parse" and (usage["parse"] + 1 > config["max_parse_submissions"]
                                   or usage["pages"] + units > config["max_submitted_pages"]):
                raise WorkflowError("BUDGET_EXHAUSTED", "解析提交或累计页数预算已耗尽")
            usage[kind] += 1
            if kind == "parse":
                usage["pages"] += units
            key = uuid.uuid4().hex
            db.execute("INSERT INTO calls VALUES (?,?,?,?,?,?,?)",
                       (key, run_id, revision, kind, units, "RESERVED", encode(metadata)))
            db.execute("UPDATE objects SET payload=? WHERE kind='run' AND id=?", (encode(run), run_id))
            return key

    def finish_call(self, key, state, **metadata):
        with self.connection() as db:
            row = db.execute("SELECT metadata FROM calls WHERE id=?", (key,)).fetchone()
            combined = json.loads(row[0]) | metadata
            db.execute("UPDATE calls SET state=?,metadata=? WHERE id=?", (state, encode(combined), key))

    def calls(self, run_id):
        with self.connection() as db:
            return [dict(r) | {"metadata": json.loads(r["metadata"])} for r in
                    db.execute("SELECT * FROM calls WHERE run_id=? ORDER BY rowid", (run_id,))]

    def directory(self, run_id, revision, stage):
        if not all(c in "0123456789abcdef" for c in run_id) or len(run_id) != 32 or stage not in STAGES:
            raise WorkflowError("INVALID_PATH", "无效的产物路径")
        return self.root / "runs" / run_id / f"r{revision}" / stage

    def commit(self, run_id, revision, stage, outputs, gates, input_hashes):
        directory = self.directory(run_id, revision, stage)
        directory.mkdir(parents=True, exist_ok=True)
        hashes = {}
        for name, content in outputs.items():
            path = directory / name
            path.parent.mkdir(parents=True, exist_ok=True)
            if path.exists():
                raise WorkflowError("IMMUTABLE_ARTIFACT", "产物已经存在，必须创建新修订")
            if isinstance(content, (dict, list)):
                atomic_json(path, content)
            elif isinstance(content, bytes):
                path.write_bytes(content)
            else:
                path.write_text(content, encoding="utf-8", newline="\n")
            hashes[name] = file_hash(path)
        manifest = dict(schema_version="0.5", run_id=run_id, revision=revision, stage=stage,
                        producer_version="0.5.0", input_hashes=input_hashes, files=hashes,
                        gate_results={g: "PASSED" for g in gates})
        atomic_json(directory / "manifest.json", manifest)
        self.transition(run_id, revision, stage, "PASSED", artifact_revision=revision,
                        manifest_hash=digest(manifest), gates=manifest["gate_results"])
        return manifest

    def artifact_dir(self, run, stage):
        state = run["stages"][stage]
        if state["state"] != "PASSED":
            raise WorkflowError("PRECONDITION", f"{stage} 没有已通过产物")
        directory = self.directory(run["id"], state["artifact_revision"], stage)
        manifest = json.loads((directory / "manifest.json").read_text(encoding="utf-8"))
        if digest(manifest) != state["manifest_hash"]:
            raise WorkflowError("HASH_MISMATCH", "产物清单已变化")
        for name, sha in manifest["files"].items():
            if file_hash(directory / name) != sha:
                raise WorkflowError("HASH_MISMATCH", f"产物校验失败: {name}")
        return directory

    def artifact(self, run, stage, name):
        path = self.artifact_dir(run, stage) / name
        return json.loads(path.read_text(encoding="utf-8"))

    def recover(self):
        for run in self.list("run"):
            if run["state"] != "RUNNING":
                continue
            def apply(r):
                r["state"] = "NEEDS_REVIEW"
                for stage in r["stages"].values():
                    if stage["state"] == "RUNNING":
                        stage.update(state="NEEDS_REVIEW", error={
                            "code": "INTERRUPTED", "message": "执行器中断，请恢复运行；远端结果未知时不会重复提交"})
            self.change(run["id"], apply)
