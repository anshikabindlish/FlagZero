"""FlagZero vision - every tunable number for the camera side lives here.

Tune these at the venue. Times are seconds unless the name says _ms.
Distances ending in _m are TRACK metres along the lap of flagzero/track.json
(the paper strip = TRACK_START_M..TRACK_END_M).
"""
import json
from pathlib import Path

# ---------------------------------------------------------------- paths
PKG_DIR = Path(__file__).resolve().parents[1]          # flagzero/
VISION_DIR = PKG_DIR / "vision"
CALIB_PATH = VISION_DIR / "vision_calib.json"
FRAMES_DIR = VISION_DIR / "frames"
RESULTS_DIR = PKG_DIR / "results"
RECORDINGS_DIR = PKG_DIR / "recordings"
MARKERS_DIR = VISION_DIR / "markers"

# ---------------------------------------------------------------- server
SERVER_WS_URL = "ws://localhost:8000/ws/vision"
SEND_HZ = 10.0
CAMERA_ID = 1
RECONNECT_MIN_S = 0.5
RECONNECT_MAX_S = 5.0

# ---------------------------------------------------------------- track window
# The paper strip represents this stretch of the lap. No corner is special: pick any
# stretch (calibrate.py maps the drawn centre line linearly onto it).
TRACK_START_M = 1380.0
TRACK_END_M = 1480.0
PAPER_MARK_M = 1423.0        # a reference mark on the paper: drawn in the preview, crash spot in the test clip
try:                         # the real lap (Interlagos, 4,247 m) from the server's track file
    LAP_LEN_M = float(json.loads((PKG_DIR / "track.json").read_text(encoding="utf-8"))["lap_length_m"])
except (OSError, KeyError, ValueError):
    LAP_LEN_M = 4247.0

# ---------------------------------------------------------------- car identity
# ArUco dictionary + marker id -> car number. Default: marker id == car number.
ARUCO_DICT = "DICT_4X4_50"
MARKER_TO_CAR = {17: 17, 8: 8, 21: 21}
# Colour fallback (sticky notes). HSV ranges get overwritten by calibrate.py.
COLOR_CARS = {
    17: {"name": "pink", "lo": [150, 70, 90], "hi": [179, 255, 255]},
    8: {"name": "blue", "lo": [95, 80, 60], "hi": [125, 255, 255]},
}
COLOR_MIN_AREA_PX = 250
NOSE_DOT_MAX_V = 90          # a dark marker dot on the note = the car's nose

# ---------------------------------------------------------------- smoothing
POS_EMA = 0.5                # 0..1, higher = trust the new frame more
SPEED_EMA = 0.35
YAW_EMA = 0.4
LOST_TIMEOUT_S = 1.5         # drop a car from the list after this long unseen

# Display-speed scale. 1.0 = literal scale of the paper track.
SPEED_DISPLAY_SCALE = 1.0

# ---------------------------------------------------------------- stationary
STILL_PX = 3.0               # centroid moves less than this ...
STILL_WINDOW_S = 1.0         # ... over this window -> car is still
STOPPED_RAISE_S = 2.0        # raise STOPPED_VEHICLE after this long still
REQUIRE_MOVE_BEFORE_STOP = True   # ignore cars parked since start/reset
ARM_MOVE_M = 2.0             # a car must travel this far (track m) to be armed

# ---------------------------------------------------------------- predictive
MOVING_KMH = 15.0            # below this a car counts as crawling/stopped
SPIN_YAW_DPS = 120.0         # sustained yaw rate that means "rotating"
SPIN_HEADING_ERR_DEG = 40.0  # nose this far off the track direction while moving
SPIN_CONFIRM_S = 0.15
OFF_TRACK_LATERAL = 1.0      # 1.0 = at the corridor edge
OFF_TRACK_CONFIRM_S = 0.2
LOOKAHEAD_S = 0.5            # trajectory extrapolation horizon
PREDICT_OFF_LATERAL = 1.15   # predicted point this far out -> predicted OFF_TRACK
SLOWING_DECEL_KMH_S = 250.0  # sharp speed drop (km/h per s) while on track
SLOWING_MIN_FROM_KMH = 60.0
CLOSING_TTC_S = 1.0          # time for follower to reach leader
CLOSING_MAX_GAP_M = 25.0
HAZARD_HOLD_S = 1.5          # predicted hazards stay up this long after last true

# ---------------------------------------------------------------- multi stop
MULTI_STOP_ENABLED = True
MULTI_STOP_GAP_M = 30.0
MULTI_STOP_WINDOW_S = 5.0

# ---------------------------------------------------------------- hand / occlusion
HAND_MIN_AREA_FRAC = 0.04    # moving blob > 4 % of frame = a hand
HAND_CLEAR_S = 0.4           # stay occluded this long after the hand leaves
MOG2_HISTORY = 300
MOG2_VAR_THRESHOLD = 32

# ---------------------------------------------------------------- debris
DEBRIS_DIFF_THRESHOLD = 28   # grey-level difference vs clean reference
DEBRIS_MIN_AREA_PX = 150
DEBRIS_MAX_AREA_PX = 12000
DEBRIS_PERSIST_S = 2.0
DEBRIS_MATCH_PX = 25         # same blob if centroid within this many px
DEBRIS_CORRIDOR_MARGIN = 1.3 # accept blobs slightly outside the corridor
DEBRIS_CAR_MASK_PAD_PX = 18  # blank out car markers before diffing
REFERENCE_BLEND = 0.02       # slow background adaptation where nothing is found

# ---------------------------------------------------------------- confidence
CONF_MAX = 0.99
