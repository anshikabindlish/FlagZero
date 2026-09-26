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
ROUTER_FLAG_ZONE_M = 500.0      # inside this distance a car always sees the incident's flag (marshal-post sector)
ROUTER_ETA_MAX_LEVEL_S = 6.0
ROUTER_ETA_YELLOW_S = 20.0
ROUTER_ETA_CAUTION_S = 40.0
ROUTER_RESEND_S = 0.25

# ---------------------------------------------------------------- association / fusion (A3)
ASSOC_MAX_DIST_M = 50.0
ASSOC_MAX_DT_S = 5.0
LOW_CONF = 0.6
IMPACT_STRONG_G = 8.0            # IMPACT at or above this peak g counts as "strong"
STOPPING_CLASSES = ("IMPACT", "SEVERE", "ROLLOVER")   # these stop the car + start the countdown

# ---------------------------------------------------------------- warnings
WARNING_LABELS = {0: "NORMAL", 1: "CAUTION", 2: "YELLOW", 3: "DBL YELLOW", 4: "SLOW ZONE", 5: "RED"}
SEVERITY_LABELS = {0: "NORMAL", 1: "CAUTION", 2: "YELLOW", 3: "DOUBLE_YELLOW", 4: "RED_FLAG_RECOMMENDED"}
SEVERITY_MAX_WARNING = {1: 1, 2: 2, 3: 3, 4: 4}   # sev 3 -> DBL YELLOW, sev 4 -> SLOW ZONE
SPEED_CAP_KMH = {3: 120.0, 4: 80.0, 5: 60.0}       # sim cars obey DBL YELLOW, SLOW ZONE and RED

# ---------------------------------------------------------------- medical (A4)
COUNTDOWN_S = 15                 # driver has 15 s to press I'm OK
COUNTDOWN_SERVER_GRACE_S = 3     # server declares TIMEOUT itself if the phone never answers
AUTO_RED_ON_TIMEOUT = True       # no OK in time -> RED goes out automatically (no click needed)
HR_REST = (70, 80)
HR_IMPACT = (135, 145)
HR_RAMP_S = 5.0
SPO2 = (96, 98)

# ---------------------------------------------------------------- demo scenes (A4)
SCENE_CAR21_BEHIND_S = 9.0       # a scene lines car 21 up this many seconds behind car 17, wherever car 17 is

# ---------------------------------------------------------------- marshal baseline (A4/A5)
MARSHAL_SEE_VISIBLE_S = (0.2, 1.0)   # post can see the spot
MARSHAL_SEE_BLIND_S = (3.0, 8.0)     # blind crest: relies on radio / another post
MARSHAL_VISIBILITY = 0.5             # chance the nearest post can see the incident
MARSHAL_REACT_S = (0.8, 2.5)
MARSHAL_FLAG_S = (0.5, 1.5)

# ---------------------------------------------------------------- Monte Carlo (A5)
MC_N = 10_000
MC_GAP_S = (0.8, 3.0)
MC_SPEED_KMH = (150.0, 250.0)
MC_SIGHTLINE_M = (50.0, 250.0)
MC_BRAKE_G = (1.0, 1.5)
MC_DRIVER_REACT_S = (0.7, 1.5)
MC_IMU_DETECT_S = 0.3
MC_CAMERA_DETECT_S = (0.5, 1.0)
MC_NETWORK_S = (0.05, 0.3)
MC_IMU_FALSE_NEG = 0.10          # replace with the tuning-session numbers (spec §11)
MC_CAMERA_FALSE_NEG = 0.05       # replace with the vision-metrics numbers
MC_CAMERA_COVERAGE = 0.8         # share of incidents inside a camera's view
# false yellows/hour: PLACEHOLDERS until the tuning session measures real rates
MC_IMU_FALSE_EVENTS_PER_CAR_HOUR = 0.2
MC_CAMERA_FALSE_EVENTS_PER_HOUR = 0.5
MC_FIELD_CARS = 20
MC_SINGLE_SOURCE_YELLOW_SHARE = 0.3   # share of false single-source events that reach YELLOW (rest capped at CAUTION)

# ---------------------------------------------------------------- latency (A6)
RESULTS_DIR = PACKAGE_DIR.parent / "results"

G = 9.81
