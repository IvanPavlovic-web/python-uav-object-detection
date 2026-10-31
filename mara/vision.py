import os
import threading
import time
from typing import Callable, Dict, List, Optional, Tuple, Union

from .utils import ensure_dir, utc_timestamp

Detection = Dict[str, object]


class VisionError(RuntimeError):
    pass


class CameraStream:

    def __init__(self, source: Union[int, str] = 0, width: int = 640,
                 height: int = 480, max_frame_age_s: float = 2.0):
        self.source = source
        self.width = width
        self.height = height
        self.max_frame_age_s = max_frame_age_s
        self._cap = None
        self._frame = None
        self._frame_time = 0.0
        self._lock = threading.Lock()
        self._running = False
        self._thread: Optional[threading.Thread] = None

    def start(self) -> None:
        try:
            import cv2
        except ImportError as exc:
            raise VisionError("Paket 'opencv-python' nije instaliran.") from exc
        cap = cv2.VideoCapture(self.source)
        if not cap.isOpened():
            raise VisionError(f"Kamera/video nije dostupan: {self.source!r}")
        if isinstance(self.source, int):
            cap.set(cv2.CAP_PROP_FRAME_WIDTH, self.width)
            cap.set(cv2.CAP_PROP_FRAME_HEIGHT, self.height)
        ok, frame = cap.read()
        if not ok:
            cap.release()
            raise VisionError(f"Kamera ne vraća sliku: {self.source!r}")
        self._cap = cap
        self._is_file = not isinstance(self.source, int)
        fps = cap.get(cv2.CAP_PROP_FPS) if self._is_file else 0
        self._frame_delay = 1.0 / fps if fps and fps > 1 else 1.0 / 30
        self._store(frame)
        self._running = True
        self._thread = threading.Thread(target=self._reader, name="camera", daemon=True)
        self._thread.start()

    def _store(self, frame) -> None:
        with self._lock:
            self._frame = frame
            self._frame_time = time.monotonic()

    def _reader(self) -> None:
        import cv2
        while self._running:
            ok, frame = self._cap.read()
            if ok:
                self._store(frame)
                if self._is_file:
                    time.sleep(self._frame_delay)
            elif self._is_file:
                self._cap.set(cv2.CAP_PROP_POS_FRAMES, 0)
            else:
                time.sleep(0.05)

    def read(self):
        with self._lock:
            if self._frame is None:
                return None
            if time.monotonic() - self._frame_time > self.max_frame_age_s:
                return None
            return self._frame.copy()

    def stop(self) -> None:
        self._running = False
        if self._thread:
            self._thread.join(timeout=2)
        if self._cap is not None:
            self._cap.release()
            self._cap = None


class ObjectDetector:
    def __init__(self, model_path: str = "yolov8n.pt", min_confidence: float = 0.25,
                 image_size: int = 640, device: Optional[str] = None):
        try:
            from ultralytics import YOLO
        except ImportError as exc:
            raise VisionError("Paket 'ultralytics' nije instaliran.") from exc
        try:
            self.model = YOLO(model_path)
        except Exception as exc:
            raise VisionError(f"Model se ne može učitati ({model_path}): {exc}") from exc
        self.min_confidence = min_confidence
        self.image_size = image_size
        self.device = device

    def detect(self, frame) -> List[Detection]:
        results = self.model.predict(frame, conf=self.min_confidence, imgsz=self.image_size,
                                     device=self.device, verbose=False)
        detections: List[Detection] = []
        for result in results:
            names = result.names
            for box in result.boxes:
                class_id = int(box.cls[0])
                x1, y1, x2, y2 = (float(v) for v in box.xyxy[0].tolist())
                detections.append({
                    "class": names[class_id],
                    "confidence": round(float(box.conf[0]), 4),
                    "bbox": [round(x1, 1), round(y1, 1), round(x2, 1), round(y2, 1)],
                })
        return detections


class ScriptedDetector:

    def __init__(self, target: str = "person", appear_after_s: float = 15.0,
                 confidence: float = 0.90, clock: Callable[[], float] = time.monotonic):
        self.target = target
        self.appear_after_s = appear_after_s
        self.confidence = confidence
        self._clock = clock
        self._start = clock()

    def detect(self, frame) -> List[Detection]:
        if self._clock() - self._start >= self.appear_after_s:
            return [{"class": self.target, "confidence": self.confidence,
                     "bbox": [100.0, 80.0, 220.0, 360.0]}]
        return []


class SyntheticFrameSource:

    def __init__(self, width: int = 640, height: int = 480):
        self.width, self.height = width, height

    def start(self) -> None:
        pass

    def read(self):
        try:
            import numpy as np
        except ImportError:
            return object()
        return np.zeros((self.height, self.width, 3), dtype="uint8")

    def stop(self) -> None:
        pass


class VisionPipeline:

    def __init__(self, source, detector, min_interval_s: float = 0.3,
                 clock: Callable[[], float] = time.monotonic):
        self.source = source
        self.detector = detector
        self.min_interval_s = min_interval_s
        self._clock = clock
        self._last_run = -1e9
        self.frames_processed = 0

    def start(self) -> None:
        self.source.start()

    def stop(self) -> None:
        self.source.stop()

    def poll(self) -> Optional[Tuple[object, List[Detection]]]:
        now = self._clock()
        if now - self._last_run < self.min_interval_s:
            return None
        self._last_run = now
        frame = self.source.read()
        if frame is None:
            return None
        detections = self.detector.detect(frame)
        self.frames_processed += 1
        return frame, detections


def save_snapshot(frame, detection: Detection, directory: str = "logs") -> Optional[str]:
    try:
        import cv2
        ensure_dir(directory)
        image = frame.copy()
        x1, y1, x2, y2 = (int(v) for v in detection.get("bbox", [0, 0, 0, 0]))
        label = f"{detection['class']} {float(detection['confidence']):.2f}"
        cv2.rectangle(image, (x1, y1), (x2, y2), (0, 255, 0), 2)
        cv2.putText(image, label, (x1, max(15, y1 - 6)), cv2.FONT_HERSHEY_SIMPLEX,
                    0.6, (0, 255, 0), 2)
        stamp = utc_timestamp().replace(":", "-").replace("+", "_")
        path = os.path.join(directory, f"detection_{stamp}.jpg")
        return path if cv2.imwrite(path, image) else None
    except Exception as exc:
        print(f"[VISION] Snimak nije sačuvan: {exc}")
        return None
