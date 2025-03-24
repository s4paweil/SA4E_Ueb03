import os
import json
import threading
import time
import logging
from kafka import KafkaProducer, KafkaConsumer
from kafka.errors import NoBrokersAvailable
import random

# === Logging Setup ===
logging.basicConfig(level=logging.INFO,
                    format="%(asctime)s [%(levelname)s] %(message)s")

# === Config from Environment ===
SEGMENT_ID = os.environ.get("SEGMENT_ID")
SEGMENT_TYPE = os.environ.get("SEGMENT_TYPE")
NEXT_SEGMENTS = os.environ.get("NEXT_SEGMENTS").split(",")
KAFKA_BOOTSTRAP_SERVERS = ['kafka-1:19092', 'kafka-2:19092', 'kafka-3:19092']

STATUS_TOPIC = "segment-status"
MOVEMENT_TOPIC = "token-movement"

if not SEGMENT_ID:
    raise ValueError("Missing required environment variable: SEGMENT_ID")
if not SEGMENT_TYPE:
    raise ValueError("Missing required environment variable: SEGMENT_TYPE")
if not NEXT_SEGMENTS:
    raise ValueError("Missing required environment variable: NEXT_SEGMENTS")

def wait_for_kafka(max_retries=20, delay=3):
    for attempt in range(max_retries):
        try:
            producer = KafkaProducer(
                bootstrap_servers=KAFKA_BOOTSTRAP_SERVERS,
                value_serializer=lambda v: json.dumps(v).encode("utf-8"),
                key_serializer=lambda k: str(k).encode("utf-8"),
                retries=5
            )
            logging.info("Kafka connection established.")
            return producer
        except NoBrokersAvailable as e:
            logging.warning(f"Kafka connection failed. Retrying in {delay} seconds...")
            time.sleep(delay)
    raise ConnectionError("Could not connect to Kafka after multiple retries.")

def forward_token(producer, player_id, path, steps_left, rounds_left):
    if steps_left <= 0:
        logging.debug(f"[Stop] Player {player_id} hat keine Schritte mehr.")
        return

    if not NEXT_SEGMENTS or NEXT_SEGMENTS[0] == SEGMENT_ID:
        logging.debug(f"[Dead-End] Player {player_id} reached a dead end at {SEGMENT_ID}")
        return

    next_seg = NEXT_SEGMENTS[0]

    # Token für nächsten Schritt
    message = {
        "type": "move",
        "playerId": player_id,
        "remainingSteps": steps_left - 1,
        "roundsLeft": rounds_left,
        "path": path + [next_seg]
    }

    producer.send(next_seg, key=player_id, value=message)
    producer.flush()


# === Nachricht vom eigenen Topic empfangen ===
def consume_loop(producer):
    logging.info(f"Starting consumer for topic '{SEGMENT_ID}'...")
    consumer = KafkaConsumer(
        SEGMENT_ID,
        bootstrap_servers=KAFKA_BOOTSTRAP_SERVERS,
        group_id=f"group-{SEGMENT_ID}",
        value_deserializer=lambda m: json.loads(m.decode("utf-8")),
        key_deserializer=lambda k: k.decode("utf-8") if k else None,
        auto_offset_reset="earliest",
        enable_auto_commit=True
    )

    for msg in consumer:
        value = msg.value
        player_id = value.get("playerId")

        if value.get("type") == "start":
            rounds_left = value.get("roundsLeft", 1)
            steps = random.randint(1, 3)

            logging.info(f"[Start] Player {player_id} begins with {steps} steps and {rounds_left} rounds.")

            # Startbewegung
            move_msg = {
                "type": "move",
                "playerId": player_id,
                "remainingSteps": steps - 1,
                "roundsLeft": rounds_left,
                "path": [SEGMENT_ID]
            }
            forward_token(
                producer,
                player_id,
                [SEGMENT_ID],
                steps - 1,
                rounds_left
            )
            producer.flush()

        elif value.get("type") == "move":
            remaining = value.get("remainingSteps", 0)
            rounds_left = value.get("roundsLeft", 1)
            path = value.get("path", [SEGMENT_ID])

            logging.info(f"[Move] Player {player_id} moved to {SEGMENT_ID}, remaining steps: {remaining}, rounds left: {rounds_left}")

            # Wenn Zielsegment wieder Startsegment: Runde abgeschlossen
            if SEGMENT_TYPE == "start-goal" and len(path) > 1:
                rounds_left -= 1
                if rounds_left == 0:
                    logging.info(f"[Finish] Player {player_id} hat das Ziel erreicht!")
                    # finale Movement-Log senden
                    producer.send(MOVEMENT_TOPIC, key=player_id, value={
                        "playerId": player_id,
                        "path": path,
                        "roundsLeft": 0
                    })
                    producer.flush()
                    continue

            # sonst: weiterziehen
            time.sleep(1)
            forward_token(producer, player_id, path, remaining, rounds_left)

        elif value.get("type") == "scout":
            logging.info(f"[Scout] Player {player_id} scoutet durch {SEGMENT_ID}")



# === Main ===
def main():
    producer = wait_for_kafka()

    # Alive-Nachricht
    alive_msg = {
        "segmentId": SEGMENT_ID,
        "type": SEGMENT_TYPE,
        "status": "alive"
    }
    producer.send(STATUS_TOPIC, key=SEGMENT_ID, value=alive_msg)
    producer.flush()
    logging.info(f"Sent alive message for segment {SEGMENT_ID} to {STATUS_TOPIC}")

    # Consumer starten
    consumer_thread = threading.Thread(target=consume_loop, args=(producer,), daemon=True)
    consumer_thread.start()

    try:
        while True:
            time.sleep(10)
    except KeyboardInterrupt:
        logging.info("Shutting down...")

if __name__ == "__main__":
    main()