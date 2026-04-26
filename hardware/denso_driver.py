# =============================================================================
# hardware/denso_driver.py — DENSO 産業用ロボット b-CAP ドライバ
#   Normal モード: robot_move() による標準モーション (PC操作=離散ステップ)
#   Slave  モード: slvMove ストリーミング (Gamepad=連続デルタ)
# =============================================================================

import threading
import time
import logging

logger = logging.getLogger(__name__)

try:
    import pybcapclient.bcapclient as bcapclient
    BCAP_AVAILABLE = True
except ImportError:
    logger.warning("pybcapclient が見つかりません。DENSOドライバはモックモードで動作します。")
    BCAP_AVAILABLE = False


class DensoRobot:
    """DENSO 6軸ロボット b-CAP 通信ドライバ (Normal / Slave 二系統)"""

    MODE_NORMAL = "normal"   # robot_move
    MODE_SLAVE  = "slave"    # slvMove

    def __init__(self, host: str, port: int, timeout: int,
                 speed: int = 10,
                 slave_cycle: float = 0.008,
                 slave_max_step_t: float = 0.1,
                 slave_max_step_r: float = 0.1,
                 slave_speed: int = 100,
                 slave_ramp_cycles: int = 50,
                 target_lookahead_t: float = 50.0,
                 target_lookahead_r: float = 30.0):
        self.host               = host
        self.port               = port
        self.timeout            = timeout
        self.speed              = speed
        self.slave_cycle        = slave_cycle
        self.slave_max_step_t   = slave_max_step_t
        self.slave_max_step_r   = slave_max_step_r
        self.slave_speed        = slave_speed
        self.slave_ramp_cycles  = max(1, slave_ramp_cycles)
        self.target_lookahead_t = target_lookahead_t
        self.target_lookahead_r = target_lookahead_r

        self._bcap_client = None
        self._h_ctrl      = None
        self._h_robot     = None
        self._connected   = False

        self._pos_lock    = threading.Lock()
        # 位置配列: [x, y, z, rx, ry, rz, fig] — connect() で CurPos から上書き
        self._current_pos: list[float] = [0.0] * 7
        self._target_pos:  list[float] = [0.0] * 7

        # Slave モード専用速度バッファ (velocity command)
        # gamepad は「今この速度で動け」を毎フレーム上書き (accumulate しない)
        # slave loop は毎 slvMove サイクルにこの値を pos に加算
        # → 両者のレートがずれても二重加算・ゼロ加算が起きない
        self._vel_lock = threading.Lock()
        self._velocity: list[float] = [0.0] * 6

        self._mode      = self.MODE_NORMAL
        self._mode_lock = threading.Lock()

        # Normal mode 移動スレッド
        self._move_thread: threading.Thread | None = None
        self._move_stop      = threading.Event()
        self._target_changed = threading.Event()

        # Slave mode ストリーミングスレッド
        self._slave_thread: threading.Thread | None = None
        self._slave_stop = threading.Event()

    # ------------------------------------------------------------------ #
    #  接続・初期化
    # ------------------------------------------------------------------ #
    def connect(self) -> bool:
        if not BCAP_AVAILABLE:
            logger.warning("[DENSO] モックモード: 接続をスキップします")
            self._connected = True
            self._start_move_thread()
            return True
        try:
            self._bcap_client = bcapclient.BCAPClient(self.host, self.port, self.timeout)
            self._bcap_client.service_start("")
            self._h_ctrl = self._bcap_client.controller_connect(
                "", "CaoProv.DENSO.VRC", "localhost", ""
            )
            self._h_robot = self._bcap_client.controller_getrobot(self._h_ctrl, "Arm", "")

            # 制御権取得 → Tool1 座標系選択 → 速度設定 → モーターON
            # (robot_control.py 参照: Tool1 を選択しないとスレーブモードで座標系が
            #  不整合になり、最初の slvMove で速度違反 → スレーブ自動解除する)
            self._bcap_client.robot_execute(self._h_robot, "TakeArm", [0, 0])
            self._bcap_client.robot_change(self._h_robot, "Tool1")
            self._bcap_client.robot_execute(
                self._h_robot, "ExtSpeed", [self.speed, self.speed, self.speed]
            )
            self._bcap_client.robot_execute(self._h_robot, "Motor", [1, 0])
            time.sleep(0.5)

            cur_pos = self._bcap_client.robot_execute(self._h_robot, "CurPos")
            with self._pos_lock:
                self._current_pos = list(cur_pos)
                self._target_pos  = list(cur_pos)
            logger.info(f"[DENSO] 現在位置: {self._current_pos}")

            self._connected = True
            self._start_move_thread()
            logger.info("[DENSO] 接続・初期化 完了 (Normal mode)")
            return True

        except Exception as e:
            logger.error(f"[DENSO] 接続失敗: {e}")
            self._connected = False
            return False

    # ------------------------------------------------------------------ #
    #  Normal mode (robot_move) スレッド
    # ------------------------------------------------------------------ #
    def _start_move_thread(self):
        self._move_stop.clear()
        self._target_changed.clear()
        self._move_thread = threading.Thread(
            target=self._move_loop, daemon=True, name="denso-move"
        )
        self._move_thread.start()

    def _stop_move_thread(self):
        self._move_stop.set()
        self._target_changed.set()
        if self._move_thread:
            self._move_thread.join(timeout=3.0)
        self._move_thread = None

    def _move_loop(self):
        """target_pos が変化したら robot_move を送信する。最大10Hz。"""
        POLL_INTERVAL = 0.1  # 100ms

        while not self._move_stop.is_set():
            triggered = self._target_changed.wait(timeout=POLL_INTERVAL)
            if not triggered or self._move_stop.is_set():
                continue
            self._target_changed.clear()

            with self._pos_lock:
                target  = list(self._target_pos)
                current = list(self._current_pos)

            if target == current:
                continue

            if not BCAP_AVAILABLE:
                with self._pos_lock:
                    self._current_pos = list(target)
                continue

            try:
                pose = [target, "L", "@E"]
                self._bcap_client.robot_move(self._h_robot, 2, pose, "")
                with self._pos_lock:
                    self._current_pos = list(target)
                logger.debug(f"[DENSO] 移動完了: {target[:3]}")

            except Exception as e:
                err_str = (
                    f"[DENSO] robot_move エラー: {e} "
                    f"(0x{e.hresult & 0xFFFFFFFF:08X})"
                    if hasattr(e, "hresult") else
                    f"[DENSO] robot_move エラー: {e}"
                )
                logger.error(err_str)
                break

        logger.info("[DENSO] Normal mode スレッド終了")

    # ------------------------------------------------------------------ #
    #  Slave mode (slvMove) ストリーミング
    # ------------------------------------------------------------------ #
    def enter_slave_mode(self) -> bool:
        with self._mode_lock:
            if self._mode == self.MODE_SLAVE:
                return True
            if not self._connected:
                logger.warning("[DENSO] 未接続のためスレーブモードに入れません")
                return False

            # Normal mode スレッド停止
            self._stop_move_thread()

            # ジャンプ防止: target を current 位置にリセット
            with self._pos_lock:
                self._target_pos = list(self._current_pos)

            # 速度バッファをゼロリセット (前回の残留速度を破棄)
            with self._vel_lock:
                self._velocity = [0.0] * 6

            if not BCAP_AVAILABLE:
                self._mode = self.MODE_SLAVE
                logger.warning("[DENSO] モックモード: スレーブをシミュレート")
                return True

            try:
                # スレーブモード中は追従速度を高めに設定 (SLAVE_SPEED)
                self._bcap_client.robot_execute(
                    self._h_robot, "ExtSpeed",
                    [self.slave_speed, self.slave_speed, self.slave_speed]
                )
                self._bcap_client.robot_execute(self._h_robot, "slvSendFormat", 0x0000)
                self._bcap_client.robot_execute(self._h_robot, "slvRecvFormat", 0x0014)
                self._bcap_client.robot_execute(self._h_robot, "slvChangeMode", 0x201)

                self._slave_stop.clear()
                self._slave_thread = threading.Thread(
                    target=self._slave_loop, daemon=True, name="denso-slave"
                )
                self._slave_thread.start()

                self._mode = self.MODE_SLAVE
                logger.info("[DENSO] Slave mode 開始")
                return True

            except Exception as e:
                logger.error(f"[DENSO] Slave mode 開始失敗: {e}")
                self._start_move_thread()
                return False

    def exit_slave_mode(self) -> bool:
        with self._mode_lock:
            if self._mode != self.MODE_SLAVE:
                return True

            # Slave スレッド停止
            self._slave_stop.set()
            if self._slave_thread:
                self._slave_thread.join(timeout=3.0)
            self._slave_thread = None

            if BCAP_AVAILABLE and self._h_robot:
                try:
                    self._bcap_client.robot_execute(self._h_robot, "slvChangeMode", 0x000)
                except Exception as e:
                    logger.error(f"[DENSO] Slave mode 解除エラー: {e}")

                # ExtSpeed を Normal mode 用の値に戻す
                try:
                    self._bcap_client.robot_execute(
                        self._h_robot, "ExtSpeed",
                        [self.speed, self.speed, self.speed]
                    )
                except Exception:
                    pass

                # スレーブ中に動いた現在位置を再同期
                try:
                    cur = self._bcap_client.robot_execute(self._h_robot, "CurPos")
                    with self._pos_lock:
                        self._current_pos = list(cur)
                        self._target_pos  = list(cur)
                except Exception as e:
                    logger.error(f"[DENSO] CurPos 再同期失敗: {e}")

            # Normal mode 再開
            self._mode = self.MODE_NORMAL
            self._start_move_thread()
            logger.info("[DENSO] Normal mode に復帰")
            return True

    def _slave_loop(self):
        """デルタバッファ方式スレーブループ。
        各サイクル先頭で gamepad が積んだデルタを原子的に消費し、
        自分が管理するローカル pos に加算してから slvMove を送る。
        slvMove 中はどのロックも保持しないためガタつきを排除。"""
        # slave loop だけが読み書きするローカル位置 (ロック不要)
        with self._pos_lock:
            pos = list(self._current_pos)   # [x,y,z,rx,ry,rz,fig]

        slave_error = False

        while not self._slave_stop.is_set():
            # ① 現在の速度コマンドをコピー (上書き方式なのでリセット不要)
            with self._vel_lock:
                v = list(self._velocity)

            # ② ローカル pos に速度を加算 (毎サイクル固定ステップ)
            for i in range(6):
                pos[i] += v[i]

            # ③ slvMove 送信 (ロック保持なし → 通信ジッタが他スレッドに波及しない)
            try:
                self._bcap_client.robot_execute(self._h_robot, "slvMove", pos)

                # current_pos を更新 (get_target 用)
                with self._pos_lock:
                    self._current_pos = list(pos)
                    for i in range(6):
                        self._target_pos[i] = pos[i]

            except Exception as e:
                err_str = (
                    f"[DENSO] slvMove エラー: {e} (0x{e.hresult & 0xFFFFFFFF:08X})"
                    if hasattr(e, "hresult") else
                    f"[DENSO] slvMove エラー: {e}"
                )
                logger.error(err_str)
                slave_error = True
                break

        logger.info("[DENSO] Slave loop 終了")

        # エラーで抜けた場合は自動回復 (Normal mode に戻す)
        if slave_error:
            threading.Thread(
                target=self._recover_to_normal, daemon=True, name="denso-recover"
            ).start()

    def _recover_to_normal(self):
        """slvMove エラー時の自動回復: スレーブ解除 → モーターON → Normal mode"""
        time.sleep(0.3)
        with self._mode_lock:
            if self._mode != self.MODE_SLAVE:
                return

            if BCAP_AVAILABLE and self._h_robot:
                for cmd, param in [
                    ("slvChangeMode", 0x000),          # スレーブ解除
                    ("Motor", [0, 0]),                  # 一旦 Motor OFF
                ]:
                    try:
                        self._bcap_client.robot_execute(self._h_robot, cmd, param)
                    except Exception:
                        pass

                time.sleep(0.3)

                for cmd, param in [
                    ("Motor", [1, 0]),                  # Motor ON
                    ("ExtSpeed", [self.speed, self.speed, self.speed]),
                ]:
                    try:
                        self._bcap_client.robot_execute(self._h_robot, cmd, param)
                    except Exception:
                        pass

                try:
                    cur = self._bcap_client.robot_execute(self._h_robot, "CurPos")
                    with self._pos_lock:
                        self._current_pos = list(cur)
                        self._target_pos  = list(cur)
                except Exception:
                    pass

            self._slave_thread = None
            self._mode = self.MODE_NORMAL
            self._start_move_thread()
            logger.warning("[DENSO] Slave エラー → Motor ON 再起動 → Normal mode 復帰")

    def reconnect(self) -> bool:
        """コントローラーエラー後の手動再接続: TakeArm→Motor ON→CurPos から再初期化"""
        logger.info("[DENSO] 手動再接続を開始します")
        if not BCAP_AVAILABLE or not self._h_robot:
            logger.warning("[DENSO] 接続なし — 再接続スキップ")
            return False

        # 走行中のスレッドを止める
        if self._mode == self.MODE_SLAVE:
            self._slave_stop.set()
            if self._slave_thread:
                self._slave_thread.join(timeout=2.0)
        self._stop_move_thread()

        try:
            for cmd, param in [
                ("slvChangeMode", 0x000),
                ("Motor", [0, 0]),
                ("GiveArm", None),
            ]:
                try:
                    self._bcap_client.robot_execute(self._h_robot, cmd, param)
                except Exception:
                    pass

            time.sleep(0.5)

            self._bcap_client.robot_execute(self._h_robot, "TakeArm", [0, 0])
            self._bcap_client.robot_change(self._h_robot, "Tool1")
            self._bcap_client.robot_execute(
                self._h_robot, "ExtSpeed", [self.speed, self.speed, self.speed]
            )
            self._bcap_client.robot_execute(self._h_robot, "Motor", [1, 0])
            time.sleep(0.5)

            cur = self._bcap_client.robot_execute(self._h_robot, "CurPos")
            with self._pos_lock:
                self._current_pos = list(cur)
                self._target_pos  = list(cur)

            with self._vel_lock:
                self._velocity = [0.0] * 6

            self._connected = True
            self._mode = self.MODE_NORMAL
            self._start_move_thread()
            logger.info(f"[DENSO] 手動再接続完了: {cur}")
            return True

        except Exception as e:
            logger.error(f"[DENSO] 手動再接続失敗: {e}")
            self._connected = False
            return False

    # ------------------------------------------------------------------ #
    #  目標位置更新 (両モード共通)
    # ------------------------------------------------------------------ #
    def update_target(self, delta: list[float]):
        """目標座標を差分で更新する [dX, dY, dZ, dRX, dRY, dRZ]

        Slave mode: gamepad スレッドは _delta_buf に書くだけ。
                    slave loop が各 slvMove サイクル先頭で消費する。
                    slvMove 中はバッファもロックも保持しないので通信ジッタを排除。
        Normal mode: _target_pos を直接更新し、move thread を起こす。
        """
        if self._mode == self.MODE_SLAVE:
            # 速度として上書き (積み上げ不可 → 二重加算ガタつきを防止)
            with self._vel_lock:
                for i in range(min(len(delta), 6)):
                    self._velocity[i] = delta[i]
        else:
            with self._pos_lock:
                for i in range(min(len(delta), 6)):
                    cap = self.target_lookahead_t if i < 3 else self.target_lookahead_r
                    new_val   = self._target_pos[i] + delta[i]
                    lookahead = new_val - self._current_pos[i]
                    if lookahead > cap:
                        new_val = self._current_pos[i] + cap
                    elif lookahead < -cap:
                        new_val = self._current_pos[i] - cap
                    self._target_pos[i] = new_val
            self._target_changed.set()

    def get_target(self) -> list[float]:
        with self._pos_lock:
            return list(self._target_pos[:6])

    # ------------------------------------------------------------------ #
    #  終了処理
    # ------------------------------------------------------------------ #
    def disconnect(self):
        logger.info("[DENSO] 終了処理を開始します")

        # Slave mode 中なら抜ける
        if self._mode == self.MODE_SLAVE:
            try:
                self.exit_slave_mode()
            except Exception as e:
                logger.error(f"[DENSO] Slave mode 終了エラー: {e}")

        self._stop_move_thread()

        if BCAP_AVAILABLE and self._h_robot:
            try:
                self._bcap_client.robot_execute(self._h_robot, "Motor", [0, 0])
                self._bcap_client.robot_execute(self._h_robot, "GiveArm", None)
                self._bcap_client.robot_release(self._h_robot)
            except Exception as e:
                logger.error(f"[DENSO] 終了処理エラー: {e}")

        if BCAP_AVAILABLE and self._h_ctrl and self._bcap_client:
            try:
                self._bcap_client.controller_disconnect(self._h_ctrl)
                self._bcap_client.service_stop()
            except Exception:
                pass

        self._connected = False
        logger.info("[DENSO] 切断完了")

    # ------------------------------------------------------------------ #
    #  プロパティ
    # ------------------------------------------------------------------ #
    @property
    def is_connected(self) -> bool:
        return self._connected

    @property
    def mode(self) -> str:
        return self._mode
