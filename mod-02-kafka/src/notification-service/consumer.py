import json
import logging
import os
import signal

from confluent_kafka import Consumer, KafkaError

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [notification-service] %(levelname)s %(message)s",
)
logger = logging.getLogger(__name__)

shutdown_requested = False


def handle_sigterm(signum, frame):
    global shutdown_requested
    shutdown_requested = True


signal.signal(signal.SIGTERM, handle_sigterm)
signal.signal(signal.SIGINT, handle_sigterm)


def send_mock_email(event: dict):
    items = "\n".join(
        f"  │   {item['quantity']}x {item['product_name']} @ ${item['unit_price']}"
        for item in event["items"]
    )
    logger.info(
        f"\n"
        f"  ┌── ORDER CONFIRMATION EMAIL ──────────────────\n"
        f"  │ To: {event['customer_email']}\n"
        f"  │ Order #{event['order_id']} — Total: ${event['total_price']}\n"
        f"  │\n"
        f"{items}\n"
        f"  └──────────────────────────────────────────────"
    )


def main():
    consumer = Consumer(
        {
            "bootstrap.servers": os.environ.get(
                "KAFKA_BOOTSTRAP_SERVERS", "kafka:29092"
            ),
            "group.id": "notification-service",
            "auto.offset.reset": "earliest",
            "enable.auto.commit": False,
        }
    )
    consumer.subscribe(["order_events"])
    logger.info(
        "Started. Listening on order_events as group notification-service."
    )

    try:
        while not shutdown_requested:
            msg = consumer.poll(timeout=1.0)
            if msg is None:
                continue
            if msg.error():
                if msg.error().code() != KafkaError._PARTITION_EOF:
                    logger.error(f"Consumer error: {msg.error()}")
                continue

            try:
                event = json.loads(msg.value().decode("utf-8"))
            except json.JSONDecodeError:
                consumer.commit(msg)
                continue

            # we only care about order_placed events for this service, but we
            # still commit offsets for all events
            if event.get("event_type") == "order_placed":
                send_mock_email(event)

            consumer.commit(msg)
    finally:
        consumer.close()


if __name__ == "__main__":
    main()
