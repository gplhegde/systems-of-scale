import json
import logging
import os
from typing import Literal

from confluent_kafka import Message, Producer

logger = logging.getLogger(__name__)
from .models import Order

EventType = Literal["order_placed", "order_confirmed"]

ORDER_EVENTS_TOPIC = "order_events"

_producer = None


def get_kafka_producer():
    """Get a singleton Kafka producer instance.

    NOTE: In production we need to handle producer lifecycle and errors more
    robustly. Also, use lock to ensure thread safety if this is called from
    multiple threads.
    """
    global _producer
    if _producer is None:
        bootstrap_servers = os.environ.get(
            "KAFKA_BOOTSTRAP_SERVERS", "localhost:9092"
        )
        _producer = Producer(
            {"bootstrap.servers": bootstrap_servers, "acks": "all"}
        )
    return _producer


def _delivery_callback(err, msg: Message):
    if err is not None:
        logger.error(f"Failed to deliver message: {err}")
    else:
        logger.info(
            f"Message delivered to {msg.topic()} [{msg.partition()}] at offset {msg.offset()}"
        )


def publish_order_event(order: Order, event_type: EventType):
    event = {
        "event_type": event_type,
        "order_id": order.pk,
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
            for item in order.items.all()
        ],
    }

    producer = get_kafka_producer()
    producer.produce(
        topic=ORDER_EVENTS_TOPIC,
        key=str(order.customer_id),
        value=json.dumps(event),
        callback=_delivery_callback,
    )
    # Trigger delivery callback for any outstanding messages
    producer.poll(0)
    return event
