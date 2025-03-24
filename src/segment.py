import os
import json
import threading
import time
import logging
from kafka import KafkaProducer, KafkaConsumer
from kafka.errors import NoBrokersAvailable

# === Logging Setup ===
logging.basicConfig(level=logging.INFO,
                    format="%(asctime)s [%(levelname)s] %message)s")

# === Config from Environment ===
SEGMENT_ID = os.environ.get("SEGMENT_ID")
SEGMENT_TYPE = os.environ.get("SEGMENT_TYPE")
NEXT_SEGMENTS = os.environ.get("NEXT_SEGMENTS").split(",")
KAFKA_BOOTSTRAP_SERVERS = ['kafka-1:19092', 'kafka-2:19092', 'kafka-3:19092']

STATUS_TOPIC = "segment-status"

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


# === Nachricht vom eigenen Topic empfangen ===
def consume_loop():
    logging.info(f"Starting consumer for topic '{SEGMENT_ID}'...")
    consumer = KafkaConsumer(
        SEGMENT_ID,
        bootstrap_servers=KAFKA_BOOTSTRAP_SERVERS.split(","),
        group_id=f"group-{SEGMENT_ID}",
        value_deserializer=lambda m: json.loads(m.decode("utf-8")),
        key_deserializer=lambda k: k.decode("utf-8") if k else None,
        auto_offset_reset="earliest",
        enable_auto_commit=True
    )

    for msg in consumer:
        key = msg.key
        value = msg.value
        logging.info(f"Received message on '{SEGMENT_ID}': Key={key}, Value={value}")

        # Hier kann später je nach Nachrichtentyp reagiert werden
        if value.get("type") == "scout":
            logging.info(f"[Scout] Player {value.get('playerId')} scoutet durch {SEGMENT_ID}")

        elif value.get("type") == "move":
            logging.info(f"[Move] Player {value.get('playerId')} bewegt sich nach {SEGMENT_ID}")

        # Weitere Logik folgt später


# === Main ===
def main():
    producer = wait_for_kafka()

    # Alive-Nachricht an zentrales Status-Topic senden
    alive_msg = {
        "segmentId": SEGMENT_ID,
        "type": SEGMENT_TYPE,
        "status": "alive"
    }

    producer.send(STATUS_TOPIC, key=SEGMENT_ID, value=alive_msg)
    producer.flush()

    logging.info(f"Sent alive message for segment {SEGMENT_ID} to {STATUS_TOPIC}")

    # Starte den Consumer-Thread
    consumer_thread = threading.Thread(target=consume_loop, daemon=True)
    consumer_thread.start()

    # Keep process alive
    try:
        while True:
            time.sleep(10)
    except KeyboardInterrupt:
        logging.info("Shutting down...")

if __name__ == "__main__":
    main()