"""
flagzero/config.py

Every tunable number in the project lives here -- nobody hardcodes a
threshold, a speed, or a distance anywhere else. Right now most of these are
placeholders (the values the H0-0:45 setup prompt asks for); they get real
values as A1-A6 are built out. Grouped by the subsystem that owns them so
it's obvious where to look when tuning something live at the venue.
"""

# ---------------------------------------------------------------------------
# TRACK
# ---------------------------------------------------------------------------
LAP_LENGTH_M = 4000.0
PAPER_TRACK_START_M = 1380.0
PAPER_TRACK_END_M = 1480.0
T4_POSITION_M = 1423.0
T4_SIGHTLINE_M = 90.0

# ---------------------------------------------------------------------------
# SIM (sim/sim.py -- built in A1)
# ---------------------------------------------------------------------------
SIM_NUM_CARS = 20
SIM_TICK_HZ = 20
SIM_START_SPACING_MIN_S = 1.0
SIM_START_SPACING_MAX_S = 3.0
SIM_MAX_ACCEL_MPS2 = 8.0    # placeholder, tune once A1 is built
SIM_MAX_BRAKE_MPS2 = 12.0   # placeholder

# ---------------------------------------------------------------------------
# ROUTER (core/router.py -- built in A2)
# ---------------------------------------------------------------------------
ROUTER_MAX_RANGE_M = 2000.0
ROUTER_T_REACT_S = 1.0
ROUTER_V_SAFE_KMH = 80.0
ROUTER_BRAKE_A_G = 1.2
ROUTER_WARNING_RESEND_MS = 250
ROUTER_YELLOW_ETA_MAX_S = 20.0
ROUTER_CAUTION_ETA_MAX_S = 40.0
ROUTER_URGENT_ETA_MAX_S = 6.0
ROUTER_URGENT_MARGIN_MAX_M = 100.0

# ---------------------------------------------------------------------------
# FUSION (core/fusion.py -- built in A3)
# ---------------------------------------------------------------------------
FUSION_ASSOCIATION_DIST_M = 50.0
FUSION_ASSOCIATION_TIME_S = 5.0

# ---------------------------------------------------------------------------
# SEVERITY (core/severity.py -- built in A3)
# ---------------------------------------------------------------------------
SEVERITY_LOW_CONF_THRESHOLD = 0.6
SEVERITY_STATIONARY_S_FOR_LEVEL4 = 3.0  # placeholder, tune in A3

# ---------------------------------------------------------------------------
# MEDICAL (core/medical.py -- built in A4)
# ---------------------------------------------------------------------------
MEDICAL_COUNTDOWN_SECS = 10
MEDICAL_HR_REST_MIN = 70
MEDICAL_HR_REST_MAX = 80
MEDICAL_HR_POST_IMPACT_MIN = 135
MEDICAL_HR_POST_IMPACT_MAX = 145
MEDICAL_HR_RAMP_S = 5.0
MEDICAL_SPO2_MIN = 96
MEDICAL_SPO2_MAX = 98

# ---------------------------------------------------------------------------
# MARSHAL (sim/marshal.py -- built in A4)
# ---------------------------------------------------------------------------
MARSHAL_REACTION_MIN_S = 0.8
MARSHAL_REACTION_MAX_S = 2.5
MARSHAL_FLAG_DEPLOY_MIN_S = 0.5
MARSHAL_FLAG_DEPLOY_MAX_S = 1.5

# ---------------------------------------------------------------------------
# MONTE CARLO (sim/montecarlo.py -- built in A5)
# ---------------------------------------------------------------------------
MC_DEFAULT_N = 10000
MC_FOLLOWING_GAP_MIN_S = 0.8
MC_FOLLOWING_GAP_MAX_S = 3.0
MC_SPEED_MIN_KMH = 150
MC_SPEED_MAX_KMH = 250
MC_SIGHTLINE_MIN_M = 50
MC_SIGHTLINE_MAX_M = 250
MC_BRAKING_MIN_G = 1.0
MC_BRAKING_MAX_G = 1.5
MC_DRIVER_REACTION_MIN_S = 0.7
MC_DRIVER_REACTION_MAX_S = 1.5
MC_IMU_DETECT_S = 0.3
MC_CAMERA_DETECT_MIN_S = 0.5
MC_CAMERA_DETECT_MAX_S = 1.0
MC_NETWORK_MIN_S = 0.05
MC_NETWORK_MAX_S = 0.3

# ---------------------------------------------------------------------------
# SERVER
# ---------------------------------------------------------------------------
SERVER_HOST = "0.0.0.0"
SERVER_PORT = 8000
DASH_BROADCAST_HZ = 20  # background loop tick rate; state is sent to /ws/dash at this rate
