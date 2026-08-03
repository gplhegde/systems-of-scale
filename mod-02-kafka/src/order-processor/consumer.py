import json
import logging
import os
import signal
import time

import psycopg2
from confluent_kafka import Consumer, KafkaError

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [order-processor] %(levelname)s %(message)s",
)
logger = logging.getLogger(__name__)

shutdown_requested = False


def handle_sigterm(signum, frame):
    global shutdown_requested
    logger.info("SIGTERM received, shutting down after current message...")
    shutdown_requested = True


signal.signal(signal.SIGTERM, handle_sigterm)
signal.signal(signal.SIGINT, handle_sigterm)


def get_db_connection():
    for attempt in range(10):
        try:
            return psycopg2.connect(
                host=os.environ["POSTGRES_HOST"],
                dbname=os.environ["POSTGRES_DB"],
                user=os.environ["POSTGRES_USER"],
                password=os.environ["POSTGRES_PASSWORD"],
            )
        except psycopg2.OperationalError:
            logger.warning(f"DB not ready, retry {attempt + 1}/10...")
            time.sleep(3)
    raise RuntimeError("Could not connect to database after 10 attempts")


def process_order_placed(conn, event: dict) -> bool:
    """
    Idempotency is built into the WHERE clause: if this message is processed
    twice (at-least-once delivery), the second UPDATE matches 0 rows because
    status is already 'confirmed'. Safe to run twice with no side effects.
    """
    with conn.cursor() as cur:
        cur.execute(
            """
            UPDATE shop_order
            SET status = 'confirmed', updated_at = NOW()
            WHERE id = %s AND status = 'pending'
            """,
            (event["order_id"],),
        )
        updated = cur.rowcount
        conn.commit()

    if updated:
        logger.info(
            f"Order {event['order_id']} confirmed "
            f"(customer={event['customer_id']}, total={event['total_price']})"
        )
    else:
        logger.warning(
            f"Order {event['order_id']} skipped — not pending or not found. "
            f"event_id={event.get('event_id')} (duplicate delivery?)"
        )
    return updated > 0


def main():
    conn = get_db_connection()

    consumer = Consumer(
        {
            "bootstrap.servers": os.environ.get(
                "KAFKA_BOOTSTRAP_SERVERS", "kafka:29092"
            ),
            "group.id": "order-processor",
            "auto.offset.reset": "earliest",
            "enable.auto.commit": False,
            "heartbeat.interval.ms": 3000,
            "session.timeout.ms": 30000,
            # If processing one message takes longer than this, Kafka assumes the
            # consumer is dead and triggers a rebalance. Raise if your processing
            # calls slow external APIs.
            "max.poll.interval.ms": 300000,
        }
    )

    consumer.subscribe(["order_events"])
    logger.info("Started. Listening on order_events as group order-processor.")

    try:
        while not shutdown_requested:
            msg = consumer.poll(timeout=1.0)

            if msg is None:
                continue
            if msg.error():
                if msg.error().code() == KafkaError._PARTITION_EOF:
                    continue  # caught up to end of partition — not an error
                logger.error(f"Consumer error: {msg.error()}")
                continue

            try:
                event = json.loads(msg.value().decode("utf-8"))
            except json.JSONDecodeError as e:
                logger.error(
                    f"Malformed message at offset {msg.offset()}: {e}"
                )
                consumer.commit(msg)  # skip unrecoverable messages
                continue

            if event.get("event_type") != "order_placed":
                consumer.commit(msg)
                continue

            process_order_placed(conn, event)
            # Commit after successful processing. If we crash between process
            # and commit, the message replays on restart. The idempotency guard
            # above handles the duplicate without harm.
            consumer.commit(msg)

    finally:
        # consumer.close() sends a LeaveGroup request, triggering an immediate
        # rebalance rather than waiting for the session timeout (up to 30s).
        # This is why graceful shutdown matters.
        consumer.close()
        conn.close()
        logger.info("Shutdown complete.")


if __name__ == "__main__":
    main()
