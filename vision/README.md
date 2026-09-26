# /vision — Person B

Owns: the OpenCV pipeline reading the overhead Continuity Camera feed of
the paper track and turning what it sees into camera-origin Incidents.

## Stub: `stub_detector.py`

Run from the **repo root**:

```
pip install -r vision/requirements.txt
python -m vision.stub_detector
```

Reads `sample_frame.jpg` (a synthetic stand-in for a real camera frame —
regenerate it any time with `python -m vision.generate_sample_frame`),
finds the reddest blob in it with a deliberately trivial HSV threshold +
contour search, and prints an Incident JSON built via
`shared.schemas.new_incident(...)` — the exact shape /core and /dashboard
already expect.

## Replacing the stub

1. Swap `cv2.imread(SAMPLE_IMAGE)` for a live frame from the Continuity
   Camera (on macOS it shows up as a normal `cv2.VideoCapture(index)`
   source once your iPhone is signed into the same Apple ID and nearby —
   iterate on `index` to find it).
2. Replace the red-blob threshold with real centre-line-deviation /
   stillness / rollover-orientation detection.
3. Uncomment the send block at the bottom of `main()` to push detections
   into the running `/core` stub over its WebSocket for an end-to-end
   test, instead of just printing them.

Keep using `shared.track_config.zone_position_m(...)` to resolve zone names
to `track_position_m` — don't hardcode `1423`.
