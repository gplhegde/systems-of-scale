#!/bin/bash
# Usage: ./setup-broker.sh <broker_id> <broker1_ip> <broker2_ip> <broker3_ip>
set -e

BROKER_ID=$1
B1=$2; B2=$3; B3=$4
MY_IP=$(hostname -I | awk '{print $1}')

sudo apt-get update -q
sudo apt-get install -y ca-certificates curl gnupg
sudo install -m 0755 -d /etc/apt/keyrings
curl -fsSL https://download.docker.com/linux/ubuntu/gpg | sudo gpg --dearmor -o /etc/apt/keyrings/docker.gpg
sudo chmod a+r /etc/apt/keyrings/docker.gpg
echo "deb [arch=$(dpkg --print-architecture) signed-by=/etc/apt/keyrings/docker.gpg] https://download.docker.com/linux/ubuntu $(. /etc/os-release && echo "$VERSION_CODENAME") stable" \
  | sudo tee /etc/apt/sources.list.d/docker.list > /dev/null
sudo apt-get update -q
sudo apt-get install -y docker-ce docker-ce-cli containerd.io docker-compose-plugin
sudo systemctl enable --now docker

# KRaft cluster ID must be identical across all brokers.
# Generate once with: docker run --rm confluentinc/cp-kafka:7.6.12 kafka-storage random-uuid
CLUSTER_ID="${CLUSTER_ID:-Mk3OEYWWR2mBXBmPtUUBMg}"

cat > /home/ubuntu/docker-compose.yml << EOF
services:
  kafka:
    image: confluentinc/cp-kafka:7.6.12
    network_mode: host
    environment:
      KAFKA_NODE_ID: ${BROKER_ID}
      KAFKA_PROCESS_ROLES: broker,controller
      KAFKA_LISTENERS: PLAINTEXT://${MY_IP}:9092,CONTROLLER://${MY_IP}:9093
      KAFKA_ADVERTISED_LISTENERS: PLAINTEXT://${MY_IP}:9092
      KAFKA_CONTROLLER_LISTENER_NAMES: CONTROLLER
      KAFKA_LISTENER_SECURITY_PROTOCOL_MAP: CONTROLLER:PLAINTEXT,PLAINTEXT:PLAINTEXT
      KAFKA_CONTROLLER_QUORUM_VOTERS: 1@${B1}:9093,2@${B2}:9093,3@${B3}:9093
      KAFKA_INTER_BROKER_LISTENER_NAME: PLAINTEXT
      KAFKA_OFFSETS_TOPIC_REPLICATION_FACTOR: 3
      KAFKA_DEFAULT_REPLICATION_FACTOR: 3
      KAFKA_MIN_INSYNC_REPLICAS: 2
      KAFKA_AUTO_CREATE_TOPICS_ENABLE: "false"
      CLUSTER_ID: ${CLUSTER_ID}
    volumes:
      - kafka_data:/var/lib/kafka/data
volumes:
  kafka_data:
EOF

sudo docker compose -f /home/ubuntu/docker-compose.yml up -d
echo "Broker ${BROKER_ID} running at ${MY_IP}:9092"
