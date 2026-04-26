# =============================================================================
# hardware/iai_driver.py — IAI 電動シリンダー / ロボットハンド Modbus RTU ドライバ
# クセが強い初期化シーケンスを仕様書通りに厳格実装
# =============================================================================

import time
import logging

logger = logging.getLogger(__name__)

try:
    from pymodbus.client import ModbusSerialClient
    MODBUS_AVAILABLE = True
except ImportError:
    logger.warning("pymodbus が見つかりません。IAIドライバはモックモードで動作します。")
    MODBUS_AVAILABLE = False

import config


class IAIHand:
    """IAI 電動ハンド Modbus RTU ドライバ"""

    def __init__(self, port: str, baudrate: int = 38400,
                 parity: str = "N", stopbits: int = 1, slave_id: int = 1):
        self.port      = port
        self.baudrate  = baudrate
        self.parity    = parity
        self.stopbits  = stopbits
        self.slave_id  = slave_id
        self._client   = None
        self._connected = False
        self._hand_open = True   # 現在の開閉状態

    # ------------------------------------------------------------------ #
    #  接続・初期化 (仕様書 § ② 初期化シーケンス 厳守)
    # ------------------------------------------------------------------ #
    def connect(self) -> bool:
        if not MODBUS_AVAILABLE:
            logger.warning("[IAI] モックモード: 接続をスキップします")
            self._connected = True
            return True
        try:
            self._client = ModbusSerialClient(
                port=self.port,
                baudrate=self.baudrate,
                parity=self.parity,
                stopbits=self.stopbits,
                bytesize=8,
                timeout=1,
            )
            if not self._client.connect():
                raise ConnectionError(f"Modbus ポート {self.port} に接続できません")

            did = self.slave_id

            # 1. アラームリセット: コイル 1031 を True → 0.1秒待機 → False
            self._client.write_coil(config.IAI_COIL_ALARM_RESET, True,  device_id=did)
            time.sleep(0.1)
            self._client.write_coil(config.IAI_COIL_ALARM_RESET, False, device_id=did)

            # 2. サーボON: コイル 1027 を True
            self._client.write_coil(config.IAI_COIL_SERVO_ON, True, device_id=did)
            time.sleep(0.5)

            # 3. 原点復帰開始: コイル 1035 を True
            self._client.write_coil(config.IAI_COIL_HOME_START, True, device_id=did)

            # 4. 原点復帰完了待ち: レジスタ 0x9005 の 4bit目 (0x0008) が 1 になるまで
            logger.info("[IAI] 原点復帰中...")
            timeout_sec = 30.0
            deadline = time.time() + timeout_sec
            while time.time() < deadline:
                result = self._client.read_holding_registers(
                    config.IAI_REG_STATUS, count=1, device_id=did
                )
                if not result.isError():
                    val = result.registers[0]
                    if val & config.IAI_HOME_DONE_MASK:
                        break
                time.sleep(0.1)
            else:
                raise TimeoutError("[IAI] 原点復帰がタイムアウトしました")

            # 5. 完了確認後、コイル 1035 を False に戻す
            self._client.write_coil(config.IAI_COIL_HOME_START, False, device_id=did)

            # 6. 初期位置をオープン (3900) に設定して物理状態とソフト状態を一致させる
            self._client.write_register(
                config.IAI_REG_POSITION, config.IAI_HAND_OPEN_POS, device_id=did
            )
            self._hand_open = True

            self._connected = True
            logger.info("[IAI] 初期化完了 (ハンド全開位置へ移動)")
            return True

        except Exception as e:
            logger.error(f"[IAI] 接続・初期化失敗: {e}")
            if self._client:
                self._client.close()
            return False

    # ------------------------------------------------------------------ #
    #  ハンド制御
    # ------------------------------------------------------------------ #
    def set_position(self, position: int):
        """位置指令値をレジスタ 39169 に書き込む (0〜3900)"""
        position = max(0, min(3900, position))
        if not self._connected:
            return
        if not MODBUS_AVAILABLE:
            logger.info(f"[IAI] モック: 位置 {position} を送信")
            return
        try:
            self._client.write_register(
                config.IAI_REG_POSITION, position, device_id=self.slave_id
            )
            logger.debug(f"[IAI] 位置指令: {position}")
        except Exception as e:
            logger.error(f"[IAI] 位置書き込みエラー: {e}")

    def open_hand(self):
        self._hand_open = True
        self.set_position(config.IAI_HAND_OPEN_POS)
        logger.info("[IAI] ハンドを開きます")

    def close_hand(self):
        self._hand_open = False
        self.set_position(config.IAI_HAND_CLOSE_POS)
        logger.info("[IAI] ハンドを閉じます")

    def toggle_hand(self):
        if self._hand_open:
            self.close_hand()
        else:
            self.open_hand()

    # ------------------------------------------------------------------ #
    #  終了処理
    # ------------------------------------------------------------------ #
    def disconnect(self):
        if self._client and MODBUS_AVAILABLE:
            try:
                # 安全のためハンドを開いて終了
                self.open_hand()
                time.sleep(0.2)
                self._client.close()
            except Exception as e:
                logger.error(f"[IAI] 切断エラー: {e}")
        self._connected = False
        logger.info("[IAI] 切断完了")

    @property
    def is_connected(self) -> bool:
        return self._connected

    @property
    def hand_is_open(self) -> bool:
        return self._hand_open
