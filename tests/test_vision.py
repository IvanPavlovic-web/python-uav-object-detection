import os
import time

import pytest

cv2 = pytest.importorskip("cv2")
np = pytest.importorskip("numpy")

from mara.vision import (CameraStream, ScriptedDetector, SyntheticFrameSource,
                         VisionError, VisionPipeline, save_snapshot)


@pytest.fixture
def video_file(tmp_path):
    path = str(tmp_path / "test.avi")
    writer = cv2.VideoWriter(path, cv2.VideoWriter_fourcc(*"MJPG"), 10, (64, 48))
    for i in range(20):
        writer.write(np.full((48, 64, 3), i * 10, dtype="uint8"))
    writer.release()
    return path


def test_camera_stream_reads_and_loops_video_file(video_file):
    stream = CameraStream(video_file)
    stream.start()
    try:
        first = stream.read()
        assert first is not None and first.shape == (48, 64, 3)
        time.sleep(2.6)
        assert stream.read() is not None
    finally:
        stream.stop()


def test_camera_stream_missing_source_raises(tmp_path):
    with pytest.raises(VisionError):
        CameraStream(str(tmp_path / "nema.avi")).start()


def test_pipeline_respects_min_interval():
    now = [0.0]
    pipeline = VisionPipeline(SyntheticFrameSource(), ScriptedDetector("person", 0.0),
                              min_interval_s=1.0, clock=lambda: now[0])
    assert pipeline.poll() is not None
    assert pipeline.poll() is None
    now[0] = 1.5
    assert pipeline.poll() is not None
    assert pipeline.frames_processed == 2


def test_stale_frame_is_not_used():
    stream = CameraStream(0, max_frame_age_s=0.05)
    stream._frame = np.zeros((4, 4, 3), dtype="uint8")
    stream._frame_time = time.monotonic() - 1.0
    assert stream.read() is None


def test_save_snapshot_writes_image(tmp_path):
    frame = np.zeros((120, 160, 3), dtype="uint8")
    detection = {"class": "person", "confidence": 0.9, "bbox": [10, 10, 80, 100]}
    path = save_snapshot(frame, detection, str(tmp_path))
    assert path and os.path.exists(path)
    assert cv2.imread(path).shape == (120, 160, 3)


def test_scripted_detector_appears_after_delay():
    now = [0.0]
    detector = ScriptedDetector("person", 5.0, clock=lambda: now[0])
    assert detector.detect(None) == []
    now[0] = 5.0
    assert detector.detect(None)[0]["class"] == "person"
