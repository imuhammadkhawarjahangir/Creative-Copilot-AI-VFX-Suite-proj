from typing import Annotated, Literal, Optional, Any
from pydantic import BaseModel, ConfigDict, Field, StringConstraints

# Client-supplied strings that end up in logs and the UI are bounded, and can't carry control
# characters (e.g. newlines that would forge log lines).
ProjectId = Annotated[str, StringConstraints(pattern=r"^[A-Za-z0-9._-]{1,64}$")]
SuggestionId = Annotated[str, StringConstraints(pattern=r"^sug_[0-9a-f]{8}$")]
Label = Annotated[str, StringConstraints(pattern=r"^[^\x00-\x1f\x7f]{1,64}$")]

class HostInfo(BaseModel):
    name: Label
    version: Label

class ProjectInfo(BaseModel):
    id: ProjectId
    fps: float

class HelloMsg(BaseModel):
    type: Literal["hello"]
    host: HostInfo
    project: ProjectInfo

class SceneCtx(BaseModel):
    shot_id: str
    emotion: Optional[str] = None
    lighting: Optional[str] = None
    notes: Optional[str] = None

class Clip(BaseModel):
    id: str
    in_: float = Field(alias="in")
    out: float
    kind: Literal["video", "audio"]
    label: Optional[str] = None

class AudioInfo(BaseModel):
    bpm: Optional[float] = None
    beats: list[float] = []

class TimelineCtx(BaseModel):
    clips: list[Clip] = []
    cuts: list[float] = []
    audio: Optional[AudioInfo] = None

class ContextFrameMsg(BaseModel):
    type: Literal["context_frame"]
    ts: int
    playhead: float
    scene: SceneCtx
    timeline: TimelineCtx

class FeedbackMsg(BaseModel):
    type: Literal["feedback"]
    suggestion_id: SuggestionId
    verdict: Literal["accepted", "rejected", "modified"]
    project_id: ProjectId
    notes: Optional[Annotated[str, StringConstraints(max_length=1000)]] = None

class Action(BaseModel):
    op: str
    params: dict[str, Any] = {}

class SuggestionItem(BaseModel):
    id: str
    kind: Literal["color_grade", "transition", "keyframe", "vfx"]
    title: str
    confidence: float
    rationale: str
    action: Action

class SuggestionsMsg(BaseModel):
    type: Literal["suggestions"] = "suggestions"
    ts: int
    project_id: str = ""
    host: str = ""
    items: list[SuggestionItem]

class ProjectClosedMsg(BaseModel):
    type: Literal["project_closed"] = "project_closed"
    project_id: str

# --- Applying actions -------------------------------------------------------------------------

ActionId = Annotated[str, StringConstraints(pattern=r"^act_[0-9a-f]{12}$")]
PresetName = Annotated[str, StringConstraints(pattern=r"^[a-z0-9_]{1,64}$")]
Unit = Annotated[float, Field(ge=0.0, le=1.0)]
Seconds = Annotated[float, Field(ge=0.0)]

class BoundedParams(BaseModel):
    model_config = ConfigDict(extra="forbid")

class ColorGradeParams(BoundedParams):
    lut: PresetName
    contrast: Annotated[float, Field(ge=0.5, le=2.0)]
    sat: Annotated[float, Field(ge=0.0, le=2.0)]
    shadow_tint: Optional[Literal["teal", "warm"]] = None
    highlight_tint: Optional[Literal["teal", "warm"]] = None

class TransitionParams(BoundedParams):
    type: Literal["dip_to_black", "cross_dissolve"]
    frames: Annotated[int, Field(ge=1, le=48)]
    at: Seconds

class KeyframeParams(BoundedParams):
    time: Seconds
    target: Literal["next_keyframe"]

class VfxParams(BoundedParams):
    preset: PresetName
    threshold: Unit
    intensity: Unit
    radius: Unit

# The only operations a host is ever asked to perform, and the bounds on their params.
ACTION_PARAMS: dict[str, type[BoundedParams]] = {
    "apply_color_grade": ColorGradeParams,
    "apply_transition_at_cut": TransitionParams,
    "snap_keyframe_to_time": KeyframeParams,
    "apply_vfx_preset": VfxParams,
}

def checked_params(op: str, params: dict[str, Any]) -> dict[str, Any]:
    if op not in ACTION_PARAMS:
        raise ValueError(f"unknown action op {op!r}")
    return ACTION_PARAMS[op].model_validate(params).model_dump(exclude_none=True)

class ApplyMsg(BaseModel):  # UI -> service: names a suggestion; the action comes from the service
    type: Literal["apply"]
    project_id: ProjectId
    suggestion_id: SuggestionId

class ApplyActionMsg(BaseModel):  # service -> host
    type: Literal["apply_action"] = "apply_action"
    action_id: ActionId
    suggestion_id: SuggestionId
    op: str
    params: dict[str, Any]

class ActionResultMsg(BaseModel):  # host -> service
    type: Literal["action_result"]
    action_id: ActionId
    status: Literal["applied", "marked", "duplicate", "rejected"]
    detail: Optional[Annotated[str, StringConstraints(max_length=200)]] = None

class ActionOutcomeMsg(BaseModel):  # service -> UI; "failed" means the service never reached a host
    type: Literal["action_result"] = "action_result"
    project_id: str
    suggestion_id: str
    action_id: Optional[str] = None
    status: Literal["applied", "marked", "duplicate", "rejected", "failed"]
    detail: Optional[str] = None
