# =============================================================================
# config.py — システム全体の設定を一元管理
# =============================================================================

# --- DENSO ロボット (b-CAP) ---
DENSO_HOST      = "192.168.127.13"
DENSO_PORT      = 5007
DENSO_TIMEOUT   = 2000
ROBOT_SPEED       = 10      # robot_move 速度 0–100 (%)
SLAVE_SPEED       = 70      # スレーブモード時の ExtSpeed (追従能力確保: 高いほど速く動ける)
SLAVE_CYCLE_SEC   = 0.008   # スレーブモード送信周期 (秒) 約8ms

# 1サイクルあたりの最大ステップ (速度違反防止の補間制限)
SLAVE_MAX_STEP_T  = 0.1     # mm / cycle  (並進: 12.5mm/s)
SLAVE_MAX_STEP_R  = 0.1     # deg / cycle (回転: 12.5deg/s)

# ジャーク低減用: スレーブ開始から最大ステップに到達するまでのランプ長 (cycles)
SLAVE_RAMP_CYCLES = 50      # 50×8ms = 400ms

# target が current から離れすぎないようキャップ (gamepad の暴走防止)
TARGET_LOOKAHEAD_T = 50.0   # mm
TARGET_LOOKAHEAD_R = 30.0   # deg

# --- IAI ハンド (Modbus RTU) ---
IAI_SERIAL_PORT = "/dev/cu.usbserial-0001"
IAI_BAUDRATE    = 38400
IAI_PARITY      = "N"
IAI_STOPBITS    = 1
IAI_SLAVE_ID    = 1

# Modbus コイル / レジスタアドレス
IAI_COIL_ALARM_RESET  = 1031
IAI_COIL_SERVO_ON     = 1027
IAI_COIL_HOME_START   = 1035
IAI_REG_STATUS        = 0x9005   # 原点復帰完了確認レジスタ
IAI_REG_POSITION      = 39169   # 位置指令レジスタ
IAI_HOME_DONE_MASK    = 0x0008  # 4bit目 = 原点復帰完了フラグ

IAI_HAND_OPEN_POS     = 3900   # 開位置
IAI_HAND_CLOSE_POS    = 0      # 閉位置

# --- Basler GigE カメラ ---
BASLER_FRAME_WIDTH   = 3840
BASLER_FRAME_HEIGHT  = 2160
BASLER_EXPOSURE_AUTO = True    # True=自動露出, False=手動
BASLER_EXPOSURE_TIME = 10000.0 # 手動時の露出時間 (µs) 元値: 49998
BASLER_GAIN_AUTO     = True    # True=自動ゲイン, False=手動
BASLER_GAIN          = 0.0     # 手動時のゲイン (dB) 元値: 8.0

# --- USB Webカメラ ---
# macOS: system_profiler SPCameraDataType でインデックス順を確認
# 0=USBカメラ(外部), 1=FaceTime HD(内蔵), 2=iPhone Continuity Camera
USB_CAMERA_INDEX   = 0
USB_CAMERA_BACKEND = "AVFOUNDATION"  # macOS は AVFOUNDATION を明示指定

# --- MJPEG ストリーミング ---
STREAM_JPEG_QUALITY = 70   # 0-100

# --- PC 操作ステップ量 ---
STEP_TRANSLATE_MM = 10.0   # 移動量 (mm)
STEP_ROTATE_DEG   = 5.0    # 回転量 (度)

# --- Gamepad 感度 ---
GAMEPAD_TRANSLATE_SCALE = 1.5   # スティック最大振り切り時の移動量 (mm/cycle) 125Hz×1.5=187mm/s
GAMEPAD_ROTATE_SCALE    = 0.3   # 回転量 (deg/cycle) 125Hz×0.3=37.5deg/s
GAMEPAD_DEADZONE        = 0.1   # 不感帯
GAMEPAD_HAND_SPEED      = 130   # ○/× 長押し時のハンド速度 (IAI位置単位/cycle, 3900÷130÷50Hz≒0.6秒で全開/全閉)

# --- Flask ---
FLASK_HOST  = "0.0.0.0"
FLASK_PORT  = 8080
FLASK_DEBUG = False
