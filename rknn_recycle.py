import cv2
import numpy as np
import requests
import json
import time
import multiprocessing
from datetime import datetime, timezone, timedelta
from ultralytics import YOLO


# =========================
# Load Configuration
# =========================
def load_config(path="config.json"):
    with open(path, "r") as f:
        return json.load(f)


CONFIG = load_config()

SERVER_URL = CONFIG["data_send_url"]
HEARTBEAT_URL = CONFIG["heartbeat_url"]
SECRET_KEY = CONFIG["X-Secret-Key"]

MODEL_PATH = CONFIG["model"]
CAMERAS = CONFIG["streams"]

CHECK_INTERVAL = CONFIG["inference_interval"]
HEARTBEAT_INTERVAL = CONFIG["heartbeat_interval"]

FRAME_WIDTH = CONFIG["frame_width"]
FRAME_HEIGHT = CONFIG["frame_height"]

SEND_WIDTH = CONFIG["frame_send_width"]
SEND_HEIGHT = CONFIG["frame_send_height"]

JPEG_QUALITY = CONFIG["frame_send_jpeg_quality"]

SHOW = CONFIG["show"]
SEND_DATA = CONFIG["send_data"]

# =========================
# Classes
# =========================
CLASS_NAMES = ["Plastic", "Paper", "Metal", "Glass", "Other"]


# =========================
# Time Helper
# =========================
def get_next_check_time():
    return datetime.now(timezone.utc) + timedelta(seconds=CHECK_INTERVAL)


# =========================
# Send Data
# =========================
def send_recycle_data(
    camera_id,
    camera_sn,
    is_clean,
    current_status,
    start_time,
    end_time,
    timestamp,
    frame
):
    headers = {"X-Secret-Key": SECRET_KEY}

    payload = {
        "id": camera_id,
        "camera": camera_id,
        "is_clean": is_clean,
        "current_status": current_status,
        "annotator_status": "",
        "ai_status": current_status,
        "start_time": start_time,
        "end_time": end_time,
        "image": "",
        "created_at": timestamp,
        "updated_at": timestamp,
        "camera_type": "recycle",
        "is_annotated": False,
        "is_ai_annotated": True,
        "ai_annotation_time": timestamp,
        "time": timestamp
    }

    frame = cv2.resize(frame, (SEND_WIDTH, SEND_HEIGHT))

    _, img_encoded = cv2.imencode(
        ".jpg",
        frame,
        [cv2.IMWRITE_JPEG_QUALITY, JPEG_QUALITY]
    )

    files = {
        "image": ("snapshot.jpg", img_encoded.tobytes(), "image/jpeg")
    }

    data = {
        "data": json.dumps(payload)
    }

    try:
        r = requests.post(
            SERVER_URL,
            headers=headers,
            data=data,
            files=files,
            timeout=30
        )

        if r.status_code in [200, 201]:
            print(f"[{camera_sn}] ✓ Sent")
        else:
            print(f"[{camera_sn}] ✗ Failed: {r.status_code}")

    except Exception as e:
        print(f"[{camera_sn}] ✗ Send error: {e}")


# =========================
# Heartbeat
# =========================
def send_heartbeat(camera_sn):
    try:
        requests.post(
            HEARTBEAT_URL,
            headers={"X-Secret-Key": SECRET_KEY},
            json={"sn": camera_sn},
            timeout=5
        )
        print(f"[{camera_sn}] ♥ Heartbeat")
    except:
        print(f"[{camera_sn}] ✗ Heartbeat failed")


# =========================
# Camera Process
# =========================
def process_camera(camera):
    camera_sn = camera["sn"]
    camera_id = camera.get("id", 0)
    rtsp_url = camera["video_source"]

    print(f"[{camera_sn}] Starting...")

    try:
        model = YOLO(MODEL_PATH, task="detect")
        print(f"[{camera_sn}] ✓ Model loaded")
    except Exception as e:
        print(f"[{camera_sn}] ✗ Model error: {e}")
        return

    cap = None
    next_check = get_next_check_time()
    last_heartbeat = time.time()

    while True:
        try:
            # connect camera
            if cap is None or not cap.isOpened():
                print(f"[{camera_sn}] Connecting...")
                cap = cv2.VideoCapture(rtsp_url)

                if not cap.isOpened():
                    print(f"[{camera_sn}] ✗ Connection failed")
                    time.sleep(5)
                    continue

                print(f"[{camera_sn}] ✓ Connected")

            # heartbeat
            if time.time() - last_heartbeat > HEARTBEAT_INTERVAL:
                send_heartbeat(camera_sn)
                last_heartbeat = time.time()

            # wait inference interval
            now = datetime.now(timezone.utc)
            if now < next_check:
                time.sleep(0.5)
                continue

            cap.release()
            cap = cv2.VideoCapture(rtsp_url, cv2.CAP_FFMPEG)
            for _ in range(5):
                cap.grab()

            ret, frame = cap.read()

            if not ret:
                print(f"[{camera_sn}] ✗ Frame read failed")
                cap.release()
                cap = None
                next_check = get_next_check_time()
                continue

            frame_resized = cv2.resize(frame, (FRAME_WIDTH, FRAME_HEIGHT))

            # =========================
            # Inference
            # =========================
            start = time.time()
            results = model.predict(
                frame_resized,
                imgsz=FRAME_WIDTH,
                conf=0.3,
                iou=CONFIG["iou_threshold"],
                verbose=False
            )
            print(f"[{camera_sn}] Inference: {(time.time()-start)*1000:.1f} ms")

            # =========================
            # Detection counting (NO BBX DRAWING)
            # =========================
            detected_counts = {name: 0 for name in CLASS_NAMES}

            if results and results[0].boxes:
                classes = results[0].boxes.cls.cpu().numpy().astype(int)

                for cls_id in classes:
                    if cls_id < len(CLASS_NAMES):
                        detected_counts[CLASS_NAMES[cls_id]] += 1

            detected_counts = {k: v for k, v in detected_counts.items() if v > 0}

            # =========================
            # PRINT OUTPUT
            # =========================
            if detected_counts:
                print(
                    f"[{camera_sn}] Detected: " +
                    "  ".join([f"{k}: {v}" for k, v in detected_counts.items()])
                )
                current_status = list(detected_counts.keys())
                is_clean = False
            else:
                print(f"[{camera_sn}] Detected: Clean")
                current_status = []
                is_clean = True

            # =========================
            # Time window
            # =========================
            period_start = next_check - timedelta(seconds=CHECK_INTERVAL)
            period_end = next_check

            start_time = period_start.strftime("%Y-%m-%dT%H:%M:%SZ")
            end_time = period_end.strftime("%Y-%m-%dT%H:%M:%SZ")
            timestamp = end_time

            # =========================
            # Send
            # =========================
            if SEND_DATA:
                send_recycle_data(
                    camera_id,
                    camera_sn,
                    is_clean,
                    current_status,
                    start_time,
                    end_time,
                    timestamp,
                    frame
                )

            # =========================
            # Show (raw frame only)
            # =========================
            if SHOW:
                cv2.imshow(camera_sn, frame)
                cv2.waitKey(1)

            next_check = get_next_check_time()

        except Exception as e:
            print(f"[{camera_sn}] ✗ Error: {e}")
            time.sleep(3)

    if cap:
        cap.release()


# =========================
# Main
# =========================
def main():
    print("\n" + "=" * 60)
    print("RECYCLING CLASSIFICATION SYSTEM (NO BBOX)")
    print("=" * 60)

    print(f"Model: {MODEL_PATH}")
    print(f"Cameras: {len(CAMERAS)}")
    print(f"Inference interval: {CHECK_INTERVAL}s")

    processes = []

    for camera in CAMERAS:
        p = multiprocessing.Process(
            target=process_camera,
            args=(camera,)
        )
        p.start()
        processes.append(p)

    try:
        for p in processes:
            p.join()
    except KeyboardInterrupt:
        print("\nStopping...")
        for p in processes:
            p.terminate()
            p.join()


if __name__ == "__main__":
    multiprocessing.set_start_method("spawn", force=True)
    main()