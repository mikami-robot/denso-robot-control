# =============================================================================
# app.py — Flask バックエンド / 統合ハードウェア制御システム
# =============================================================================

import os
import signal
import sys
import logging
import threading
import time
import cv2

from flask import Flask, Response, jsonify, render_template, request

import config
from hardware.denso_driver  import DensoRobot
from hardware.iai_driver     import IAIHand
from hardware.basler_camera  import BaslerCamera
from hardware.usb_camera     import USBCamera
from hardware.gamepad_driver import GamepadDriver

# ─────────────────────────── ロギング設定 ───────────────────────────
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s"
)
logger = logging.getLogger(__name__)

# ─────────────────────────── Flask アプリ ───────────────────────────
app = Flask(__name__)

# ─────────────────────────── ハードウェアインスタンス ───────────────
robot   = DensoRobot(config.DENSO_HOST, config.DENSO_PORT, config.DENSO_TIMEOUT,
                     config.ROBOT_SPEED, config.SLAVE_CYCLE_SEC,
                     config.SLAVE_MAX_STEP_T, config.SLAVE_MAX_STEP_R,
                     config.SLAVE_SPEED, config.SLAVE_RAMP_CYCLES,
                     config.TARGET_LOOKAHEAD_T, config.TARGET_LOOKAHEAD_R)
hand    = IAIHand(config.IAI_SERIAL_PORT, config.IAI_BAUDRATE,
                  config.IAI_PARITY, config.IAI_STOPBITS, config.IAI_SLAVE_ID)
basler  = BaslerCamera(config.BASLER_FRAME_WIDTH, config.BASLER_FRAME_HEIGHT)
usbcam  = USBCamera(config.USB_CAMERA_INDEX)
gamepad = GamepadDriver(config.GAMEPAD_DEADZONE)

# 操作モード管理
_mode_lock    = threading.Lock()
_current_mode = "pc"   # "pc" | "gamepad"

# ─── IAI ハンド独立制御 ───────────────────────────────────────────────
# gamepad スレッドを Modbus (~15ms) でブロックしないよう専用スレッドで処理
# velocity 方式: gamepad が毎サイクル速度を送信 (0=停止) → タイムアウト不要
_hand_pos      = config.IAI_HAND_OPEN_POS  # 現在の目標位置 (0–3900)
_hand_velocity = 0                          # +SPEED=開, -SPEED=閉, 0=停止

# ─────────────────────────── 初期化 ─────────────────────────────────
def initialize_hardware():
    logger.info("=== ハードウェア初期化開始 ===")

    # カメラ起動 (失敗してもシステムは継続)
    basler.start()
    usbcam.start()

    # DENSO ロボット接続 (CurPos を取得して robot_move 方式で制御開始)
    robot.connect()

    # IAI ハンド初期化
    hand.connect()

    # IAI ハンド独立制御スレッド起動
    threading.Thread(target=_hand_loop, daemon=True, name="hand-ctrl").start()

    # Gamepad 初期化とコールバック登録
    gamepad.on_move          = _on_gamepad_move
    gamepad.on_hand_velocity = _on_gamepad_hand_velocity
    gamepad.on_capture       = _on_gamepad_capture
    gamepad.on_shutdown      = _on_gamepad_shutdown
    gamepad.start()

    logger.info("=== ハードウェア初期化完了 ===")


def _on_gamepad_move(delta: list):
    """Gamepad からの移動コマンドをロボットに送信"""
    with _mode_lock:
        if _current_mode != "gamepad":
            return
    robot.update_target(delta)


def _hand_loop():
    """IAI ハンド専用スレッド。
    velocity 方式: gamepad が毎サイクル速度を送信 (0=停止) するため
    タイムアウト不要。ボタンを離した瞬間に vel=0 が届き即停止。"""
    global _hand_pos
    while True:
        v = _hand_velocity   # 単純な int 読み取り (Python では原子的)
        if v != 0:
            new_pos = max(config.IAI_HAND_CLOSE_POS,
                         min(config.IAI_HAND_OPEN_POS, _hand_pos + v))
            if new_pos != _hand_pos:
                _hand_pos = new_pos
                hand.set_position(_hand_pos)   # Modbus write ~15ms (専用スレッド)
        time.sleep(0.002)


def _on_gamepad_hand_velocity(vel: int):
    """gamepad から毎サイクル届く velocity (0=停止, ±=開閉)"""
    global _hand_velocity
    with _mode_lock:
        if _current_mode != "gamepad":
            _hand_velocity = 0
            return
    _hand_velocity = vel


def _on_gamepad_capture():
    """□ボタン: Baslerカメラで撮影して ~/Downloads に日時付きJPEGで保存"""
    frame = basler.get_frame()
    if frame is None:
        logger.warning("[Capture] Basler フレームが取得できませんでした")
        return
    from datetime import datetime
    filename = datetime.now().strftime("%Y%m%d_%H%M%S") + ".jpg"
    path = os.path.join(os.path.expanduser("~/Downloads"), filename)
    import cv2 as _cv2
    _cv2.imwrite(path, frame)
    logger.info(f"[Capture] 保存: {path}")


def _on_gamepad_shutdown():
    """PSボタン: プログラム全体を終了する (SIGTERM 経由でクリーンシャットダウン)"""
    logger.warning("!!! PSボタン → 終了シグナル送信 !!!")
    os.kill(os.getpid(), signal.SIGTERM)


def emergency_stop():
    """緊急停止: モーター OFF・制御権解放 (Web API 用)"""
    logger.warning("!!! 緊急停止 実行 !!!")
    robot.disconnect()


def shutdown_all():
    """全デバイスを安全にシャットダウン"""
    logger.info("シャットダウン開始...")
    gamepad.stop()
    robot.disconnect()
    hand.disconnect()
    basler.stop()
    usbcam.stop()
    logger.info("シャットダウン完了")


# SIGTERM / SIGINT ハンドラ (フェールセーフ)
def _signal_handler(signum, frame):
    logger.info(f"シグナル {signum} 受信 → シャットダウン")
    shutdown_all()
    sys.exit(0)

signal.signal(signal.SIGINT,  _signal_handler)
signal.signal(signal.SIGTERM, _signal_handler)


# ─────────────────────────── MJPEG ストリーミング ────────────────────
def _frame_to_jpeg(frame):
    if frame is None:
        return None
    _, buf = cv2.imencode(
        ".jpg", frame,
        [int(cv2.IMWRITE_JPEG_QUALITY), config.STREAM_JPEG_QUALITY]
    )
    return buf.tobytes()


def _generate_stream(camera_get_frame):
    while True:
        frame = camera_get_frame()
        jpeg  = _frame_to_jpeg(frame)
        if jpeg is None:
            time.sleep(0.05)
            continue
        yield (
            b"--frame\r\n"
            b"Content-Type: image/jpeg\r\n\r\n" + jpeg + b"\r\n"
        )
        time.sleep(0.033)   # ~30fps


# ─────────────────────────── Flask ルート ────────────────────────────
@app.route("/")
def index():
    return render_template("index.html")


@app.route("/video/basler")
def video_basler():
    return Response(
        _generate_stream(basler.get_frame),
        mimetype="multipart/x-mixed-replace; boundary=frame"
    )


@app.route("/video/usb")
def video_usb():
    return Response(
        _generate_stream(usbcam.get_frame),
        mimetype="multipart/x-mixed-replace; boundary=frame"
    )


@app.route("/api/status")
def api_status():
    pos = robot.get_target()
    return jsonify({
        "mode":          _current_mode,
        "robot_mode":    robot.mode,
        "robot_ok":      robot.is_connected,
        "hand_ok":       hand.is_connected,
        "hand_open":     hand.hand_is_open,
        "gamepad_ok":    gamepad.is_connected,
        "position": {
            "x": round(pos[0], 2),
            "y": round(pos[1], 2),
            "z": round(pos[2], 2),
            "rx": round(pos[3], 2),
            "ry": round(pos[4], 2),
            "rz": round(pos[5], 2),
        }
    })


@app.route("/api/mode", methods=["POST"])
def api_set_mode():
    global _current_mode
    data = request.get_json()
    mode = data.get("mode", "pc")
    if mode not in ("pc", "gamepad"):
        return jsonify({"error": "invalid mode"}), 400

    with _mode_lock:
        prev = _current_mode
        _current_mode = mode

    # ロボット側のモード切替: gamepad → slave、pc → normal(robot_move)
    if mode == "gamepad" and prev != "gamepad":
        robot.enter_slave_mode()
    elif mode == "pc" and prev != "pc":
        robot.exit_slave_mode()

    logger.info(f"操作モード変更: {prev} → {mode}")
    return jsonify({"mode": _current_mode, "robot_mode": robot.mode})


@app.route("/api/robot/move", methods=["POST"])
def api_robot_move():
    """PC操作モード: ボタン押下時の移動"""
    with _mode_lock:
        if _current_mode != "pc":
            return jsonify({"error": "not in PC mode"}), 403
    data  = request.get_json()
    axis  = data.get("axis", "")          # "x","y","z","rx","ry","rz"
    sign  = float(data.get("sign", 1))   # +1 or -1

    axis_map = {"x": 0, "y": 1, "z": 2, "rx": 3, "ry": 4, "rz": 5}
    if axis not in axis_map:
        return jsonify({"error": "invalid axis"}), 400

    delta = [0.0] * 6
    idx   = axis_map[axis]
    if idx < 3:
        delta[idx] = sign * config.STEP_TRANSLATE_MM
    else:
        delta[idx] = sign * config.STEP_ROTATE_DEG

    robot.update_target(delta)
    return jsonify({"ok": True, "position": robot.get_target()})


@app.route("/api/hand/toggle", methods=["POST"])
def api_hand_toggle():
    with _mode_lock:
        if _current_mode != "pc":
            return jsonify({"error": "not in PC mode"}), 403
    hand.toggle_hand()
    return jsonify({"hand_open": hand.hand_is_open})


@app.route("/api/emergency_stop", methods=["POST"])
def api_emergency_stop():
    emergency_stop()
    return jsonify({"ok": True, "message": "Emergency stop executed"})


@app.route("/api/robot/reconnect", methods=["POST"])
def api_robot_reconnect():
    """コントローラーエラー後に TakeArm→Motor ON から再初期化"""
    ok = robot.reconnect()
    return jsonify({"ok": ok, "robot_ok": robot.is_connected})


# ─────────────────────────── エントリポイント ────────────────────────
if __name__ == "__main__":
    initialize_hardware()
    try:
        app.run(
            host=config.FLASK_HOST,
            port=config.FLASK_PORT,
            debug=config.FLASK_DEBUG,
            threaded=True,
            use_reloader=False   # reloader は二重初期化を引き起こすため無効
        )
    finally:
        shutdown_all()
