# =============================================================================
# hardware/usb_camera.py — USB Webカメラ (サイド) ドライバ
# OpenCV VideoCapture を使い別スレッドで最新フレームを保持する
# =============================================================================

import threading
import time
import logging
import numpy as np
import cv2

logger = logging.getLogger(__name__)


class USBCamera:
    """USB Webカメラドライバ (サイドカメラ)"""

    def __init__(self, index: int = 0):
        self.index   = index
        self._cap    = None
        self._frame: np.ndarray | None = None
        self._lock   = threading.Lock()
        self._running = False
        self._thread: threading.Thread | None = None

    def start(self) -> bool:
        import config as cfg
        backend = getattr(cv2, f"CAP_{cfg.USB_CAMERA_BACKEND}", cv2.CAP_ANY)
        self._cap = cv2.VideoCapture(self.index, backend)
        if not self._cap.isOpened():
            logger.warning(f"[USBCam] インデックス {self.index} を開けません。モックで起動します。")
            self._cap = None
            self._running = True
            self._thread = threading.Thread(
                target=self._mock_loop, daemon=True, name="usbcam-mock"
            )
            self._thread.start()
            return True

        self._cap.set(cv2.CAP_PROP_FRAME_WIDTH,  1280)
        self._cap.set(cv2.CAP_PROP_FRAME_HEIGHT, 720)
        self._running = True
        self._thread  = threading.Thread(
            target=self._capture_loop, daemon=True, name="usbcam-capture"
        )
        self._thread.start()
        logger.info("[USBCam] カメラ起動完了")
        return True

    def _capture_loop(self):
        while self._running:
            ret, frame = self._cap.read()
            if ret:
                with self._lock:
                    self._frame = frame.copy()
            else:
                time.sleep(0.01)

    def _mock_loop(self):
        while self._running:
            h, w = 720, 1280
            frame = np.zeros((h, w, 3), dtype=np.uint8)
            cv2.putText(
                frame, "USB Camera (MOCK)", (50, h // 2),
                cv2.FONT_HERSHEY_SIMPLEX, 1.2, (0, 255, 100), 2
            )
            with self._lock:
                self._frame = frame
            time.sleep(0.033)

    def get_frame(self) -> np.ndarray | None:
        with self._lock:
            return self._frame.copy() if self._frame is not None else None

    def stop(self):
        self._running = False
        if self._cap:
            self._cap.release()
        if self._thread:
            self._thread.join(timeout=2.0)
        logger.info("[USBCam] カメラ停止")
