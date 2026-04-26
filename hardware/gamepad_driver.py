# =============================================================================
# hardware/gamepad_driver.py — PS5 DualSense コントローラードライバ
# =============================================================================
#
# 【ボタン割り当て一覧】
#   ×  (0) : ハンド閉じる (押している間)
#   ○  (1) : ハンド開く   (押している間)
#   □  (2) : RY+ (ピッチ+)
#   △  (3) : RY- (ピッチ-)
#   L1  (4) : RX+ (ロール+)
#   R1  (5) : RX- (ロール-)
#   L2 [axis4]: RY+ (アナログ比例、□と同じ軸)
#   R2 [axis5]: RY- (アナログ比例、△と同じ軸)
#   Create (8) : (未使用)
#   Options(9) : (未使用)
#   L3 (10) : (未使用)
#   R3 (11) : (未使用)
#   PS (12) : 終了 (1秒長押し必須)
#   タッチパッド(13): (未使用)
#   ミュート(14): (未使用)
#
# 【6軸完全対応】
#   並進: 左スティック上下=X, 左スティック左右=Y, 右スティック上下=Z
#   回転: L1/R1=RX, □/△(またはL2/R2)=RY, 右スティック左右=RZ
#
# 【スティック割り当て】
#   左スティック 上下 → ロボット X軸 (上=X-)
#   左スティック 左右 → ロボット Y軸 (右=Y+)
#   右スティック 上下 → ロボット Z軸 (上=Z+)
#   右スティック 左右 → ロボット RZ軸 (右=RZ+)
# =============================================================================

import threading
import time
import logging

logger = logging.getLogger(__name__)

try:
    import pygame
    PYGAME_AVAILABLE = True
except ImportError:
    logger.warning("pygame が見つかりません。Gamepadドライバはモックモードで動作します。")
    PYGAME_AVAILABLE = False

import config

# ボタンインデックス (macOS DualSense pygame 実測値 17buttons/6axes)
# ログ確認済み: ×=0, □=2, △=3, L1=10, R1=9
BTN_CROSS     = 0
BTN_CIRCLE    = 1
BTN_SQUARE    = 2
BTN_TRIANGLE  = 3
BTN_L3        = 4   # 左スティック押し込み
BTN_R3        = 5   # 右スティック押し込み
BTN_OPTIONS   = 6
BTN_PS        = 7   # PSボタン
BTN_CREATE    = 8   # Create/Share
BTN_R1        = 9   # 実測確認済み
BTN_L1        = 10  # 実測確認済み
BTN_R2_DIG    = 11  # R2 デジタル
BTN_L2_DIG    = 12  # L2 デジタル
BTN_DPAD_UP   = 13
BTN_DPAD_DOWN = 14
BTN_DPAD_LEFT = 15
BTN_DPAD_RIGHT = 16

# 軸インデックス
AXIS_LX = 0   # 左スティック X
AXIS_LY = 1   # 左スティック Y
AXIS_RX = 2   # 右スティック X
AXIS_RY = 3   # 右スティック Y
AXIS_L2 = 4   # L2 トリガー (アナログ: -1=離す, +1=押し切り)
AXIS_R2 = 5   # R2 トリガー (アナログ: -1=離す, +1=押し切り)

TRIGGER_THRESHOLD = 0.3   # トリガーを「押した」と判定するしきい値 (0–1)
PS_HOLD_SEC       = 1.0   # PS ボタンを何秒長押しで終了するか


class GamepadDriver:
    """PS5 DualSense コントローラードライバ"""

    def __init__(self, deadzone: float = config.GAMEPAD_DEADZONE):
        self.deadzone   = deadzone
        self._joystick  = None
        self._running   = False
        self._thread: threading.Thread | None = None
        self._connected = False

        # コールバック (app.py から登録)
        self.on_move         = None   # (delta: list[float]) -> None
        self.on_hand_velocity= None   # (vel: int) -> None  毎サイクル送信 (0=停止)
        self.on_capture      = None   # () -> None  □ボタン: Basler撮影保存
        self.on_shutdown     = None   # () -> None  PSボタン長押し

        self._btn_prev: dict[int, bool] = {}
        self._ps_hold_start: float | None = None

    def start(self) -> bool:
        if not PYGAME_AVAILABLE:
            logger.warning("[Gamepad] モックモード: コントローラーなし")
            return False

        import os
        os.environ.setdefault("SDL_VIDEODRIVER", "dummy")
        os.environ.setdefault("SDL_AUDIODRIVER", "dummy")

        pygame.init()
        pygame.joystick.init()

        if pygame.joystick.get_count() == 0:
            logger.warning("[Gamepad] コントローラーが接続されていません")
            return False

        self._joystick = pygame.joystick.Joystick(0)
        self._joystick.init()
        self._connected = True

        logger.info(
            f"[Gamepad] 接続: {self._joystick.get_name()} | "
            f"buttons={self._joystick.get_numbuttons()} "
            f"axes={self._joystick.get_numaxes()} "
            f"hats={self._joystick.get_numhats()}"
        )

        self._running = True
        self._thread  = threading.Thread(
            target=self._poll_loop, daemon=True, name="gamepad-poll"
        )
        self._thread.start()
        return True

    def _apply_deadzone(self, val: float) -> float:
        if abs(val) < self.deadzone:
            return 0.0
        return val

    def _btn_just_pressed(self, idx: int) -> bool:
        """ボタンが今フレームで押された瞬間だけ True"""
        if idx >= self._joystick.get_numbuttons():
            return False
        current = bool(self._joystick.get_button(idx))
        prev    = self._btn_prev.get(idx, False)
        self._btn_prev[idx] = current
        return current and not prev

    def _read_trigger(self, axis_idx: int) -> float:
        """アナログトリガーを 0.0–1.0 に正規化 (macOS DualSense: 休止=-1, 押込=+1)
        デッドゾーンを除去し、しきい値→1.0 を 0→1.0 に線形マッピング (ガクつき防止)"""
        if axis_idx >= self._joystick.get_numaxes():
            return 0.0
        raw  = self._joystick.get_axis(axis_idx)
        val  = (raw + 1.0) / 2.0           # -1..+1 → 0..1
        if val < TRIGGER_THRESHOLD:
            return 0.0
        return (val - TRIGGER_THRESHOLD) / (1.0 - TRIGGER_THRESHOLD)  # 0..1 スムーズ

    def _poll_loop(self):
        translate_scale = config.GAMEPAD_TRANSLATE_SCALE
        rotate_scale    = config.GAMEPAD_ROTATE_SCALE

        while self._running:
            pygame.event.pump()

            # ─── スティック読み取り ───────────────────────────────────
            lx = self._apply_deadzone(self._joystick.get_axis(AXIS_LX))
            ly = self._apply_deadzone(self._joystick.get_axis(AXIS_LY))
            rx = self._apply_deadzone(self._joystick.get_axis(AXIS_RX))
            ry = self._apply_deadzone(self._joystick.get_axis(AXIS_RY))

            # L2/R2 アナログトリガー
            l2 = self._read_trigger(AXIS_L2)
            r2 = self._read_trigger(AXIS_R2)

            # ─── デルタ計算 ──────────────────────────────────────────
            delta = [
                ly * translate_scale,       # dX: 上=X-、下=X+
                lx * translate_scale,       # dY: 右=Y+
                -ry * translate_scale,      # dZ: 上=Z+
                0.0,                        # dRX
                0.0,                        # dRY
                rx * rotate_scale,          # dRZ: 右=RZ+
            ]

            # L1/R1 → RX 回転
            if self._joystick.get_button(BTN_L1):
                delta[3] = +rotate_scale
            if self._joystick.get_button(BTN_R1):
                delta[3] = -rotate_scale

            # L2/R2 → RY 回転 (アナログ比例)
            if l2 > 0:
                delta[4] = +rotate_scale * l2
            if r2 > 0:
                delta[4] = -rotate_scale * r2

            # ─── 移動コールバック (velocity 方式: 0 でも必ず送る) ──────
            # stick を離した瞬間 delta=0 が届き、slave loop が pos 更新を止める
            if self.on_move:
                self.on_move(delta)

            # ─── ハンド velocity (毎サイクル送信, 離したら即 vel=0) ──────
            if self.on_hand_velocity:
                if self._joystick.get_button(BTN_CIRCLE):
                    self.on_hand_velocity(+config.GAMEPAD_HAND_SPEED)
                elif self._joystick.get_button(BTN_CROSS):
                    self.on_hand_velocity(-config.GAMEPAD_HAND_SPEED)
                else:
                    self.on_hand_velocity(0)

            # ─── 撮影: □ボタン (押した瞬間のみ) ─────────────────────
            if self._btn_just_pressed(BTN_SQUARE) and self.on_capture:
                self.on_capture()

            # ─── 終了 (PS ボタン 1秒長押し) ─────────────────────────
            if self._joystick.get_button(BTN_PS):
                if self._ps_hold_start is None:
                    self._ps_hold_start = time.time()
                    logger.info("[Gamepad] PS 長押し開始 (1秒で終了)...")
                elif time.time() - self._ps_hold_start >= PS_HOLD_SEC:
                    logger.warning("[Gamepad] PS 長押し確定 → 終了")
                    if self.on_shutdown:
                        self.on_shutdown()
                    self._ps_hold_start = None
            else:
                if self._ps_hold_start is not None:
                    logger.info("[Gamepad] PS 長押しキャンセル (離した)")
                self._ps_hold_start = None

            time.sleep(0.008)  # 125Hz — スレーブループ (8ms/cycle) に合わせる

    def stop(self):
        self._running = False
        if self._thread:
            self._thread.join(timeout=1.0)
        if PYGAME_AVAILABLE:
            try:
                pygame.quit()
            except Exception:
                pass
        logger.info("[Gamepad] 停止")

    @property
    def is_connected(self) -> bool:
        return self._connected
