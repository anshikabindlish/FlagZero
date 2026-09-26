"""Every tunable number in FlagZero lives here. Change values here, not in code."""
from pathlib import Path

PACKAGE_DIR = Path(__file__).resolve().parent
TRACK_FILE = PACKAGE_DIR / "track.json"
WEB_DIR = PACKAGE_DIR / "web"

# ---------------------------------------------------------------- server
SERVER_TICK_HZ = 20          # simulation + router loop rate
DASH_BROADCAST_HZ = 10       # "state" messages to /ws/dash
PHONE_PING_INTERVAL_S = 1.0  # phones show NO LINK after 3 s without a ping

# ---------------------------------------------------------------- simulation
SIM_NUM_CARS = 20
SIM_CAR_NUMBERS = [1, 3, 4, 5, 7, 8, 10, 11, 14, 16, 17, 18, 21, 22, 23, 27, 31, 33, 44, 55]
SIM_PHONE_CARS = [17, 21]        # ordinary sim cars that also have a real phone
SIM_TOP_SPEED_KMH = 250.0
SIM_ACCEL_G = 0.6                # acceleration out of corners
SIM_BRAKE_G = 2.0                # braking into corners (race-car braking)
SIM_SPACING_S = (1.0, 3.0)       # random gap between consecutive cars at start
SIM_START_M = 3900.0             # where the leading car starts
SIM_SPEED_JITTER = 0.03          # +/- 3 % per-car pace variation
SIM_SEED = None                  # set an int for a repeatable start

# ---------------------------------------------------------------- camera override
CAMERA_OVERRIDE_TIMEOUT_S = 0.5  # sim takes over again if vision stops seeing the car

# ---------------------------------------------------------------- router (used from A2)
ROUTER_MAX_UPSTREAM_M = 2000.0
ROUTER_T_REACT_S = 1.0
ROUTER_V_SAFE_KMH = 80.0
ROUTER_BRAKE_G = 1.2
ROUTER_MARGIN_M = 100.0
ROUTER_ETA_MAX_LEVEL_S = 6.0
ROUTER_ETA_YELLOW_S = 20.0
ROUTER_ETA_CAUTION_S = 40.0
ROUTER_RESEND_S = 0.25

# ---------------------------------------------------------------- association / fusion (A3)
ASSOC_MAX_DIST_M = 50.0
ASSOC_MAX_DT_S = 5.0
LOW_CONF = 0.6

# ---------------------------------------------------------------- medical (A4)
COUNTDOWN_S = 10
HR_REST = (70, 80)
HR_IMPACT = (135, 145)
HR_RAMP_S = 5.0
SPO2 = (96, 98)

G = 9.81
