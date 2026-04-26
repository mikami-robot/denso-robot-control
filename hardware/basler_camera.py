# =============================================================================
# hardware/basler_camera.py — Basler GigE カメラ (天井) ドライバ
# pypylon を使い別スレッドで最新フレームを保持する
# =============================================================================

import threading
import logging
import numpy as np

logger = logging.getLogger(__name__)

try:
    from pypylon import pylon
    PYPYLON_AVAILABLE = True
except ImportError:
    logger.warning("pypylon が見つかりません。BaslerCameraはモックモードで動作します。")
    PYPYLON_AVAILABLE = False

import cv2


class BaslerCamera:
    """Basler GigE カメラドライバ (天井カメラ)"""

    def __init__(self, width: int = 1280, height: int = 720):
        self.width   = width
        self.height  = height
        self._camera = None
        self._frame: np.ndarray | None = None
        self._lock   = threading.Lock()
        self._running = False
        self._thread: threading.Thread | None = None

    def start(self) -> bool:
        if not PYPYLON_AVAILABLE:
            logger.warning("[Basler] モックモード: ダミーフレームを生成します")
            self._running = True
            self._thread = threading.Thread(
                target=self._mock_loop, daemon=True, name="basler-mock"
            )
            self._thread.start()
            return True
        try:
            self._camera = pylon.InstantCamera(
                pylon.TlFactory.GetInstance().CreateFirstDevice()
            )
            self._camera.Open()
            self._camera.Width.Value  = self.width
            self._camera.Height.Value = self.height

            # 露出・ゲイン設定
            import config as cfg
            nm = self._camera.GetNodeMap()
            if cfg.BASLER_EXPOSURE_AUTO:
                nm.GetNode("ExposureAuto").SetValue("Continuous")
                logger.info("[Basler] 自動露出 ON")
            else:
                nm.GetNode("ExposureAuto").SetValue("Off")
                nm.GetNode("ExposureTime").SetValue(cfg.BASLER_EXPOSURE_TIME)
                logger.info(f"[Basler] 手動露出: {cfg.BASLER_EXPOSURE_TIME}µs")

            if cfg.BASLER_GAIN_AUTO:
                nm.GetNode("GainAuto").SetValue("Continuous")
                logger.info("[Basler] 自動ゲイン ON")
            else:
                nm.GetNode("GainAuto").SetValue("Off")
                nm.GetNode("Gain").SetValue(cfg.BASLER_GAIN)
                logger.info(f"[Basler] 手動ゲイン: {cfg.BASLER_GAIN}dB")

            self._camera.StartGrabbing(pylon.GrabStrategy_LatestImageOnly)

            self._running = True
            self._thread  = threading.Thread(
                target=self._grab_loop, daemon=True, name="basler-grab"
            )
            self._thread.start()
            logger.info("[Basler] カメラ起動完了")
            return True

        except Exception as e:
            logger.error(f"[Basler] カメラ起動失敗: {e}")
            return False

    def _grab_loop(self):
        converter = pylon.ImageFormatConverter()
        converter.OutputPixelFormat = pylon.PixelType_BGR8packed
        converter.OutputBitAlignment = pylon.OutputBitAlignment_MsbAligned

        while self._running and self._camera.IsGrabbing():
            try:
                grab = self._camera.RetrieveResult(
                    5000, pylon.TimeoutHandling_ThrowException
                )
                if grab.GrabSucceeded():
                    img = converter.Convert(grab)
                    frame = img.GetArray()
                    with self._lock:
                        self._frame = frame.copy()
                grab.Release()
            except Exception as e:
                logger.error(f"[Basler] フレーム取得エラー: {e}")
                break

    def _mock_loop(self):
        """ハードウェア未接続時のダミーフレーム生成"""
        import time
        while self._running:
            frame = np.zeros((self.height, self.width, 3), dtype=np.uint8)
            cv2.putText(
                frame, "Basler Camera (MOCK)", (50, self.height // 2),
                cv2.FONT_HERSHEY_SIMPLEX, 1.2, (0, 200, 255), 2
            )
            with self._lock:
                self._frame = frame
            time.sleep(0.033)

    def get_frame(self) -> np.ndarray | None:
        with self._lock:
            return self._frame.copy() if self._frame is not None else None

    def stop(self):
        self._running = False
        if self._camera and PYPYLON_AVAILABLE:
            try:
                self._camera.StopGrabbing()
                self._camera.Close()
            except Exception:
                pass
        if self._thread:
            self._thread.join(timeout=2.0)
        logger.info("[Basler] カメラ停止")
