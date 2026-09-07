"""Commands and the five-stage public workflow."""

from typing import Literal
from pydantic import BaseModel, ConfigDict, Field, model_validator

WORKFLOW_VERSION = "0.7"
STAGES = ["setup", "style", "convert", "seams", "headings"]
STAGE_NAMES = [
    "全书设置与计数规则",
    "固定公共 TeX 样式",
    "分批视觉转换",
    "页面衔接",
    "标题、标签与引用",
]


class StrictModel(BaseModel):
    model_config = ConfigDict(extra="forbid")


class Binding(StrictModel):
    connection_id: str = "openai_subscription"
    model_id: str = ""
    reasoning_effort: Literal["low", "medium", "high", "xhigh"] = "medium"


class RunCreate(StrictModel):
    book_id: str
    start_page: int = Field(1, ge=1)
    end_page: int = Field(ge=1)
    pages_per_task: int = Field(3, ge=1)
    llm_concurrency: int = Field(2, ge=1)
    setup_pages: list[int] = Field(default_factory=list)
    model: Binding = Field(default_factory=Binding)
    structure_effort: Literal["high", "xhigh"] = "xhigh"

    @model_validator(mode="after")
    def pages(self):
        if self.end_page < self.start_page:
            raise ValueError("结束页必须不小于起始页")
        return self


class Operation(StrictModel):
    revision: int
    operation_id: str = Field(min_length=8)
