# SA4E - Software Architectures for Enterprises Übung 03

Dieses Projekt implementiert eine verteilte Simulation eines Spiels namens **Ave Caesar**, das auf einer Microservice-Architektur basiert. Die Simulation verwendet Kafka zur Kommunikation zwischen den verschiedenen Segmenten des Spielfelds und den zentralen Steuerungskomponenten.

## Inhaltsverzeichnis

- [Überblick](#überblick)
- [Voraussetzungen](#voraussetzungen)
- [Installation und Verwendung](#installation)

---

## Überblick

Das Projekt simuliert ein rundenbasiertes Spiel, bei dem Spieler auf einem Spielfeld mit mehreren Segmenten Runden drehen. Die Spieler bewegen sich basierend auf Karten, die sie auf der Hand haben, und versuchen, das Ziel zu erreichen, nachdem sie Caesar begrüßt haben. Die Kommunikation zwischen den Segmenten erfolgt über Kafka, und die Logik für Scouting, Bewegung und Spielstatus wird in Python implementiert.

---

## Voraussetzungen

Bevor du das Projekt ausführst, stelle sicher, dass folgende Software installiert ist:

- **Python 3.10 oder höher**
- **Docker** und **Docker Compose**
- **Kafka** (wird über Docker bereitgestellt)
- Python-Bibliotheken (siehe `requirements.txt`)

---

## Installation und Verwendung

1. **Repository klonen**:
   ```bash
   git clone <repo-url>
   cd SA4E
   ```
2. **Python-Umgebung einrichten**:
    ```bash
    python3 -m venv .venv
    source .venv/bin/activate
    pip install -r requirements.txt
    ```
3. **Verwendung**:
    ```bash
    python run.py --generate <num_tracks> <track_length>

    # Beispiel:
    python run.py --generate 3 10

    # Alternativ falls bestehende map.json verwendet werden soll
    python run.py --use-existing
    ```