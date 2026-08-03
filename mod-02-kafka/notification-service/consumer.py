import json
import logging
import os
import time

from confluent_kafka import Consumer, KafkaError

logger = logging.getLogger(__name__)

KAFKA_BOOTSTRAP_SERVERS = os.environ.get(
    "KAFKA_BOOTSTRAP_SERVERS", "kafka:29092"
)
KAFKA_TOPIC = "order_events"
GROUP_ID = "notification-service"


def send_mock_email(order_event):
    pass


def main():
    consumer = Consumer(
        {
            "bootstrap.servers": KAFKA_BOOTSTRAP_SERVERS,
            "group.id": GROUP_ID,
            "auto.offset.reset": "earliest",
            "enable.auto.commit": False,
        }
    )
    consumer.subscribe([KAFKA_TOPIC])

    logger.info(
        "Notification service started, waiting for order events on %s...",
        KAFKA_TOPIC,
    )
    timeout = 1.0  # seconds

    try:
        while True:
            msg = consumer.poll(timeout)
            if msg is None:
                continue
            if msg.error():
                if msg.error().code() == KafkaError._PARTITION_EOF:
                    continue
                else:
                    logger.error(f"Kafka error: {msg.error()}")
                    continue

            order_event = json.loads(msg.value().decode("utf-8"))

            if order_event.get("event_type") != "order_placed":
                consumer.commit(msg)
                continue
            logger.info(f"Received order event: {order_event}")

            order_id = order_event.get("order_id")
            if order_id is None:
                logger.error("Order event missing order_id: %s", order_event)
                consumer.commit(msg)
                continue
            send_mock_email(order_event)
            logger.info(f"Mock email sent for order {order_id}")
            consumer.commit(msg)
    except KeyboardInterrupt:
        logger.info("Notification service shutting down...")
    finally:
        consumer.close()


if __name__ == "__main__":
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    )
    main()
