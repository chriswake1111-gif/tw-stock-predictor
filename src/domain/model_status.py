from enum import Enum


class RuleUseStatus(str, Enum):
    AVAILABLE = "available"
    NEEDS_HUMAN_INPUT = "needs_human_input"
    FORBIDDEN = "forbidden"
    UNSUPPORTED = "unsupported"


LEGACY_V1_MODEL_METADATA = {
    "model_version": "1.x",
    "legacy": True,
    "official_affiliation": False,
}
