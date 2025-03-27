import argparse
import json
import logging
import os
import signal
import sys
import subprocess
import time
import uuid

from kafka import KafkaConsumer, KafkaProducer
from kafka.admin import KafkaAdminClient
from kafka.errors import KafkaError

MAP_FILE = "map.json"
KAFKA_BOOTSTRAP_SERVERS = ['localhost:29092', 'localhost:39092', 'localhost:49092']
STATUS_TOPIC = "segment-status"
MOVEMENT_TOPIC = "token-movement"
COMPOSE_FILE = "docker-compose.yaml"

MAX_SEGMENTS = 100

should_exit = False

# Configure Logging
def configure_logging(verbose=False):
    level = logging.DEBUG if verbose else logging.INFO
    root_logger = logging.getLogger()
    for handler in root_logger.handlers[:]:
        root_logger.removeHandler(handler)
    console_handler = logging.StreamHandler()
    console_handler.setFormatter(logging.Formatter("%(asctime)s [%(levelname)s] %(message)s"))
    console_handler.setLevel(level)
    root_logger.addHandler(console_handler)
    root_logger.setLevel(level)
    kafka_level = logging.DEBUG if verbose else logging.CRITICAL
    for name in logging.root.manager.loggerDict:
        if name.startswith("kafka"):
            logging.getLogger(name).setLevel(kafka_level)

# Start Docker Containers
def start_docker_compose():
    logging.info("Starting Docker containers...")
    subprocess.run(["docker-compose", "build", "--no-cache"], check=True)
    subprocess.run(["docker-compose", "up", "-d"], check=True)
    logging.info("Docker containers started.")

# Stop Docker Containers
def stop_docker_compose():
    logging.info("Stopping all containers...")
    subprocess.run(["docker-compose", "down", "-v"], check=True)
    logging.info("Containers stopped.")

# Handle Program Shut Down
def handle_exit(sig, frame):
    global should_exit
    logging.info("Received signal. Shutting down...")
    should_exit = True
    stop_docker_compose()
    sys.exit(0)

# Waiting for Kafka Container to be ready
def wait_for_kafka_ready(timeout=60):
    logging.info("Waiting for Kafka to become available...")
    start = time.time()
    while time.time() - start < timeout:
        try:
            admin = KafkaAdminClient(bootstrap_servers=KAFKA_BOOTSTRAP_SERVERS)
            admin.list_topics()
            logging.info("Kafka is available.")
            return
        except KafkaError as e:
            logging.debug(f"Kafka not ready yet: {e}")
            time.sleep(2)
    logging.error("Timeout waiting for Kafka to become available.")
    sys.exit(1)

# Remove all old Images from Docker - only relevant for development
def remove_old_images():
    logging.info("Looking for old segment/start-goal images to remove...")
    try:
        result = subprocess.run(
            ["docker", "images", "--format", "{{.Repository}}:{{.Tag}} {{.ID}}"],
            check=True,
            stdout=subprocess.PIPE,
            text=True
        )
        lines = result.stdout.strip().split("\n")
        to_remove = []
        for line in lines:
            parts = line.split()
            if len(parts) != 2:
                continue
            name, image_id = parts
            if name.startswith("src-segment-") or name.startswith("src-start-and-goal-"):
                to_remove.append(image_id)
        if not to_remove:
            logging.info("No old segment/start-goal images found.")
            return
        logging.info(f"Removing {len(to_remove)} image(s): {to_remove}")
        subprocess.run(["docker", "rmi", "-f"] + to_remove, check=True)
        logging.info("Old images removed successfully.")
    except subprocess.CalledProcessError as e:
        logging.warning(f"Failed to remove old images: {e}")

# Run Setup for map and docker-compose.yaml
def run_setup(mode: str, num_tracks=None, track_length=None):
    if mode == "--generate":
        if not num_tracks or not track_length:
            logging.error("Missing parameters for map generation.")
            sys.exit(1)
        cmd = ["python", "setup.py", "--generate", str(num_tracks), str(track_length)]
    elif mode == "--use-existing":
        cmd = ["python", "setup.py", "--use-existing"]
    else:
        logging.error(f"Unknown setup mode: {mode}")
        sys.exit(1)
    logging.info(f"Running setup: {' '.join(cmd)}")
    subprocess.run(cmd, check=True)

# Read Number Segments from map.json
def load_segment_count():
    if not os.path.exists(MAP_FILE):
        logging.error(f"Map file '{MAP_FILE}' not found.")
        sys.exit(1)
    with open(MAP_FILE, "r", encoding="utf-8") as f:
        data = json.load(f)
    return data.get("totalSegments", 0)

# Listen for Alive Message from all Segment Containers
def listen_for_alive_messages(expected_count):
    logging.info(f"Waiting for {expected_count} 'I am alive' messages...")
    consumer = KafkaConsumer(
        STATUS_TOPIC,
        bootstrap_servers=KAFKA_BOOTSTRAP_SERVERS,
        group_id="runpy-controller-",
        value_deserializer=lambda m: json.loads(m.decode("utf-8")),
        key_deserializer=lambda k: k.decode("utf-8") if k else None,
        auto_offset_reset="earliest",
        enable_auto_commit=True
    )
    received_segments = set()
    for msg in consumer:
        if should_exit:
            break
        data = msg.value
        segment_id = data.get("segmentId")
        status = data.get("status")
        if status == "alive" and segment_id not in received_segments:
            received_segments.add(segment_id)
            logging.info(f"[{len(received_segments)}/{expected_count}] Alive: {segment_id}")
        if len(received_segments) >= expected_count:
            logging.info("All segments reported alive.")
            break


def main():
    parser = argparse.ArgumentParser(description="Run the Ave Ceasar microservice system.")
    group = parser.add_mutually_exclusive_group(required=True)
    group.add_argument("--generate", action="store_true", help="Generate a new map")
    group.add_argument("--use-existing", action="store_true", help="Use existing map")
    parser.add_argument("num_tracks", nargs="?", type=int, help="Number of tracks (with --generate)")
    parser.add_argument("track_length", nargs="?", type=int, help="Track length (with --generate)")
    parser.add_argument("--verbose", action="store_true", help="Enable debug logging")
    args = parser.parse_args()

    configure_logging(verbose=args.verbose)
    signal.signal(signal.SIGINT, handle_exit)
    signal.signal(signal.SIGTERM, handle_exit)

    if args.generate:
        if args.num_tracks is None or args.track_length is None:
            logging.error("Missing arguments for --generate. Required: num_tracks and track_length.")
            sys.exit(1)
        run_setup("--generate", args.num_tracks, args.track_length)
    elif args.use_existing:
        run_setup("--use-existing")

    segment_count = load_segment_count()

    if segment_count > MAX_SEGMENTS:
        logging.info(f"[⚠] Too many segments ({segment_count})! Limit is {MAX_SEGMENTS} due to Docker Compose limitations. Reduce tracks or track length.")
        sys.exit(1)

    remove_old_images()
    start_docker_compose()
    wait_for_kafka_ready()

    try:
        listen_for_alive_messages(segment_count)
    except KeyboardInterrupt:
        logging.info("Interrupted by user.")
    finally:
        stop_docker_compose()


if __name__ == "__main__":
    main()