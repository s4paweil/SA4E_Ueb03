import json
import os
import random
import sys
import uuid
import yaml

MAP_FILE = "map.json"
COMPOSE_FILE = "docker-compose.yaml"
SEGMENT_IMAGE = "segment-service:latest"
KAFKA_NETWORK = "kafka-net"

def generate_tracks(num_tracks: int, length_of_track: int):
    all_tracks = []
    total_segments = 0

    # Caesar-Gasse als separater Track (liegt "oberhalb" von Track 1)
    caesar_track = {
        "trackId": "caesar",
        "segments": []
    }
    caesar_ids = [f"caesar-{i}" for i in range(3)]
    for i, seg_id in enumerate(caesar_ids):
        next_seg = caesar_ids[i + 1] if i < 2 else "segment-1-1"
        caesar_track["segments"].append({
            "segmentId": seg_id,
            "type": "caesar",
            "nextSegments": [next_seg]
        })
        total_segments += 1
    all_tracks.append(caesar_track)

    # Erzeugen der Reihenfolge der besonderen Segmenttypen für Tracks
    segment_types = []
    segment_types.append("start-goal")
    segment_types.append("normal")
    for i in range(1, length_of_track):
        if random.randint(1, 100) <= 10:
            segment_types.append("bottleneck")
        elif random.randint(1, 100) <= 30:
            segment_types.append("wall-divided")
        else:
            segment_types.append("normal")

    # Erzeugen der Tracks
    for t in range(1, num_tracks + 1):
        track_id = str(t)
        segments = []

        # Start-and-goal Segment
        start_segment_id = f"start-and-goal-{t}"
        next_segments = [f"segment-{t}-1"]
        if t > 1:
            next_segments.append(f"segment-{t-1}-1")
        if t < num_tracks:
            next_segments.append(f"segment-{t+1}-1")
        
        segments.append({
            "segmentId": start_segment_id,
            "type": "start-goal",
            "nextSegments": next_segments
        })
        total_segments += 1

        # Erzeugen der weitere Segmente
        for i in range(1, length_of_track):
            seg_type = segment_types[i]
            if seg_type == "bottleneck" and t != 1:
                continue

            seg_id = f"segment-{t}-{i}"
            next_segments = []
            next_index = i + 1
            next_type = segment_types[next_index]

            if i == length_of_track - 1:
                seg_type = "normal"
                next_segments.append(f"start-and-goal-{t}")
                if t > 1:
                    next_segments.append(f"start-and-goal-{t-1}")
                if t < num_tracks:
                    next_segments.append(f"start-and-goal-{t+1}")
                if t == 1:
                    next_segments.append("caesar-0")
            elif seg_type == "wall-divided":
                if next_type == "botteckneck":
                    next_segments.append(f"segment-1-{next_index}")
                else:
                    next_segments.append(f"segment-{t}-{next_index}")
            elif seg_type == "bottleneck" and t == 1:
                if next_type == "bottleneck":
                    next_segments.append(f"segment-1-{next_index}")
                else:
                    for j in range(1, num_tracks + 1):
                        next_segments.append(f"segment-{j}-{next_index}")
            else:
                if next_type == "bottleneck":
                    next_segments.append(f"segment-1-{next_index}")
                else:
                    next_segments.append(f"segment-{t}-{next_index}")
                    if t > 1:
                        next_segments.append(f"segment-{t-1}-{next_index}")
                    if t < num_tracks:
                        next_segments.append(f"segment-{t+1}-{next_index}")

            segments.append({
                "segmentId": seg_id,
                "type": seg_type,
                "nextSegments": next_segments
            })
            total_segments += 1

        all_tracks.append({
            "trackId": str(t),
            "segments": segments
        })

    return {
        "totalSegments": total_segments,
        "tracks": all_tracks
    }

# ==== DOCKER COMPOSE GENERATION ====
def generate_compose(map_data):
    kafka_cluster_id = "4L6g3nShT-eMCtK--X86sw"
    compose = {
        "version": "3.9",
        "networks": {
            KAFKA_NETWORK: {}
        },
        "services": {}
    }

    kafka_ports = [29092, 39092, 49092]
    for i in range(1, 4):
        broker_name = f"kafka-{i}"
        host_port = kafka_ports[i - 1]

        service = {
            "image": "apache/kafka-native",
            "hostname": broker_name,
            "container_name": broker_name,
            "ports": [f"{host_port}:9092"],
            "environment": {
                "KAFKA_NODE_ID": str(i),
                "KAFKA_PROCESS_ROLES": "broker,controller",
                "KAFKA_LISTENER_SECURITY_PROTOCOL_MAP": "CONTROLLER:PLAINTEXT,PLAINTEXT:PLAINTEXT,PLAINTEXT_HOST:PLAINTEXT",
                "KAFKA_CONTROLLER_QUORUM_VOTERS": "1@kafka-1:9093,2@kafka-2:9093,3@kafka-3:9093",
                "KAFKA_LISTENERS": "PLAINTEXT://:19092,CONTROLLER://:9093,PLAINTEXT_HOST://:9092",
                "KAFKA_INTER_BROKER_LISTENER_NAME": "PLAINTEXT",
                "KAFKA_ADVERTISED_LISTENERS": f"PLAINTEXT://{broker_name}:19092,PLAINTEXT_HOST://localhost:{host_port}",
                "KAFKA_CONTROLLER_LISTENER_NAMES": "CONTROLLER",
                "CLUSTER_ID": kafka_cluster_id,
                "KAFKA_OFFSETS_TOPIC_REPLICATION_FACTOR": "1",
                "KAFKA_GROUP_INITIAL_REBALANCE_DELAY_MS": "0",
                "KAFKA_TRANSACTION_STATE_LOG_MIN_ISR": "1",
                "KAFKA_TRANSACTION_STATE_LOG_REPLICATION_FACTOR": "1",
                "KAFKA_SHARE_COORDINATOR_STATE_TOPIC_REPLICATION_FACTOR": "1",
                "KAFKA_SHARE_COORDINATOR_STATE_TOPIC_MIN_ISR": "1",
                "KAFKA_LOG_DIRS": "/tmp/kraft-combined-logs"
            },
            "networks": [KAFKA_NETWORK]
        }

        compose["services"][broker_name] = service

    # Segment-Container definieren
    for track in map_data["tracks"]:
        for segment in track["segments"]:
            seg_id = segment["segmentId"]
            service = {
                "build": {
                    "context": ".",
                    "dockerfile": "Dockerfile.segment",
                    "args": {
                        "CACHE_BUSTER": str(uuid.uuid4())
                    }
                },
                "container_name": seg_id,
                "environment": {
                    "SEGMENT_ID": seg_id,
                    "SEGMENT_TYPE": segment["type"],
                    "NEXT_SEGMENTS": ",".join(segment["nextSegments"]),
                    "KAFKA_BOOTSTRAP_SERVERS": "kafka-1:19092,kafka-2:19092,kafka-3:19092"
                },
                "depends_on": {
                    "kafka-1": {"condition": "service_started"},
                    "kafka-2": {"condition": "service_started"},
                    "kafka-3": {"condition": "service_started"}
                },
                "networks": [KAFKA_NETWORK]
            }
            compose["services"][seg_id] = service

    return compose




def main():
    if len(sys.argv) < 2:
        print("Usage:")
        print("  setup.py --generate <num_tracks> <track_length>")
        print("  setup.py --use-existing")
        sys.exit(1)

    mode = sys.argv[1]

    if mode == "--generate":
        if len(sys.argv) != 4:
            print("Usage: setup.py --generate <num_tracks> <track_length>")
            sys.exit(1)
        num_tracks = int(sys.argv[2])
        track_length = int(sys.argv[3])
        map_data = generate_tracks(num_tracks, track_length)
        with open(MAP_FILE, "w", encoding="utf-8") as f:
            json.dump(map_data, f, indent=2)
        print(f"Generated new {MAP_FILE} with {map_data['totalSegments']} segments.")

    elif mode == "--use-existing":
        if not os.path.exists(MAP_FILE):
            print(f"[\u2718] '{MAP_FILE}' not found. Use --generate to create one.")
            sys.exit(1)
        with open(MAP_FILE, "r", encoding="utf-8") as f:
            map_data = json.load(f)
        print(f"Using existing {MAP_FILE} with {map_data['totalSegments']} segments.")

    else:
        print("Unknown mode:", mode)
        sys.exit(1)

    compose_data = generate_compose(map_data)
    with open(COMPOSE_FILE, "w", encoding="utf-8") as f:
        yaml.dump(compose_data, f, sort_keys=False)
    print(f"Generated {COMPOSE_FILE}.")


if __name__ == "__main__":
    main()