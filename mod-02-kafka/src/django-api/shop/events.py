import json
import logging
import os
import uuid
from datetime import datetime, timezone

from confluent_kafka import Producer
from shop.models import Order

logger = logging.getLogger(__name__)


ORDER_EVENTS_TOPIC = "order_events"

# Module-level singleton — Producer creation opens connections and starts
# background threads. Do this once per process, not per request.
_producer: Producer | None = None


def _get_producer() -> Producer:
    global _producer
    bootstrap_servers = os.environ.get(
        "KAFKA_BOOTSTRAP_SERVERS", "kafka:29092"
    )
    if _producer is None:
        _producer = Producer(
            {
                "bootstrap.servers": bootstrap_servers,
                # acks=all: leader waits for all ISR replicas to acknowledge.
                # With replication factor 1 this equals acks=1; with factor 3
                # it prevents data loss if the leader fails immediately after write.
                "acks": "all",
                "retries": 5,
                "retry.backoff.ms": 100,
                "message.timeout.ms": 10000,
            }
        )
    return _producer


def _on_delivery(err, msg):
    if err:
        logger.error(
            "Event delivery failed",
            extra={"topic": msg.topic(), "error": str(err)},
        )
    else:
        logger.debug(
            "Event delivered",
            extra={
                "topic": msg.topic(),
                "partition": msg.partition(),
                "offset": msg.offset(),
            },
        )


def publish_order_event(event_type: str, order: Order) -> dict:
    """
    Publish an order event to Kafka.

    Key is customer_id so all events for one customer land in the same
    partition, preserving per-customer ordering without global ordering.

    poll(0) is non-blocking — it triggers any already-completed delivery
    callbacks and returns immediately. flush() would block until Kafka
    acknowledges, turning async publishing back into a synchronous call.
    """
    event = {
        "event_type": event_type,
        "event_id": str(uuid.uuid4()),
        "produced_at": datetime.now(timezone.utc).isoformat(),
        "schema_version": 1,
        "order_id": order.id,
        "customer_id": order.customer_id,
        "customer_email": order.customer.email,
        "total_price": str(order.total_price),
        "status": order.status,
        "items": [
            {
                "product_id": item.product_id,
                "product_name": item.product.name,
                "quantity": item.quantity,
                "unit_price": str(item.unit_price),
            }
            for item in order.items.select_related("product").all()
        ],
    }

    producer = _get_producer()
    producer.produce(
        topic=ORDER_EVENTS_TOPIC,
        key=str(order.customer_id),
        value=json.dumps(event).encode("utf-8"),
        callback=_on_delivery,
    )
    # poll(0) serves the producer's internal event queue without blocking.
    # produce() enqueues the message and returns immediately — the actual
    # network write happens on a background thread. poll() is what drives
    # that thread's callbacks (including _on_delivery). Passing 0 means
    # "flush any already-completed callbacks right now, then return" — it
    # never waits for new ones. flush() would block until Kafka acknowledges
    # the write, which turns this async publish back into a synchronous call
    # inside the HTTP request, defeating the purpose.
    producer.poll(0)
    return event
