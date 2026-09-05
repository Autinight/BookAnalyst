"""Validated user commands. Workflow configuration contains references, never secrets."""
from typing import Literal
from pydantic import BaseModel, ConfigDict, Field, model_validator

STAGES = [f"S{i}" for i in range(9)]
ROLES = ("analyst", "converter", "reviewer", "visual_reviewer")


class StrictModel(BaseModel):
    model_config = ConfigDict(extra="forbid")


class Binding(StrictModel):
    connection_id: str = "openai_subscription"
    model_id: str = ""
    context_limit: int = Field(32768, ge=4096, le=2_000_000)
    output_tokens: int = Field(4096, ge=256, le=100_000)


class RunCreate(StrictModel):
    book_id: str
    profile: Literal["cloud_smoke", "local_smoke", "offline_fixture", "llm_smoke", "book"] = "cloud_smoke"
    start_page: int = Field(1, ge=1)
    end_page: int = Field(1, ge=1)
    parser: Literal["cloud", "local"] = "cloud"
    llm_concurrency: int = Field(2, ge=1)
    max_llm_requests: int | None = Field(8, ge=0)
    max_parse_submissions: int = Field(1, ge=0)
    max_submitted_pages: int = Field(3, ge=0)
    visual_mode: Literal["targeted", "sampled", "full", "disabled"] = "targeted"
    visual_pages: list[int] = Field(default_factory=list)
    stop_after: Literal["S1", "S5"] | None = None
    cached_run_id: str | None = None
    analyst: Binding = Field(default_factory=Binding)
    converter: Binding = Field(default_factory=Binding)
    reviewer: Binding = Field(default_factory=Binding)
    visual_reviewer: Binding = Field(default_factory=Binding)

    @model_validator(mode="after")
    def limits(self):
        if self.end_page < self.start_page:
            raise ValueError("结束页不能小于开始页")
        count = self.end_page - self.start_page + 1
        if self.profile in ("cloud_smoke", "llm_smoke") and count > 3:
            raise ValueError("该测试模式最多 3 页")
        if self.profile == "local_smoke" and count > 1:
            raise ValueError("本地最小测试限 1 页")
        if self.profile == "llm_smoke" and (not self.cached_run_id or (self.max_llm_requests is None or self.max_llm_requests > 8)):
            raise ValueError("LLM 测试须选择已解析运行，且最多 8 次请求")
        if self.profile == "book" and (self.max_llm_requests == 0 or self.max_submitted_pages < count):
            raise ValueError("全书模式必须填写完整处理预算")
        if any(p < self.start_page or p > self.end_page for p in self.visual_pages):
            raise ValueError("核对页必须属于运行范围")
        return self


class Operation(StrictModel):
    revision: int = Field(ge=1)
    operation_id: str = Field(min_length=8, max_length=100)


class Rerun(Operation):
    stage: Literal["S0", "S1", "S2", "S3", "S4", "S5", "S6", "S7", "S8"]
    task_id: str | None = None
    force_recompute: bool = False
    reason: str = Field(min_length=1, max_length=2000)
    additional_visual_pages: list[int] = Field(default_factory=list)
    max_llm_requests: int | None = Field(None, ge=0)
    max_parse_submissions: int | None = Field(None, ge=0)
    max_submitted_pages: int | None = Field(None, ge=0)


class Correction(Operation):
    atom_id: str
    before: str
    after: str
    evidence: str = Field(min_length=3)
    page_idx: int = Field(ge=0)
