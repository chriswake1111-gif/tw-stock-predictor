from dataclasses import dataclass
from enum import Enum
from typing import Any, Mapping, Tuple


class EvidenceLevel(str, Enum):
    A = "A"
    B = "B"
    C = "C"
    U = "U"


class ImplementationMode(str, Enum):
    VERIFIED_CORE = "verified_core"
    PARAMETERIZED_SUPPORT = "parameterized_support"
    PROJECT_OPERATIONALIZATION = "project_operationalization"
    LEGACY_EXPERIMENTAL = "legacy_experimental"
    UNSUPPORTED = "unsupported"


@dataclass(frozen=True)
class EvidenceRule:
    rule_id: str
    title: str
    evidence_level: EvidenceLevel
    implementation_mode: ImplementationMode
    human_approval_required: bool
    source_refs: Tuple[Mapping[str, str], ...]
    allowed_outputs: Tuple[str, ...]
    forbidden_uses: Tuple[str, ...]
    version: str
    project_operationalization: bool = False

    @classmethod
    def from_mapping(cls, rule_id: str, raw: Mapping[str, Any]) -> "EvidenceRule":
        rule = cls(
            rule_id=rule_id,
            title=str(raw["title"]),
            evidence_level=EvidenceLevel(raw["evidence_level"]),
            implementation_mode=ImplementationMode(raw["implementation_mode"]),
            human_approval_required=bool(raw.get("human_approval_required", False)),
            source_refs=tuple(raw.get("source_refs", ())),
            allowed_outputs=tuple(raw.get("allowed_outputs", ())),
            forbidden_uses=tuple(raw.get("forbidden_uses", ())),
            version=str(raw["version"]),
            project_operationalization=bool(raw.get("project_operationalization", False)),
        )
        rule.validate()
        return rule

    def validate(self) -> None:
        if self.evidence_level is EvidenceLevel.U and self.implementation_mode is ImplementationMode.VERIFIED_CORE:
            raise ValueError(f"{self.rule_id}: U evidence cannot be registered as verified_core")
        if self.implementation_mode is ImplementationMode.VERIFIED_CORE and self.evidence_level is not EvidenceLevel.A:
            raise ValueError(f"{self.rule_id}: verified_core requires A-level evidence")
        if self.evidence_level is EvidenceLevel.C and not self.project_operationalization:
            raise ValueError(f"{self.rule_id}: C-level evidence must be marked project_operationalization")
        if not self.allowed_outputs:
            raise ValueError(f"{self.rule_id}: allowed_outputs must not be empty")
        if not self.version:
            raise ValueError(f"{self.rule_id}: version is required")

    def to_dict(self) -> dict[str, Any]:
        return {
            "rule_id": self.rule_id,
            "title": self.title,
            "evidence_level": self.evidence_level.value,
            "implementation_mode": self.implementation_mode.value,
            "human_approval_required": self.human_approval_required,
            "source_refs": list(self.source_refs),
            "allowed_outputs": list(self.allowed_outputs),
            "forbidden_uses": list(self.forbidden_uses),
            "version": self.version,
            "project_operationalization": self.project_operationalization,
        }
