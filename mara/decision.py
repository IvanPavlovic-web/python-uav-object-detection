import time
from dataclasses import dataclass
from typing import Callable, Dict, List, Optional

from .config import Mission


@dataclass(frozen=True)
class Decision:
    detection: Dict[str, object]
    action: str
    stop_mission: bool
    frames_confirmed: int


class DecisionEngine:

    def __init__(self, target_object: str, confidence_threshold: float,
                 required_frames: int = 3, action: str = "LOITER",
                 stop_mission: bool = True, cooldown_s: float = 30.0,
                 clock: Callable[[], float] = time.monotonic):
        self.target_object = target_object.strip().lower()
        self.confidence_threshold = confidence_threshold
        self.required_frames = max(1, required_frames)
        self.action = action
        self.stop_mission = stop_mission
        self.cooldown_s = cooldown_s
        self._clock = clock
        self._streak = 0
        self._blocked_until = 0.0

    @classmethod
    def from_mission(cls, mission: Mission) -> "DecisionEngine":
        return cls(mission.target_object, mission.confidence_threshold,
                   mission.required_consecutive_frames, mission.detection_action,
                   mission.stop_on_detection, mission.detection_cooldown_seconds)

    def best_match(self, detections: List[Dict[str, object]]) -> Optional[Dict[str, object]]:
        candidates = [d for d in detections
                      if str(d["class"]).lower() == self.target_object
                      and float(d["confidence"]) >= self.confidence_threshold]
        return max(candidates, key=lambda d: d["confidence"]) if candidates else None

    def evaluate(self, detections: List[Dict[str, object]]) -> Optional[Decision]:
        if self._clock() < self._blocked_until:
            self._streak = 0
            return None
        match = self.best_match(detections)
        if match is None:
            self._streak = 0
            return None
        self._streak += 1
        if self._streak < self.required_frames:
            return None
        confirmed = self._streak
        self._streak = 0
        self._blocked_until = self._clock() + self.cooldown_s
        return Decision(match, self.action, self.stop_mission, confirmed)

    def reset(self) -> None:
        self._streak = 0
