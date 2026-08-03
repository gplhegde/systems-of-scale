import json
import logging
import os
import time

from confluent_kafka import Consumer, KafkaError
from db import confirm_order, get_connection

logger = logging.getLogger(__name__)


KAFKA_BOOTSTRAP_SERVERS = os.environ.get(
    "KAFKA_BOOTSTRAP_SERVERS", "kafka:29092"
)
KAFKA_TOPIC = os.environ.get("KAFKA_TOPIC", "order_events")
GROUP_ID = "order-processor"


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

    db_conn = get_connection()
    logger.info(
        "Order processor started, waiting for order events on %s...",
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
                logger.warning("Order event missing order_id, skipping.")
                consumer.commit(msg)
                continue
            time.sleep(1)  # Simulate processing time
            if confirm_order(db_conn, order_id):
                logger.info(f"Order {order_id} confirmed.")
            else:
                logger.warning(
                    f"Order {order_id} could not be confirmed (maybe already processed)."
                )
            consumer.commit(msg)

    except KeyboardInterrupt:
        logger.info("Shutting down order processor...")
    finally:
        consumer.close()
        db_conn.close()
        logger.info("Order processor stopped.")


if __name__ == "__main__":
    logging.basicConfig(
        level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s"
    )
    main()
