from dataclasses import dataclass
import json
import logging
import os
from random import randint
import random
import string
import sys
import threading
import time
from kafka import KafkaConsumer, KafkaProducer
from kafka.errors import NoBrokersAvailable
from kafka.admin import KafkaAdminClient
from kafka.errors import KafkaError

# === Logging Setup ===
#logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] [%(threadName)s] %(message)s"
)

# === Config ===
SEGMENT_ID = os.environ.get("SEGMENT_ID")
SEGMENT_TYPE = os.environ.get("SEGMENT_TYPE")
NEXT_SEGMENTS = os.environ.get("NEXT_SEGMENTS", "").split(",")
KAFKA_BOOTSTRAP_SERVERS = ["kafka-1:19092", "kafka-2:19092", "kafka-3:19092"]

STATUS_TOPIC = "segment-status"
MOVEMENT_TOPIC = "token-movement"
FINISHED_PLAYERS_TOPIC = "finished-players"

ROUNDS = 2
SLEEP_TIME = 5

# === Helper ===
@dataclass(frozen=True)
class Target:
    segment_id: str
    segment_type: str
    path: str

    def to_dict(self):
        return {
            "segment_id": self.segment_id,
            "segment_type": self.segment_type,
            "path": self.path
        }

def target_from_dict(d):
    return Target(d["segment_id"], d["segment_type"], d["path"])

@dataclass
class ScoutingState:
    scouting_segment_index: int
    scouting_path: list[str]
    scouting_steps: int
    scouting_for_card: int
    scouting_origin: str
    player: dict
    scouting_possible_targets: set[Target]
    scouting_visited_segments: list[str]
    scouting_targets_found_per_card: dict[str, set[Target]]

occupied = False
scouting_states = {}


# Waiting for Kafka Container to be ready
def wait_for_kafka_ready(timeout=60):
    logging.info("[DEBUG] Entered wait_for_kafka_ready")
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
    logging.info("[DEBUG] Entered wait_for_kafka_producer")
    for _ in range(20):
        try:
            return KafkaProducer(
                bootstrap_servers=KAFKA_BOOTSTRAP_SERVERS,
                value_serializer=lambda v: json.dumps(v).encode("utf-8")
            )
        except NoBrokersAvailable:
            #logging.warning("[WAIT] Kafka producer not available yet. Retrying...")
            time.sleep(2)
    raise ConnectionError("Failed to connect to Kafka as producer.")

def wait_for_kafka_consumer(topic):
    logging.info("[DEBUG] Entered wait_for_kafka_consumer")
    for _ in range(20):
        try:
            return KafkaConsumer(
                topic,
                bootstrap_servers=KAFKA_BOOTSTRAP_SERVERS,
                value_deserializer=lambda m: json.loads(m.decode("utf-8"))
            )
        except NoBrokersAvailable:
            logging.warning("[WAIT] Kafka consumer not available yet. Retrying...")
            time.sleep(2)
    raise ConnectionError("Failed to connect to Kafka as consumer.")

def scout(state: ScoutingState, producer):
    logging.info("[DEBUG] Entered scout()")
    state.scouting_path.append(NEXT_SEGMENTS[state.scouting_segment_index])
    if NEXT_SEGMENTS[state.scouting_segment_index] in state.scouting_visited_segments:
        scout_next_segment(state, producer)
        return
    
    targets = [target.to_dict() for target in state.scouting_possible_targets]

    #producer.partitions_for(NEXT_SEGMENTS[state.scouting_segment_index])

    producer.send(NEXT_SEGMENTS[state.scouting_segment_index], value={
        "event": "scout",
        "scouting_origin": state.scouting_origin,
        "player": state.player,
        "scouting_steps": state.scouting_steps,
        "scouting_path": state.scouting_path,
        "scouting_for_card": state.scouting_for_card,
        "scouting_possible_targets": targets,
        "scouting_visited_segments": state.scouting_visited_segments
    })

def start_scouting(player, origin: str, scouting_path: list[str], scouting_steps, scouting_card, scouting_targets, scouting_visited, producer):
    logging.info("[DEBUG] Entered start_scouting()")
    state = ScoutingState(
        player = player,
        scouting_origin = origin,
        scouting_segment_index = 0,
        scouting_path = scouting_path,
        scouting_steps = scouting_steps,
        scouting_for_card = scouting_card,
        scouting_possible_targets = scouting_targets,
        scouting_targets_found_per_card = {},
        scouting_visited_segments = scouting_visited
    )
    scout(state, producer)
    scouting_states[origin] = state

def handle_scouting_results(scouting_origin, scouting_path, possible_targets, scouting_visited_segments, producer):
    logging.info("[DEBUG] Entered handle_scouting_results()")
    state = scouting_states[scouting_origin]

    for segment in scouting_visited_segments:
        if segment not in state.scouting_visited_segments:
            state.scouting_visited_segments.append(segment)
    
    for target in possible_targets:
        state.scouting_possible_targets.add(target)

    finished_scouting = scout_next_segment(state, producer)
    if finished_scouting and scouting_origin == SEGMENT_ID:
        apply_best_move(state, producer)


def apply_best_move(state: ScoutingState, producer):
    logging.info("[DEBUG] Entered apply_best_move()")
    
    global occupied

    possible_targets = [target for targets in state.scouting_targets_found_per_card.values() for target in targets]
    if len(possible_targets) == 0:
        logging.info(f"[MOVE] {state.player['player_id']} has no possible targets. Stuck.")
        producer.send(MOVEMENT_TOPIC, value={
            "message": f"[MOVE] Player {state.player['player_id']} has no possible targets. Stuck and waiting..."
        })
        time.sleep(SLEEP_TIME)
        start_scouting(state.player, SEGMENT_ID, [SEGMENT_ID], state.player["cards"][0], state.player["cards"][0], set(), [], producer)
        return
    
    preferred_card = None
    preferred_target = None

    # Bevorzuge Caesar Felder, wenn noch nicht gegrüsst
    if not state.player["has_greeted_caesar"]:
        for(card, targets) in state.scouting_targets_found_per_card.items():
            for target in targets:
                if target.segment_type == "caesar":
                    preferred_card = card
                    preferred_target = target
                    break

    if preferred_card is None or preferred_target is None:
        highest_card = 0
        for (card, targets) in state.scouting_targets_found_per_card.items():
            if int(card) > highest_card and len(targets) > 0:
                highest_card = int(card)
                preferred_card = highest_card
                preferred_target = list(targets)[0]

    if preferred_card is None or preferred_target is None:
        raise Exception("No preferred card or target found.")
    
    occupied = False
    cards : list[int] = state.player["cards"]
    for i in range(len(cards)):
        if cards[i] == preferred_card:
            cards.pop(i)
            break
    state.player["cards"] = cards

    #producer.partition_for(preferred_target.segment_id)

    producer.send(preferred_target.segment_id, value={
        "event": "move",
        "player": state.player,
        "path": preferred_target.path
    })

def scout_next_segment(state: ScoutingState, producer):
    logging.info("[DEBUG] Entered scout_next_segment()")
    player = state.player
    request_origin = state.scouting_origin
    cards : list[int] = player["cards"]
    segment_index = state.scouting_segment_index
    state.scouting_path.pop() # Remove last segment from path
    state.scouting_path.pop() # Remove last segment from path
    previous_segment = None
    if len(state.scouting_path) > 0:
        previous_segment = state.scouting_path.pop()

    # Try next segment
    if segment_index is not None and segment_index < len(NEXT_SEGMENTS) - 1:
        segment_index += 1
        state.scouting_segment_index = segment_index
        if previous_segment is not None:
            state.scouting_path.append(previous_segment)
        state.scouting_path.append(SEGMENT_ID)
        scout(state, producer)
    # Try next card if scouting startet here
    else:
        next_card = None
        if request_origin == SEGMENT_ID:
            previous_card = state.scouting_for_card
            state.scouting_targets_found_per_card[str(previous_card)] = state.scouting_possible_targets
            for card in cards:
                if str(card) not in state.scouting_targets_found_per_card.keys():
                    next_card = card
                
        if next_card is not None:
            state.scouting_segment_index = 0
            state.scouting_for_card = next_card
            state.scouting_steps = next_card
            state.scouting_path = [SEGMENT_ID]
            state.scouting_possible_targets = set()
            state.scouting_visited_segments = []

            scout(state, producer)
        # Continue scouting
        else:
            if previous_segment is not None:
                targets = [target.to_dict() for target in state.scouting_possible_targets]
                producer.send(previous_segment, value={
                    "event": "scouting_results",
                    "scouting_origin": request_origin,
                    "player": player,
                    "scouting_possible_targets": targets,
                    "scouting_visited_segments": state.scouting_visited_segments + [SEGMENT_ID]
                })
            else:
                return True
    return False



# === Handle Movement === #
def handle_move(player, planned_path, producer):
    logging.info("[DEBUG] Entered handle_move()")
    global occupied

    path = planned_path.split(",")
    occupied = True
    logging.info(f"[MOVE] {player['player_id']} moving from {path[0]} to {SEGMENT_ID} using path: {path}")
    producer.send(MOVEMENT_TOPIC, value={
        "message": f"[MOVE] Player {player['player_id']} moving from {path[0]} to {SEGMENT_ID} using card {len(path)-1} following path: {path}"
    })

    if SEGMENT_TYPE == "caesar":
        player["has_greeted_caesar"] = True
        logging.info(f"[GREET] {player['player_id']} greeted Caesar.")
        producer.send(MOVEMENT_TOPIC, value={
            "message": f"[GREET] Player {player['player_id']} greeted Caesar."
        })
    caesar_field_visited_on_path = False
    for (i, segment) in enumerate(path):
        if i == 0:
            continue
        if segment.startswith("start-and-goal-") or (segment.startswith("caesar") and caesar_field_visited_on_path == False):
            caesar_field_visited_on_path = True
            player["round"] += 1
            if player["round"] == ROUNDS:
                logging.info(f"[FINISH] Player {player['player_id']} finished the game.")
                producer.send(FINISHED_PLAYERS_TOPIC, value=player)
                occupied = False
                return
            else:
                logging.info(f"[ROUND] Player {player['player_id']} finished round {player['round']}")
                producer.send(MOVEMENT_TOPIC, value={
                    "message": f"[ROUND] Player {player['player_id']} finished round {player['round']}"
                })
    
    player["cards"].append(randint(1, 6))
    time.sleep(SLEEP_TIME)

    if (len(player["cards"]) == 0):
        logging.info(f"[MOVE] Player {player['player_id']} has no more cards.")
        producer.send(MOVEMENT_TOPIC, value={
            "message": f"[MOVE] Player {player['player_id']} has no more cards."
        })
        producer.send(FINISHED_PLAYERS_TOPIC, value=player)
        occupied = False
        return
    else:
        start_scouting(player, SEGMENT_ID, [SEGMENT_ID], player["cards"][0], player["cards"][0], set(), [], producer)

def target_found(scouting_origin, scouting_path, scouting_for_card, producer, player):
    logging.info("[DEBUG] Entered target_found()")
    target = Target(segment_id=SEGMENT_ID, segment_type=SEGMENT_TYPE, path=",".join(scouting_path)).to_dict()
    target_answer : list[dict] = [target]
    #producer.partition_for(scouting_path[-2])
    producer.send(scouting_path[-2], value={
        "event": "scouting_results",
        "scouting_origin": scouting_origin,
        "scouting_path": scouting_path,
        "scouting_possible_targets": target_answer,
        "scouting_visited_segments": [SEGMENT_ID]
    })

def handle_scouting(scouting_origin, scouting_steps, player, scouting_path, scouting_for_card, scouting_visited_segments, possible_targets, producer):
    logging.info("[DEBUG] Entered handle_scouting()")
    global occupied

    if occupied and scouting_origin != SEGMENT_ID:
        scouting_path.pop()
        last_segment = scouting_path.pop()
        producer.send(last_segment, value={
            "event": "scouting_results",
            "scouting_origin": scouting_origin,
            "scouting_possible_targets": dict(),
            "scouting_visited_segments": [SEGMENT_ID]
        })
        return
    
    if scouting_steps == 1:
        target_found(scouting_origin, scouting_path, scouting_for_card, producer, player)
    else:
        start_scouting(
            player,
            scouting_origin,
            scouting_path,
            scouting_steps - 1,
            scouting_for_card,
            possible_targets,
            scouting_visited_segments,
            producer
        )
                

# Handle incoming Messages
def handle_messsage(msg, producer):
    try:
        logging.info(f"[DEBUG] Received message: {msg}")

        possible_targets_json : list[dict] | None = msg.get("scouting_possible_targets")
        possible_targets = set([target_from_dict(target) for target in possible_targets_json or []])

        if msg.get("event") == "start":
            start_scouting(
                msg.get("player"),
                SEGMENT_ID,
                [SEGMENT_ID],
                msg.get("player").get("cards")[0],
                msg.get("player").get("cards")[0],
                set(),
                [],
                producer
            )
        elif msg.get("event") == "scouting_results":
            handle_scouting_results(
                msg.get("scouting_origin"),
                msg.get("scouting_path"),
                possible_targets,
                msg.get("scouting_visited_segments"),
                producer
            )
        elif msg.get("event") == "scout":
            handle_scouting(
                msg.get("scouting_origin"),
                msg.get("scouting_steps"),
                msg.get("player"),
                msg.get("scouting_path"),
                msg.get("scouting_for_card"),
                msg.get("scouting_visited_segments"),
                possible_targets,
                producer
            )
        elif msg.get("event") == "move":
            handle_move(msg.get("player"), msg.get("path"), producer)
    except Exception as e:
        logging.error(f"[EXCEPTION] in handle_message {e}", exc_info=True)
    


def generate_thread_id(length=5):
    return ''.join(random.choices(string.ascii_uppercase + string.digits, k=length))

    

# === Main ===
def main():
    wait_for_kafka_ready()
    time.sleep(10)
    consumer = wait_for_kafka_consumer(SEGMENT_ID)
    producer = wait_for_kafka_producer()

    producer.send(STATUS_TOPIC, value={
        "segmentId": SEGMENT_ID,
        "type": SEGMENT_TYPE,
        "status": "alive"
    })
    producer.flush()
    logging.info(f"[ALIVE] {SEGMENT_ID} meldet sich lebendig.")

    try:
        for msg in consumer:
            threading.Thread(target=handle_messsage, name=f"Thread-{generate_thread_id()}" ,args=(msg.value, producer)).start()
    except KeyboardInterrupt:
        logging.info("Interrupted by user.")
    

if __name__ == "__main__":
    main()