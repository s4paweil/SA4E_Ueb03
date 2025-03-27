import json
import logging
import os
import sys
import time
from kafka import KafkaConsumer, KafkaProducer
from kafka.errors import NoBrokersAvailable
from kafka.admin import KafkaAdminClient
from kafka.errors import KafkaError

# === Logging Setup ===
logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")

# === Config ===
SEGMENT_ID = os.environ.get("SEGMENT_ID")
SEGMENT_TYPE = os.environ.get("SEGMENT_TYPE")
NEXT_SEGMENTS = os.environ.get("NEXT_SEGMENTS", "").split(",")
KAFKA_BOOTSTRAP_SERVERS = ["kafka-1:19092", "kafka-2:19092", "kafka-3:19092"]

STATUS_TOPIC = "segment-status"
MOVEMENT_TOPIC = "token-movement"

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

# === Kafka Producer Setup ===
def wait_for_kafka_producer():
    for _ in range(20):
        try:
            return KafkaProducer(
                bootstrap_servers=KAFKA_BOOTSTRAP_SERVERS,
                value_serializer=lambda v: json.dumps(v).encode("utf-8"),
                key_serializer=lambda k: k.encode("utf-8")
            )
        except NoBrokersAvailable:
            logging.warning("[WAIT] Kafka producer not available yet. Retrying...")
            time.sleep(2)
    raise ConnectionError("Failed to connect to Kafka as producer.")

def wait_for_kafka_consumer(topic):
    for _ in range(20):
        try:
            return KafkaConsumer(
                topic,
                bootstrap_servers=KAFKA_BOOTSTRAP_SERVERS,
                value_deserializer=lambda m: json.loads(m.decode("utf-8")),
                key_deserializer=lambda k: k.decode("utf-8") if k else None,
                auto_offset_reset="earliest",
                enable_auto_commit=True,
                group_id=f"group-{topic}"            )
        except NoBrokersAvailable:
            logging.warning("[WAIT] Kafka consumer not available yet. Retrying...")
            time.sleep(2)
    raise ConnectionError("Failed to connect to Kafka as consumer.")

# === Main ===
def main():
    wait_for_kafka_ready()
    time.sleep(10)
    #consumer = wait_for_kafka_consumer(SEGMENT_ID)
    producer = wait_for_kafka_producer()

    producer.send(STATUS_TOPIC, key=SEGMENT_ID, value={
        "segmentId": SEGMENT_ID,
        "type": SEGMENT_TYPE,
        "status": "alive"
    })
    producer.flush()
    logging.info(f"[ALIVE] {SEGMENT_ID} meldet sich lebendig.")

if __name__ == "__main__":
    main()