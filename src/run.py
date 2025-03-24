import subprocess
import json
import os
import sys
import time
import logging
from kafka import KafkaConsumer
from kafka.admin import KafkaAdminClient
from kafka.errors import KafkaError
import threading
import signal
import argparse

MAP_FILE = "map.json"
KAFKA_BOOTSTRAP_SERVERS = ['localhost:29092', 'localhost:39092', 'localhost:49092']
STATUS_TOPIC = "segment-status"
COMPOSE_FILE = "docker-compose.yaml"

should_exit = False

def configure_logging(verbose=False):
    level = logging.DEBUG if verbose else logging.INFO

    # Root logger + alle bestehenden Handler entfernen
    root_logger = logging.getLogger()
    for handler in root_logger.handlers[:]:
        root_logger.removeHandler(handler)

    # Globaler Formatter + StreamHandler
    console_handler = logging.StreamHandler()
    console_handler.setFormatter(logging.Formatter("%(asctime)s [%(levelname)s] %(message)s"))
    console_handler.setLevel(level)
    root_logger.addHandler(console_handler)
    root_logger.setLevel(level)

    # Alle kafka-spezifischen Logger runterdrehen
    kafka_level = logging.DEBUG if verbose else logging.CRITICAL  # <-- CRITICAL blockt alles darunter
    for name in logging.root.manager.loggerDict:
        if name.startswith("kafka"):
            logging.getLogger(name).setLevel(kafka_level)


def load_segment_count():
    if not os.path.exists(MAP_FILE):
        logging.error(f"Map file '{MAP_FILE}' not found.")
        sys.exit(1)

    with open(MAP_FILE, "r", encoding="utf-8") as f:
        data = json.load(f)
    return data.get("totalSegments", 0)

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

def start_docker_compose():
    logging.info("Starting Docker containers...")
    subprocess.run(["docker-compose", "up", "-d"], check=True)
    logging.info("Docker containers started.")

def stop_docker_compose():
    logging.info("Stopping all containers...")
    subprocess.run(["docker-compose", "down", "-v"], check=True)
    logging.info("Containers stopped.")

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

def listen_for_alive_messages(expected_count):
    logging.info(f"Waiting for {expected_count} 'I am alive' messages...")

    consumer = KafkaConsumer(
        STATUS_TOPIC,
        bootstrap_servers=KAFKA_BOOTSTRAP_SERVERS,
        group_id="runpy-controller",
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

def handle_exit(sig, frame):
    global should_exit
    logging.info("Received signal. Shutting down...")
    should_exit = True
    stop_docker_compose()
    sys.exit(0)

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
