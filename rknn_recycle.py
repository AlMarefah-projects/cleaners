import cv2
import json
import time
import requests
import multiprocessing

from datetime import datetime, timezone, timedelta
from ultralytics import YOLO


# =========================================================
# Load Configuration
# =========================================================
def load_config(path="config.json"):
    with open(path, "r") as f:
        return json.load(f)


CONFIG = load_config()

# API
SERVER_URL = CONFIG["data_send_url"]
HEARTBEAT_URL = CONFIG["heartbeat_url"]
SECRET_KEY = CONFIG["X-Secret-Key"]

# Model
MODEL_PATH = CONFIG["model"]

# Cameras
CAMERAS = CONFIG["streams"]

# Timing
CHECK_INTERVAL = CONFIG["inference_interval"]
HEARTBEAT_INTERVAL = CONFIG["heartbeat_interval"]

# Frame sizes
FRAME_WIDTH = CONFIG["frame_width"]
FRAME_HEIGHT = CONFIG["frame_height"]

SEND_WIDTH = CONFIG["frame_send_width"]
SEND_HEIGHT = CONFIG["frame_send_height"]

JPEG_QUALITY = CONFIG["frame_send_jpeg_quality"]

# Options
SHOW = CONFIG["show"]
SEND_DATA = CONFIG["send_data"]

# =========================================================
# Classes
# =========================================================
CLASS_NAMES = [
    "cleaner-female",
    "cleaner-mail",
    "supervisor-female",
    "supervisor-mail"
]


# =========================================================
# Time Helper
# =========================================================
def get_next_check_time():
    return datetime.now(timezone.utc) + timedelta(seconds=CHECK_INTERVAL)


# =========================================================
# Send Detection Data
# Payload format:
#
# curl -X POST https://backend.aihajjservices.com/camera/create-cleaners-presence/ \
#   -H "X-Secret-Key: <KEY>" \
#   -F "sn=CAM-BTH-001" \
#   -F "cleaner=cleaner-female" \
#   -F "cleaner_count=2" \
#   -F "start_time=2026-05-25T10:00:00Z" \
#   -F "end_time=2026-05-25T10:05:00Z" \
#   -F "image=@snapshot.jpg"
# =========================================================
def send_cleaner_presence(
    camera_sn,
    cleaner_name,
    cleaner_count,
    start_time,
    end_time,
    frame
):
    headers = {
        "X-Secret-Key": SECRET_KEY
    }

    # Resize frame before sending
    frame = cv2.resize(frame, (SEND_WIDTH, SEND_HEIGHT))

    # Encode image
    success, img_encoded = cv2.imencode(
        ".jpg",
        frame,
        [cv2.IMWRITE_JPEG_QUALITY, JPEG_QUALITY]
    )

    if not success:
        print(f"[{camera_sn}] ✗ Image encoding failed")
        return

    files = {
        "image": (
            "snapshot.jpg",
            img_encoded.tobytes(),
            "image/jpeg"
        )
    }

    data = {
        "sn": camera_sn,
        "cleaner": cleaner_name,
        "cleaner_count": str(cleaner_count),
        "start_time": start_time,
        "end_time": end_time
    }

    try:
        response = requests.post(
            SERVER_URL,
            headers=headers,
            data=data,
            files=files,
            timeout=30
        )

        if response.status_code in [200, 201]:
            print(
                f"[{camera_sn}] ✓ Sent | "
                f"{cleaner_name}: {cleaner_count}"
            )
        else:
            print(
                f"[{camera_sn}] ✗ Failed: "
                f"{response.status_code} | {response.text}"
            )

    except Exception as e:
        print(f"[{camera_sn}] ✗ Send error: {e}")


# =========================================================
# Heartbeat
# =========================================================
def send_heartbeat(camera_sn):
    try:
        requests.post(
            HEARTBEAT_URL,
            headers={"X-Secret-Key": SECRET_KEY},
            json={"sn": camera_sn},
            timeout=5
        )

        print(f"[{camera_sn}] ♥ Heartbeat")

    except Exception:
        print(f"[{camera_sn}] ✗ Heartbeat failed")


# =========================================================
# Camera Process
# =========================================================
def process_camera(camera):

    camera_sn = camera["sn"]
    rtsp_url = camera["video_source"]

    print(f"[{camera_sn}] Starting...")

    # -----------------------------------------------------
    # Load Model
    # -----------------------------------------------------
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

            # -------------------------------------------------
            # Connect Camera
            # -------------------------------------------------
            if cap is None or not cap.isOpened():

                print(f"[{camera_sn}] Connecting...")

                cap = cv2.VideoCapture(rtsp_url)

                if not cap.isOpened():
                    print(f"[{camera_sn}] ✗ Connection failed")
                    time.sleep(5)
                    continue

                print(f"[{camera_sn}] ✓ Connected")

            # -------------------------------------------------
            # Heartbeat
            # -------------------------------------------------
            if time.time() - last_heartbeat > HEARTBEAT_INTERVAL:

                send_heartbeat(camera_sn)
                last_heartbeat = time.time()

            # -------------------------------------------------
            # Wait for next inference time
            # -------------------------------------------------
            now = datetime.now(timezone.utc)

            if now < next_check:
                time.sleep(0.5)
                continue

            # -------------------------------------------------
            # Refresh stream
            # -------------------------------------------------
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

            # -------------------------------------------------
            # Resize frame
            # -------------------------------------------------
            frame_resized = cv2.resize(
                frame,
                (FRAME_WIDTH, FRAME_HEIGHT)
            )

            # -------------------------------------------------
            # Inference
            # -------------------------------------------------
            start = time.time()

            results = model.predict(
                frame_resized,
                imgsz=FRAME_WIDTH,
                conf=0.3,
                iou=CONFIG["iou_threshold"],
                verbose=False
            )

            inference_ms = (time.time() - start) * 1000

            print(
                f"[{camera_sn}] "
                f"Inference: {inference_ms:.1f} ms"
            )

            # -------------------------------------------------
            # Count detections
            # -------------------------------------------------
            detected_counts = {
                name: 0 for name in CLASS_NAMES
            }

            if results and results[0].boxes:

                classes = (
                    results[0]
                    .boxes
                    .cls
                    .cpu()
                    .numpy()
                    .astype(int)
                )

                for cls_id in classes:

                    if cls_id < len(CLASS_NAMES):
                        class_name = CLASS_NAMES[cls_id]
                        detected_counts[class_name] += 1

            # Remove zero counts
            detected_counts = {
                k: v
                for k, v in detected_counts.items()
                if v > 0
            }

            # -------------------------------------------------
            # Print detections
            # -------------------------------------------------
            if detected_counts:

                print(
                    f"[{camera_sn}] Detected -> "
                    + " | ".join(
                        [
                            f"{k}: {v}"
                            for k, v in detected_counts.items()
                        ]
                    )
                )

            else:
                print(f"[{camera_sn}] Detected -> None")

            # -------------------------------------------------
            # Time window
            # -------------------------------------------------
            period_start = (
                next_check -
                timedelta(seconds=CHECK_INTERVAL)
            )

            period_end = next_check

            start_time = period_start.strftime(
                "%Y-%m-%dT%H:%M:%SZ"
            )

            end_time = period_end.strftime(
                "%Y-%m-%dT%H:%M:%SZ"
            )

            # -------------------------------------------------
            # Send detections
            # -------------------------------------------------
            if SEND_DATA:

                for cleaner_name, cleaner_count in detected_counts.items():

                    send_cleaner_presence(
                        camera_sn=camera_sn,
                        cleaner_name=cleaner_name,
                        cleaner_count=cleaner_count,
                        start_time=start_time,
                        end_time=end_time,
                        frame=frame
                    )

            # -------------------------------------------------
            # Show frame
            # -------------------------------------------------
            if SHOW:

                cv2.imshow(camera_sn, frame)
                cv2.waitKey(1)

            # -------------------------------------------------
            # Schedule next inference
            # -------------------------------------------------
            next_check = get_next_check_time()

        except Exception as e:

            print(f"[{camera_sn}] ✗ Error: {e}")

            time.sleep(3)

    if cap:
        cap.release()


# =========================================================
# Main
# =========================================================
def main():

    print("\n" + "=" * 60)
    print("CLEANERS PRESENCE DETECTION SYSTEM")
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


# =========================================================
# Entry
# =========================================================
if __name__ == "__main__":

    multiprocessing.set_start_method(
        "spawn",
        force=True
    )

    main()
