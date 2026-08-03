# Module 02 — Apache Kafka

## Systems of Scale

**Scenario:** You are large e-commerce platform like Amazon. A flash sale hits.
Order volume spikes 10x in 90 seconds. Your synchronous order pipeline falls
over. This is what event streaming is actually for — and this is what it takes
to build it properly.

> **Prerequisites:** Familiarity with Docker and Docker Compose. If you need a
> refresher on multi-container local setups,
> [Module 01 — Containers](../module-01-container) covers it.
>
> **Stack:** Python, Django, confluent-kafka, Locust, Docker Compose, Multipass
> (for the 3-broker section)

---

## 1. The Problem

Every e-commerce system eventually hits the same wall. A flash sale goes live,
orders spike, and somewhere in the stack something starts queuing work faster
than it can process it. If your order pipeline is synchronous — the HTTP request
stays open while you deduct stock, write the order, send a confirmation email,
notify the warehouse, and update your analytics — you've built a system where
the slowest downstream dependency determines your checkout latency. That
dependency will be slow exactly when you can least afford it.

### The job queue attempt — and where it breaks

The first instinct is a job queue. Celery, Sidekiq, RQ. Push tasks to Redis,
return the HTTP response immediately, process asynchronously. Here is what that
looks like for the order placement view:

```python
# The Celery version — looks reasonable at first
@api_view(['POST'])
def create_order(request):
    order = Order.objects.create(...)

    # Dispatch async tasks to workers
    send_confirmation_email.delay(order.id)
    notify_warehouse.delay(order.id)

    return Response(OrderSerializer(order).data, status=201)
```

This solves the latency problem. The HTTP response is fast. But three months
later, the fraud team needs to run checks on every order. You add:

```python
    run_fraud_check.delay(order.id)
```

Six months after that, marketing wants to award loyalty points:

```python
    award_loyalty_points.delay(order.id)
```

And now someone asks: "can we replay last month's orders through the new fraud
model to backfill the scores?" The answer is no. Once a Celery task is picked up
by a worker and acknowledged, it is gone from the queue. There is no history.
There is no replay. You would have to write a management command that queries
the `order` table and re-dispatches tasks manually — which is fragile and not
the same as replaying the original events in order.

The structural problems with the job queue approach:

**Tight coupling to the producer.** Every time a new downstream system needs to
react to order placement, you modify `create_order`. The view accumulates
`.delay()` calls for every system in the company that cares about orders. It
becomes a dispatch table. A bug in the loyalty service's task signature breaks
the deployment of the order service.

**No fan-out without explicit dispatch.** Two consumers of the same event means
two `.delay()` calls. You have to know about both consumers at write time. A new
consumer deployed today cannot process yesterday's events.

**No replay.** The queue is ephemeral. Once consumed, gone. Historical
reprocessing — backfills, data migrations, re-running a buggy consumer with a
fixed version — requires building separate infrastructure on top of the queue.

**What you actually need is not a queue — it's a log.** Something that records
that an order happened, retains that record durably, and lets any number of
independent consumers read it at their own pace — including replaying from any
point in history, and including consumers that didn't exist when the event was
originally written.

That is Kafka.

---

## 2. How It Works — Mental Model First

### The append-only log

The central abstraction in Kafka is not a queue. It is a **log** — an ordered,
append-only sequence of records. New records are always written to the end.
Readers track their own position in the log. Nothing is removed when read.
Records are retained for a configured period regardless of whether anyone has
consumed them.

The best way to understand the difference from a queue is to look at both side
by side.

<!-- TODO: add excalidraw drawing here -->

**A Redis/Celery queue — work disappears on consumption:**

```
Redis queue: order_tasks

 ┌─────┬─────┬─────┬─────┐
 │ t:1 │ t:2 │ t:3 │ t:4 │  ← tasks waiting
 └─────┴─────┴─────┴─────┘
   ↑
   Worker A pops t:1. Task is gone. Worker B cannot see it.
   If Worker A crashes mid-processing, t:1 is lost (unless you
   configured visibility timeouts carefully).

After Worker A and B process everything:
 ┌─────────────────────────┐
 │         (empty)         │
 └─────────────────────────┘
   History: none. Replay: impossible.
   Adding a third worker type tomorrow: it sees nothing from the past.
```

**A Kafka log — records persist, consumers track their own position:**

```
Kafka topic: order_events (single partition, simplified)

offset:  0     1     2     3     4     5     6
       ┌─────┬─────┬─────┬─────┬─────┬─────┬─────┐
       │ e:1 │ e:2 │ e:3 │ e:4 │ e:5 │ e:6 │ e:7 │──> (new records append here)
       └─────┴─────┴─────┴─────┴─────┴─────┴─────┘
                           ↑                   ↑
              Consumer A is at offset 3         Producer just wrote offset 6
              (it was down, catching up)
                                 ↑
                    Consumer B is at offset 5
                    (running normally, nearly caught up)

Consumer A and Consumer B are completely independent.
Consumer A being behind does not affect Consumer B.
Consumer B does not "steal" records from Consumer A.
A new Consumer C starting today can read from offset 0 —
it sees all history within the retention window.
```

The permanence of the log is the architectural property that changes what you
can build. It enables:

- A consumer that was down for 4 hours to restart and read everything it missed,
  in order, from exactly where it stopped
- A new consumer deployed today to read all events from the beginning of the
  retention window — even events that predate its existence
- Two consumers reading the same log with complete independence. One being slow
  or down has zero effect on the other
- Replay of any time window for backfills, debugging, or running a fixed version
  of a buggy consumer over historical data

### Topics and partitions

A **topic** is a named log — the unit of organisation. Your order system might
have `order_events`, `inventory_updates`, `payment_events`. Producers write to a
topic. Consumers subscribe to a topic and read all its records.

A topic is split into **partitions**. Each partition is an independent ordered
log. Records within a partition are strictly ordered by offset. Records across
partitions have no ordering guarantee relative to each other. This is the
tradeoff that makes Kafka scalable: strict ordering is preserved where it
matters (within a partition, typically within a single entity like a customer or
an order) but not globally (across all customers simultaneously).

<!-- TODO: excalidraw here -->

```
Topic: order_events (3 partitions, 2 consumer instances in one group)

                        PRODUCER
                           │
           ┌───────────────┼───────────────┐
           │ hash(key)     │               │
           ▼               ▼               ▼
      Partition 0      Partition 1      Partition 2
  ┌─────────────┐  ┌─────────────┐  ┌─────────────┐
  │[0][1][2][3] │  │[0][1][2]    │  │[0][1][2][3] │
  │          ↑  │  │          ↑  │  │[4][5]    ↑  │
  └──────────┼──┘  └──────────┼──┘  └──────────┼──┘
             │                │                 │
        Consumer 1       Consumer 1        Consumer 2
        (reads P0)       (reads P1)        (reads P2)

Consumer 1 owns partitions 0 and 1. Consumer 2 owns partition 2.
Each partition is owned by exactly one consumer within the group.
Adding a third consumer instance would give each consumer one partition.
Adding a fourth consumer instance: one sits idle — no partition to own.
Partition count is the ceiling on parallelism within a consumer group.
```

**Partition count is a ceiling on consumer parallelism within a group.** A topic
with 3 partitions can be consumed in parallel by at most 3 instances of the same
consumer group.

A fourth instance would sit idle with no partition assigned to it. Set partition
count based on your expected maximum consumer count — you can increase it later,
but that requires a rebalance of all consumer groups on that topic. The common
production pattern is to set it higher than you think you'll need (6, 12, 24 are
common choices) so you have room to scale without disruption.

### Record keys and partition assignment

Every record can have an optional **key**. Kafka hashes the key to determine
which partition the record goes to. Records with the same key always land in the
same partition, which means they are always processed in order by whichever
consumer owns that partition.

For an order system: key records by `customer_id`. Every event for a given
customer — order placed, order confirmed, order shipped — arrives at the
consumer in the order they were produced. Events for different customers are
distributed across partitions and may be processed in any relative order. That's
almost always the right semantic: you care about per-customer ordering, not
global ordering.

Without a key, records are distributed round-robin. That maximises partition
utilisation but destroys per-entity ordering.

### Consumer groups — the fan-out mechanism

A **consumer group** is a named set of consumers that cooperatively read a
topic. In practice, a consumer group is a single service that scales
horizontally. Each instance of the service is a consumer in the group. Kafka's
guarantee: within a group, each partition is assigned to exactly one consumer.
Work is shared; no record is processed twice by the same group.

The more important property: **different consumer groups are completely
independent.** Each group has its own committed offset per partition. Group A
being at offset 1000 and Group B being at offset 200 is perfectly normal. Group
A crashing has zero effect on Group B. This is how you fan-out to multiple
independent consumers without touching the producer.

<!-- TODO: excalidraw diagram here -->

```
Topic: order_events (3 partitions)

                    ┌─────────────────────────────────────────┐
                    │           Consumer Group A              │
                    │         "order-processor"               │
                    │  consumer-1 ──> partition 0 (offset 45) │
                    │  consumer-2 ──> partition 1 (offset 45) │
                    │  consumer-3 ──> partition 2 (offset 44) │
                    └─────────────────────────────────────────┘

                    ┌─────────────────────────────────────────┐
                    │           Consumer Group B              │
                    │        "notification-service"           │
                    │  consumer-1 ──> partition 0 (offset 12) │
                    │  consumer-1 ──> partition 1 (offset 11) │
                    │  consumer-1 ──> partition 2 (offset 13) │
                    └─────────────────────────────────────────┘
```

Group A has 3 consumers and is well ahead. Group B has 1 consumer and is lagging
— maybe it was down for a while and is catching up. Neither affects the other.
Neither affects the producer.

Adding a third consumer group tomorrow — say, a fraud detection service — costs
zero changes to the producer and zero changes to the existing consumers. The new
group starts reading from whatever offset you configure (`earliest` to replay
history, `latest` to start from now).

### Brokers, replication, and leaders

A Kafka **broker** is a single server process. A cluster is multiple brokers.
Each partition lives on one broker (its **leader**) and, for fault tolerance, is
replicated to others (**followers**). All reads and writes for a partition go
through the leader. If the leader's broker dies, Kafka promotes a follower
automatically.

In a single-broker setup (what we start with), every partition's leader is on
the same broker. If that broker dies, the cluster is down. In a 3-broker setup,
partition leaders are spread across all 3. Losing one broker means one-third of
your partitions need to elect new leaders — which Kafka does automatically,
typically in under 30 seconds for a small cluster.

The `replication.factor` of a topic determines how many copies of each partition
exist. `replication.factor=1` means no replication — data loss if the broker's
disk dies. `replication.factor=3` on a 3-broker cluster means each partition
exists on all 3 brokers. Standard production minimum is 3.

### Delivery semantics — at-least-once, at-most-once, exactly-once

**At-most-once:** commit the offset before processing. If the consumer crashes
during processing, the message is never reprocessed. Data loss is possible. Only
acceptable if losing the odd event doesn't matter — metrics aggregation, for
example.

**At-least-once:** process the message, then commit the offset. If the consumer
crashes after processing but before committing, the message is reprocessed on
restart. Duplicates are possible. Acceptable if your processing is _idempotent_
— running it twice produces the same outcome as running it once. This is the
default for most production systems and what we implement here.

**Exactly-once:** Kafka supports this via transactions, but it requires
producers and consumers that understand Kafka transactions, and the overhead is
real. It's not the default, it's not simple, and most teams don't use it unless
they're moving money. If you're deduplicating at the application layer
(idempotent writes), at-least-once is almost always the right choice.

For our order system: processing an `order_placed` event twice should be safe.
We write to Postgres with an idempotency check (`WHERE status = 'pending'`). A
duplicate process sets the status to `confirmed` the first time and matches 0
rows the second time. That's at-least-once done correctly.

### Cluster metadata and the role of Zookeeper

**Cluster metadata** is the set of facts every node in the cluster must agree on
to function correctly:

- Which topics exist and how many partitions each has
- Which broker is the **leader** for each partition — the one that accepts
  writes and serves reads
- Which brokers hold in-sync replicas (ISR) for each partition
- Which brokers are currently alive (cluster membership)
- Per-topic configuration: retention period, replication factor, cleanup policy

Without this shared state, the cluster can't coordinate. A producer doesn't know
which broker to send a message to. A follower replica doesn't know who to fetch
from. A consumer group coordinator doesn't know which broker owns which group.
Cluster metadata is what turns a set of independent brokers into a coherent
distributed system.

The challenge: keeping this state consistent across many brokers is a
distributed systems problem in itself — it requires fault-tolerant, linearizable
writes so every node sees the same view of the cluster even as brokers fail and
rejoin. Kafka originally solved this by delegating the problem entirely to an
external system.

### Where Zookeeper fits — and KRaft

Historically, Kafka used **Zookeeper** to manage cluster metadata: which broker
is the leader for which partition, broker membership, topic configurations.
Zookeeper is a separate distributed coordination system — another thing to
operate, monitor, and keep in sync.

Kafka 3.3+ introduced **KRaft** (Kafka Raft), which removes the Zookeeper
dependency. Kafka manages its own metadata using the Raft consensus algorithm.
KRaft is the future and is production-ready as of Kafka 3.3.

This module uses Zookeeper for the single-broker Docker Compose phase. The
3-broker Multipass cluster uses KRaft, where the operational simplicity of
dropping Zookeeper is more apparent.

---

## 3. Local Setup

<!-- TODO: excalidraw diagram here -->

### What we're running

```
┌─────────────────────────────────────────────────────┐
│                  Docker Compose                     │
│                                                     │
│  ┌──────────┐   ┌──────────┐   ┌─────────────────┐ │
│  │Zookeeper │   │  Kafka   │   │    Django API   │ │
│  │  :2181   │──>│  broker  │<──│    (producer)   │ │
│  └──────────┘   │  :9092   │   └─────────────────┘ │
│                 └────┬─────┘                        │
│                      │  topic: order_events         │
│            ┌─────────┴──────────┐                   │
│            ▼                    ▼                   │
│  ┌──────────────────┐  ┌──────────────────────────┐ │
│  │  order-processor │  │  notification-service    │ │
│  │  (consumer grp A)│  │  (consumer grp B)        │ │
│  └──────────────────┘  └──────────────────────────┘ │
│                                                     │
│  ┌──────────┐   ┌──────────┐                        │
│  │ Postgres │   │  Redis   │                        │
│  └──────────┘   └──────────┘                        │
└─────────────────────────────────────────────────────┘
```

### Directory structure

```
module-02-kafka/
├── README.md
├── docker-compose.yml
├── .env.example
├── locustfile.py
├── multipass/
│   └── setup-broker.sh
└── src/
    ├── django-api/
    │   ├── Dockerfile
    │   ├── requirements.txt
    │   ├── manage.py
    │   ├── fastcommerce/
    │   │   ├── settings.py
    │   │   └── urls.py
    │   └── shop/
    │       ├── models.py
    │       ├── serializers.py
    │       ├── views.py
    │       ├── events.py
    │       └── management/commands/seed_data.py
    ├── order-processor/
    │   ├── Dockerfile
    │   ├── requirements.txt
    │   └── consumer.py
    └── notification-service/
        ├── Dockerfile
        ├── requirements.txt
        └── consumer.py
```

### Install prerequisites

```bash
brew install --cask docker       # if not already installed
brew install --cask multipass    # for the 3-broker section
pip install locust
```

### The listener configuration — the most important config in this file

Before the full compose file, this deserves its own explanation because it trips
up almost everyone and the error messages are misleading.

Kafka advertises its address to clients so they know where to reconnect after
the initial bootstrap. A container on the Docker network and your Mac at the
terminal are different network contexts. The container connects to `kafka:29092`
(Docker's internal DNS). Your Mac connects to `localhost:9092`. Configure only
one and one of them breaks — silently, with confusing errors.

```
KAFKA_LISTENER_SECURITY_PROTOCOL_MAP: PLAINTEXT:PLAINTEXT,PLAINTEXT_HOST:PLAINTEXT
KAFKA_ADVERTISED_LISTENERS: PLAINTEXT://kafka:29092,PLAINTEXT_HOST://localhost:9092
KAFKA_LISTENERS: PLAINTEXT://0.0.0.0:29092,PLAINTEXT_HOST://0.0.0.0:9092
KAFKA_INTER_BROKER_LISTENER_NAME: PLAINTEXT
```

`PLAINTEXT` is for container-to-container. `PLAINTEXT_HOST` is for connections
from your PC. `INTER_BROKER_LISTENER_NAME` tells brokers which listener to use
when talking to each other — always the internal one.

### `docker-compose.yaml`

See [`docker-compose.yaml`](./docker-compose.yaml). A few decisions worth
noting:

**`kafka-init`** is a one-shot container that creates `order_events` (3
partitions) and `order_events_dlq` with explicit settings, then exits. All
consumer services use `condition: service_completed_successfully` to wait for it
— this prevents a consumer from starting before its topic exists, which causes a
confusing `UNKNOWN_TOPIC` error. `KAFKA_AUTO_CREATE_TOPICS_ENABLE: "false"`
enforces this: auto-creation silently applies a default of 1 partition, which
immediately caps consumer parallelism.

**`stop_grace_period: 60s`** on the consumer services gives them time to finish
processing the current message and send a LeaveGroup before Docker sends
SIGKILL. Set this to be longer than your worst-case message processing time.

### Start it

```bash
cp .env.example .env
docker compose up --build
```

Watch for `kafka-init` to exit with code 0 (it logs "Topics created:"). Then
`api`, `order-processor`, and `notification-service` start.

### Verify the topic

```bash
docker compose exec kafka kafka-topics \
  --describe --topic order_events \
  --bootstrap-server localhost:9092
```

Expected:

```
Topic: order_events  PartitionCount: 3  ReplicationFactor: 1
  Partition: 0  Leader: 1  Replicas: 1  Isr: 1
  Partition: 1  Leader: 1  Replicas: 1  Isr: 1
  Partition: 2  Leader: 1  Replicas: 1  Isr: 1
```

All 3 partitions have Leader 1 (our only broker). In the 3-broker Multipass
cluster in Section 8, these leaders distribute across brokers 1, 2, and 3.

---

## 4. Core Implementation

### The order event pipeline

1. Django validates the request and deducts stock (synchronous — can't oversell)
2. Django creates the order as `PENDING` and commits the DB transaction
3. Django publishes `order_placed` to Kafka and returns immediately
4. `order-processor` consumes `order_placed`, updates order to `CONFIRMED`
5. `notification-service` consumes the same event, sends a mock email

Steps 4 and 5 happen asynchronously, in parallel, with no knowledge of each
other. The HTTP checkout response is blocked by none of it.

### The event schema

```python
{
    "event_type": "order_placed",       # consumers filter on this
    "event_id": "uuid4",                # for idempotency checks
    "produced_at": "2025-01-15T10:30:00Z",
    "schema_version": 1,                # forward compatibility
    "order_id": 42,
    "customer_id": 7,
    "customer_email": "alice@example.com",
    "total_price": "149.97",
    "status": "pending",
    "items": [
        {
            "product_id": 3,
            "product_name": "Wireless Headphones",
            "quantity": 2,
            "unit_price": "49.99"
        }
    ]
}
```

The `event_type` envelope lets a single topic carry multiple event types over
time — `order_placed`, `order_confirmed`, `order_cancelled`. Consumers filter
for what they handle and skip the rest, committing the offset either way. The
`schema_version` field is minimal schema evolution support, covered in
Section 5.

### `src/django-api/shop/events.py`

See [`src/django-api/shop/events.py`](./src/django-api/shop/events.py). Key
decisions:

**Singleton producer.** `Producer` creation opens TCP connections and starts
background threads. Creating one per HTTP request would be extremely wasteful.
The module-level `_get_producer()` creates it once on first call and reuses it
for the process lifetime.

**`poll(0)` not `flush()`.** `produce()` enqueues the message in an internal
buffer — the actual network write happens on a background thread. `poll(0)`
services any already-completed delivery callbacks without blocking. `flush()`
would block until Kafka acknowledges the write, turning the async publish back
into a synchronous call inside the HTTP request.

**`customer_id` as the record key.** Kafka hashes the key to select the
partition. All events for a given customer always land in the same partition and
are therefore processed in order — `order_placed` before `order_confirmed`
before `order_shipped`. Events for different customers are distributed across
partitions and may be processed in any relative order, which is the right
semantic for this workload.

### `src/django-api/shop/views.py` — order creation

See [`src/django-api/shop/views.py`](./src/django-api/shop/views.py). The key
decision:

The DB transaction (`transaction.atomic()`) commits first, then the event is
published. Publishing _inside_ the transaction would couple a Kafka failure to a
DB rollback — the order would fail to be created if Kafka was temporarily
unavailable, which is the wrong trade-off. Publishing _after_ means a process
crash between the commit and the publish leaves an order `PENDING` with no event
ever sent. This is an acceptable gap for low volumes; the transactional outbox
pattern (Section 8) closes it for production.

### `src/order-processor/consumer.py`

See [`src/order-processor/consumer.py`](./src/order-processor/consumer.py). Key
decisions:

**SIGTERM handler** sets `shutdown_requested = True`, checked before each
`poll()`. Combined with `consumer.close()` in the `finally` block, this sends a
LeaveGroup to the broker and triggers an immediate rebalance instead of waiting
for the session timeout (up to 30s of idle partitions). See Pattern 5.

**Idempotent `WHERE` clause.**
`UPDATE shop_order SET status = 'confirmed' WHERE id = %s AND status = 'pending'`
makes processing safe under `at-least-once` delivery. A replayed message that
was already processed matches 0 rows and logs a warning — no
double-confirmation, no error.

**Manual offset commit after processing.** `enable.auto.commit: False` means
Kafka only advances the committed offset when we explicitly call
`consumer.commit(msg)` — after `process_order_placed()` returns successfully. If
the consumer crashes mid-processing, the message replays from the last committed
offset on restart. The idempotency guard above handles the duplicate.

### `src/notification-service/consumer.py`

See
[`src/notification-service/consumer.py`](./src/notification-service/consumer.py).

This service uses consumer group `notification-service`, separate from
`order-processor`. Both groups read the same `order_events` topic but each
maintains its own committed offset per partition. The notification service being
slow or down has zero effect on the order processor — this is the fan-out
property covered in Section 2.

Note that offsets are committed for all event types, not just `order_placed`.
Skipping the commit on unrecognised events would cause them to replay
indefinitely on restart.

### Verify end-to-end

```bash
# Place an order
curl -s -X POST http://localhost:8000/api/orders/ \
  -H "Content-Type: application/json" \
  -d '{"customer_id": 1, "items": [{"product_id": 1, "quantity": 1}]}' \
  | python -m json.tool
# Response is immediate, status: "pending"

# Watch consumers
docker compose logs -f order-processor notification-service
# order-processor: Order 1 confirmed
# notification-service: ORDER CONFIRMATION EMAIL ...

# Confirm the status updated
curl -s http://localhost:8000/api/orders/1/ | python -m json.tool
# "status": "confirmed"

# Watch the raw event stream
docker compose exec kafka kafka-console-consumer \
  --topic order_events --bootstrap-server localhost:9092 \
  --from-beginning --property print.key=true --property print.timestamp=true
```

---

## 5. Production Patterns

### Pattern 1 — Dead Letter Queue (DLQ)

**The problem:** A consumer encounters a message it can't process — malformed
JSON, unexpected schema, a transient dependency failure that exhausts retries.
Skip it and commit the offset: the event is silently lost. Don't commit and keep
retrying: the consumer is stuck forever on one bad message, accumulating lag on
every partition it owns, while the rest of the system processes fine.

**The implementation:** After N retries with exponential backoff, publish the
raw message to `order_events_dlq` and commit the original offset. The DLQ is
monitored separately. A human investigates and decides whether to replay (fix
the consumer and reset the DLQ offset) or discard.

```python
from confluent_kafka import Producer as KafkaProducer
from datetime import datetime, timezone

_dlq_producer = None

def get_dlq_producer():
    global _dlq_producer
    if _dlq_producer is None:
        _dlq_producer = KafkaProducer({
            'bootstrap.servers': os.environ.get('KAFKA_BOOTSTRAP_SERVERS')
        })
    return _dlq_producer


def send_to_dlq(original_msg, reason: str):
    dlq_event = {
        'original_topic': original_msg.topic(),
        'original_partition': original_msg.partition(),
        'original_offset': original_msg.offset(),
        'original_key': original_msg.key().decode('utf-8') if original_msg.key() else None,
        'original_value': original_msg.value().decode('utf-8'),
        'failure_reason': reason,
        'failed_at': datetime.now(timezone.utc).isoformat(),
    }
    p = get_dlq_producer()
    p.produce(topic='order_events_dlq', value=json.dumps(dlq_event).encode())
    p.flush()
    logger.error(f'Message sent to DLQ: partition={original_msg.partition()} '
                 f'offset={original_msg.offset()} reason={reason}')


# Replace the bare try/except in the main loop with:
MAX_RETRIES = 3
for attempt in range(MAX_RETRIES):
    try:
        event = json.loads(msg.value().decode('utf-8'))
        process_order_placed(conn, event)
        consumer.commit(msg)
        break
    except json.JSONDecodeError as e:
        # Non-retriable — bad JSON won't fix itself on retry
        send_to_dlq(msg, reason=f'JSONDecodeError: {e}')
        consumer.commit(msg)
        break
    except Exception as e:
        if attempt < MAX_RETRIES - 1:
            wait = 2 ** attempt  # 1s, 2s, 4s
            logger.warning(f'Processing attempt {attempt + 1}/{MAX_RETRIES} failed: {e}. '
                           f'Retrying in {wait}s...')
            time.sleep(wait)
        else:
            send_to_dlq(msg, reason=str(e))
            consumer.commit(msg)
```

**The tradeoff:** A DLQ you never look at is worse than no DLQ — messages
disappear silently. Alert on DLQ depth as a metric. A growing DLQ is a bug
signal, not a normal operating state. In upcoming modules (Prometheus +
Grafana), we'll wire this up as an alert.

### Pattern 2 — Idempotent Consumers

**The problem:** At-least-once delivery means your consumer may process the same
message more than once — after a restart, after a rebalance, after a network
blip during offset commit. If "process this message" means charging a customer
or sending them two emails, duplicates matter.

**The implementation:** Our `WHERE status = 'pending'` guard in
`process_order_placed` is one form of idempotency — the business logic itself is
safe to run twice. For operations that aren't naturally idempotent (calling an
external payment API, for example), use explicit deduplication via the
`event_id`:

```sql
-- Migration
CREATE TABLE IF NOT EXISTS processed_events (
    event_id    UUID PRIMARY KEY,
    processed_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

-- Add a cleanup job so this doesn't grow forever:
-- DELETE FROM processed_events WHERE processed_at < NOW() - INTERVAL '7 days';
```

```python
def is_already_processed(conn, event_id: str) -> bool:
    with conn.cursor() as cur:
        cur.execute("SELECT 1 FROM processed_events WHERE event_id = %s", (event_id,))
        return cur.fetchone() is not None


def mark_as_processed(conn, event_id: str):
    with conn.cursor() as cur:
        # ON CONFLICT DO NOTHING handles the race condition where two
        # consumer instances somehow process the same event simultaneously
        cur.execute(
            "INSERT INTO processed_events (event_id) VALUES (%s) ON CONFLICT DO NOTHING",
            (event_id,)
        )
        conn.commit()


# In the processing flow:
event_id = event.get('event_id')
if event_id and is_already_processed(conn, event_id):
    logger.info(f'Duplicate event {event_id}, skipping.')
    consumer.commit(msg)
    continue

process_order_placed(conn, event)

if event_id:
    mark_as_processed(conn, event_id)
consumer.commit(msg)
```

**The tradeoff:** One extra DB read per message. For high-throughput consumers
this matters. The `WHERE status = 'pending'` approach (natural idempotency) is
better when the business operation itself is safe to repeat. The explicit table
is better when it's not — like external API calls where you need a record of
"did I already do this." Use both for belt-and-suspenders on anything involving
money.

### Pattern 3 — Consumer Lag Monitoring

**The problem:** A consumer that processed orders fine yesterday but has
accumulated 50,000 messages of lag today is a time bomb. Customers won't notice
until the lag becomes noticeable in their order confirmations — by which point
you're already in an incident. You need to know lag is growing before it becomes
a user-visible problem.

**CLI inspection (for ad hoc):**

```bash
docker compose exec kafka kafka-consumer-groups \
  --bootstrap-server localhost:9092 \
  --describe --group order-processor

# Output:
# GROUP           TOPIC        PARTITION  CURRENT-OFFSET  LOG-END-OFFSET  LAG
# order-processor order_events 0          45              45              0
# order-processor order_events 1          44              44              0
# order-processor order_events 2          46              46              0
```

**Programmatic monitoring (for alerting):**

```python
# src/order-processor/lag_monitor.py
# Run as a sidecar alongside the consumer.

import os
import time
from confluent_kafka import Consumer, TopicPartition

KAFKA_BOOTSTRAP_SERVERS = os.environ.get('KAFKA_BOOTSTRAP_SERVERS', 'kafka:29092')
GROUP_ID = 'order-processor'
TOPIC = 'order_events'
LAG_ALERT_THRESHOLD = 1000  # alert if any group has > 1000 unprocessed messages


def check_lag():
    # Use a separate consumer group for monitoring so we don't interfere
    # with committed offsets of the real group
    monitor = Consumer({
        'bootstrap.servers': KAFKA_BOOTSTRAP_SERVERS,
        'group.id': f'{GROUP_ID}-lag-monitor',
    })
    real_group = Consumer({
        'bootstrap.servers': KAFKA_BOOTSTRAP_SERVERS,
        'group.id': GROUP_ID,
    })

    metadata = monitor.list_topics(TOPIC, timeout=10)
    partitions = [TopicPartition(TOPIC, p)
                  for p in metadata.topics[TOPIC].partitions.keys()]

    committed = real_group.committed(partitions, timeout=10)

    total_lag = 0
    for tp in committed:
        low, high = monitor.get_watermark_offsets(tp, timeout=10)
        current = tp.offset if tp.offset >= 0 else low
        lag = high - current
        total_lag += lag
        print(f'  Partition {tp.partition}: offset={current} end={high} lag={lag}')

    print(f'Total lag [{GROUP_ID}]: {total_lag}')
    if total_lag > LAG_ALERT_THRESHOLD:
        print(f'ALERT: lag {total_lag} exceeds threshold {LAG_ALERT_THRESHOLD}')

    monitor.close()
    real_group.close()
    return total_lag


if __name__ == '__main__':
    while True:
        try:
            check_lag()
        except Exception as e:
            print(f'Lag monitor error: {e}')
        time.sleep(10)
```

**The tradeoff:** The consumer groups API is eventually consistent — lag numbers
have a small inherent delay. Fine for alerting (you don't need millisecond
precision on a 30s alert window). Jittery for real-time dashboards. In future
modules we'll scrape this properly with a Prometheus exporter and add an alert
rule.

### Pattern 4 — Schema Evolution Without Downtime

**The problem:** Your event schema changes. You add fields, rename things,
remove fields that were a mistake. Producers and consumers can't be deployed
simultaneously — there's always a window where old producers publish and new
consumers read, or new producers publish and old consumers read. Naive changes
break one side.

**The rules:**

**Rule 1: Additions are always backward compatible.** Adding a new field is safe
if consumers use `event.get('new_field', default)` rather than
`event['new_field']`. Never crash on an unknown field.

**Rule 2: Breaking changes require a version bump and a migration window.** If
you're renaming `customer_email` to `email`, bump `schema_version` to 2.

```python
def extract_customer_email(event: dict) -> str:
    version = event.get('schema_version', 1)
    if version == 1:
        return event.get('customer_email', '')
    elif version == 2:
        return event.get('email', '')  # renamed in v2
    else:
        # Unknown future version — attempt best-effort interpretation
        return event.get('email') or event.get('customer_email', '')
```

**Deployment order for a breaking change:**

1. Deploy consumers that handle both v1 and v2
2. Deploy producers that publish v2
3. Wait for all v1 events to age out of retention (24h here, 7 days in prod)
4. Remove v1 handling from consumers

**The tradeoff:** This is manageable for 2-3 schema versions. Beyond that, you
need a **schema registry** (Confluent Schema Registry, AWS Glue). A schema
registry enforces backward/forward compatibility automatically, stores versioned
schemas, and lets consumers fetch the schema for any event by ID. Production
teams with multiple teams publishing to shared topics use one. We don't run one
here — operational cost exceeds the learning benefit at this scale.

### Pattern 5 — Graceful Shutdown

**The problem:** When Docker stops a container (`docker stop`, rolling deploy,
scale-down), it sends SIGTERM, waits `stop_grace_period`, then sends SIGKILL. If
your consumer doesn't handle SIGTERM, it dies uncleanly. Kafka then waits for
`session.timeout.ms` (30s in our config) before considering the consumer dead
and triggering a rebalance. During those 30s, the partitions that consumer owned
are idle — messages accumulate, lag grows.

**The implementation:** Already in our consumer above — the `handle_sigterm`
function sets `shutdown_requested = True`, the main loop checks it before each
`poll()`, and `consumer.close()` in the `finally` block sends a LeaveGroup
request to the broker, triggering an immediate rebalance.

```python
# The key lines — already in our consumer:
signal.signal(signal.SIGTERM, handle_sigterm)
signal.signal(signal.SIGINT, handle_sigterm)

while not shutdown_requested:
    msg = consumer.poll(timeout=1.0)
    # ...

# finally block always runs, even on SIGTERM:
consumer.close()  # triggers immediate rebalance, not 30s timeout wait
```

**Verify it works:**

```bash
# Watch rebalance timing
docker compose logs -f order-processor &

time docker compose stop order-processor
# Should see LeaveGroup in logs and stop cleanly within 2-3 seconds,
# not after the 30s session timeout
```

**The tradeoff:** Graceful shutdown only works if your processing loop actually
checks `shutdown_requested` between messages. If one message takes 45 seconds to
process (slow external API call), Docker's `stop_grace_period: 60s` gives you
enough buffer — but cutting it shorter means SIGKILL mid-processing and an
unclean shutdown. Set `stop_grace_period` to be longer than your worst-case
message processing time.

---

## 6. Breaking It — Failure Modes and Observability

### Failure 1 — Kill the broker mid-stream

```bash
# Terminal 1: continuously watch consumer lag
watch -n 2 'docker compose exec kafka kafka-consumer-groups \
  --bootstrap-server localhost:9092 --describe --group order-processor 2>/dev/null'

# Terminal 2: fire 50 orders, one every 500ms
for i in $(seq 1 50); do
  curl -s -X POST http://localhost:8000/api/orders/ \
    -H "Content-Type: application/json" \
    -d "{\"customer_id\": $((RANDOM % 5 + 1)), \"items\": [{\"product_id\": 1, \"quantity\": 1}]}" \
    > /dev/null
  sleep 0.5
done &

# Terminal 3: kill the broker after 10 orders
sleep 5 && docker compose stop kafka
```

**What you'll observe:**

The API requests start returning errors after ~10s (the producer's
`message.timeout.ms`). The consumers disconnect. New orders still hit Postgres
fine (the DB is independent) but the Kafka publish in `views.py` fails. The
delivery callback logs errors. Those events are lost — there's no automatic
retry in our current implementation.

This is the gap between our implementation and production-grade: the
**transactional outbox pattern** solves it. We discuss it in Section 8.

**Recovery:**

```bash
docker compose start kafka
```

Kafka comes back within ~30s. The consumers reconnect and resubscribe
automatically (this is built into `confluent-kafka`'s reconnect logic). Any
events that were successfully published before the broker died are replayed from
the consumers' last committed offsets.

### Failure 2 — Consumer crashes mid-processing

```bash
# Place 30 orders
for i in $(seq 1 30); do
  curl -s -X POST http://localhost:8000/api/orders/ \
    -H "Content-Type: application/json" \
    -d "{\"customer_id\": $((RANDOM % 5 + 1)), \"items\": [{\"product_id\": 1, \"quantity\": 1}]}" \
    > /dev/null
done

# Kill the consumer abruptly (no graceful shutdown)
docker compose kill order-processor

# Check what got confirmed vs what's pending
docker compose exec db psql -U fastuser fastdb \
  -c "SELECT status, count(*) FROM shop_order GROUP BY status;"
```

You'll see a mix of `confirmed` and `pending`. The `pending` ones are either
unprocessed or processed but not committed when the container died.

```bash
# Restart — replays from last committed offset
docker compose start order-processor

# After a few seconds — all should be confirmed
docker compose exec db psql -U fastuser fastdb \
  -c "SELECT status, count(*) FROM shop_order GROUP BY status;"
```

The idempotency guard (`WHERE status = 'pending'`) means replayed events that
were already processed simply match 0 rows and log a warning. No duplicate
confirmations.

### Failure 3 — Deliberate consumer lag

```bash
# Stop the consumer entirely
docker compose stop order-processor

# Blast 200 orders
for i in $(seq 1 200); do
  curl -s -X POST http://localhost:8000/api/orders/ \
    -H "Content-Type: application/json" \
    -d "{\"customer_id\": $((RANDOM % 5 + 1)), \"items\": [{\"product_id\": 1, \"quantity\": 1}]}" \
    > /dev/null &
done
wait

# Check lag — should be ~200 across 3 partitions
docker compose exec kafka kafka-consumer-groups \
  --bootstrap-server localhost:9092 --describe --group order-processor

# Restart — watch it drain
docker compose start order-processor
watch -n 1 'docker compose exec kafka kafka-consumer-groups \
  --bootstrap-server localhost:9092 --describe --group order-processor 2>/dev/null'
```

Watch the lag drain in real time. This is the core operational property of
Kafka: a consumer that was down for any period of time recovers by reading from
its last committed offset, with no intervention required and no messages lost.

---

## 7. Load Testing with Locust

### The scenario

60 seconds of normal traffic (10 concurrent users), then a flash sale spike to
100 concurrent users for 90 seconds, then back to baseline. We want to see:
consumer lag growth under the spike, throughput limits, and whether scaling the
consumer group drains lag in real time.

### `locustfile.py`

See [`locustfile.py`](./locustfile.py). The load profile uses weighted tasks —
3× order placement, 2× product listing, 1× order listing — to simulate realistic
e-commerce traffic skewed toward writes. 400 responses on `POST /api/orders/`
are marked as success: stock exhaustion is an expected outcome at high
concurrency, not a service failure.

### Run the baseline

```bash
# Ensure enough stock for the test
docker compose exec api python manage.py seed_data --clear

locust -f locustfile.py --host=http://localhost:8000 --headless \
  --users 10 --spawn-rate 2 --run-time 60s \
  --html baseline-report.html
```

Expected on M-series Mac with 10 users:

- Order creation throughput: ~30–50 req/s
- p50 latency: 40–80ms (dominated by the Postgres write)
- p99 latency: 200–400ms
- Consumer lag: 0–5 (consumer keeps pace easily)
- Error rate: 0% (excluding expected stock 400s)

The near-zero lag at baseline tells you something useful: Kafka is barely being
used as a buffer here. The consumer processes events as fast as Django produces
them. The value of Kafka isn't visible until the spike.

### Simulate the flash sale spike

Open two terminals simultaneously:

**Terminal 1 — Locust spike:**

```bash
locust -f locustfile.py --host=http://localhost:8000 --headless \
  --users 100 --spawn-rate 20 --run-time 90s \
  --html spike-report.html
```

**Terminal 2 — Watch consumer lag in real time:**

```bash
watch -n 2 'docker compose exec kafka kafka-consumer-groups \
  --bootstrap-server localhost:9092 --describe --group order-processor 2>/dev/null'
```

**What you'll see:**

Lag starts growing immediately as the spike hits. Django's order creation
latency remains low — it writes to Postgres, publishes to Kafka (non-blocking),
and returns. The HTTP response time barely changes. This is Kafka doing its job:
absorbing the spike so the HTTP layer stays responsive while downstream
processing works through the backlog at whatever rate it can.

The consumer lag number you see is bounded by:

```
lag_growth_rate = produce_rate - consume_rate
```

With `time.sleep(2)` in `process_order_placed`, consume rate ≈ 0.5 msg/s per
instance. At 100 users generating ~80 orders/s, lag grows at ~79.5 msg/s.

### Scale the consumer to drain the lag

Without stopping Locust, scale up the consumer:

```bash
docker compose up --scale order-processor=3 -d
```

Watch the rebalance in the logs:

```
order-processor-2 | Assigned partitions: [order_events-0]
order-processor-3 | Assigned partitions: [order_events-1]
order-processor-1 | Assigned partitions: [order_events-2]
```

Then watch the lag drain approximately 3x faster. No code changes. No producer
changes. No downtime. This is horizontal scaling of a Kafka consumer group in
practice.

### Find the actual throughput ceiling

Remove the artificial `time.sleep(2)` from `process_order_placed` and re-run.
Now the bottleneck shifts from consumer processing time to raw Kafka I/O.

On an M-series Mac with a single broker in Docker, you'll typically hit:

- Produce throughput: 50,000–100,000 small messages/second (network + disk I/O)
- At ~30,000 msg/s, broker p99 latency starts climbing

For our order workload, the single broker is never the Kafka bottleneck —
Django's Postgres writes saturate long before Kafka does. At 100 Locust users,
we're generating maybe 80–100 orders/second. The single broker handles this with
ease.

The lesson: **Kafka throughput is rarely the bottleneck in application
workloads.** What limits you is consumer processing speed and partition count,
not broker capacity. You'd need tens of thousands of high-frequency producers
before the broker itself becomes the constraint.

---

## 8. At 10x Scale — Where This Breaks

### The outbox pattern — the gap in our implementation

There's a subtle correctness bug in our current design. After
`transaction.atomic()` commits the order to Postgres, we call
`publish_order_event()`. Between these two lines, the process could crash. The
order exists in the database with no event ever published — it sits `PENDING`
forever.

At low volume this is an acceptable gap. At scale it becomes a real incident:
orders placed during a Kafka outage never get confirmed, customers email
support, and you have to manually replay events from the database.

The **transactional outbox pattern** eliminates this gap:

```python
# Instead of publishing to Kafka directly, write to an outbox table
# in the SAME transaction as the order creation.
with transaction.atomic():
    order = serializer.save()
    OutboxEvent.objects.create(
        topic='order_events',
        key=str(order.customer_id),
        payload=json.dumps(build_order_event('order_placed', order)),
        created_at=datetime.now(timezone.utc),
    )
# Transaction commits atomically: either both the order and the
# outbox event exist, or neither does.

# A separate process (the "relay") polls the outbox table,
# publishes to Kafka, and marks events as published.
# The relay can retry indefinitely without affecting the HTTP response.
```

The relay decouples "did the order commit" from "did the event reach Kafka."
This is the pattern used by teams who need strong guarantees around event
publishing. The cost: an extra DB table, an extra process, and the latency of
the relay's polling interval (typically 100ms–1s) before events are published.

### Moving to a 3-broker cluster with Multipass

The single-broker setup is zero fault tolerance. One broker's disk dies, all
data is gone. For production, the minimum is 3 brokers with
`replication.factor=3` and `min.insync.replicas=2`.

We'll use Multipass VMs — 3 Ubuntu VMs, one Kafka broker per VM — and KRaft mode
(no Zookeeper required).

**Why Multipass instead of 3 broker containers in Docker Compose?**

Three broker containers on one Docker host share the same kernel, network
bridge, and disk I/O path. A disk failure takes all three down simultaneously.
The interesting failure modes — network partition between two brokers, one
broker's disk filling up, a broker restarting mid-replication — can't be
accurately simulated when all brokers share a host. Multipass VMs have genuine
isolation. The failure scenarios are real.

**Provision the VMs:**

```bash
for i in 1 2 3; do
  multipass launch --name kafka-$i --cpus 2 --memory 4G --disk 20G 24.04
done

multipass list
# Name       State    IPv4
# kafka-1    Running  192.168.64.10
# kafka-2    Running  192.168.64.11
# kafka-3    Running  192.168.64.12
```

**`multipass/setup-broker.sh`:**

See [`multipass/setup-broker.sh`](./multipass/setup-broker.sh). The script
installs Docker on the VM, writes a `docker-compose.yml` configured for KRaft
with the correct broker ID and quorum voter list, and starts the broker. Two
things to note:

- `CLUSTER_ID` must be identical across all 3 brokers. Generate it once before
  running the loop:
  `docker run --rm confluentinc/cp-kafka:7.6.12 kafka-storage random-uuid`
- `network_mode: host` on the Kafka container is required so the broker binds to
  the VM's real IP — which is what other brokers and external clients use to
  connect.

```bash
B1=$(multipass info kafka-1 | grep IPv4 | awk '{print $2}')
B2=$(multipass info kafka-2 | grep IPv4 | awk '{print $2}')
B3=$(multipass info kafka-3 | grep IPv4 | awk '{print $2}')

for i in 1 2 3; do
  multipass transfer multipass/setup-broker.sh kafka-$i:/home/ubuntu/
  multipass exec kafka-$i -- bash /home/ubuntu/setup-broker.sh $i $B1 $B2 $B3
done
```

**Create the topic on the cluster (6 partitions — up from 3):**

```bash
multipass exec kafka-1 -- sudo docker compose exec kafka \
  kafka-topics --create \
  --topic order_events \
  --bootstrap-server ${B1}:9092 \
  --partitions 6 \
  --replication-factor 3 \
  --config min.insync.replicas=2
```

Six partitions because we can now scale to 6 consumer instances. Setting
`min.insync.replicas` at the topic level overrides the broker default and is the
right place to configure it — different topics in production may have different
durability requirements.

**Point your local services at the cluster:**

```bash
export KAFKA_BOOTSTRAP_SERVERS="${B1}:9092,${B2}:9092,${B3}:9092"
docker compose up api order-processor notification-service
```

Providing all 3 broker addresses as `bootstrap.servers` doesn't mean every
request goes to all 3. Kafka clients use the list to discover the cluster on
initial connection; after that they talk directly to the partition leader for
each topic. The list is for resilience: if one broker is down during startup,
the client tries the next one.

**Kill a broker — watch leader election:**

```bash
# See current partition leaders
multipass exec kafka-1 -- sudo docker compose exec kafka \
  kafka-topics --describe --topic order_events --bootstrap-server ${B1}:9092

# Partition: 0  Leader: 1  Replicas: 1,2,3  Isr: 1,2,3
# Partition: 1  Leader: 2  Replicas: 2,3,1  Isr: 2,3,1
# Partition: 2  Leader: 3  Replicas: 3,1,2  Isr: 3,1,2
# ...

# Kill broker 1
multipass stop kafka-1

# Partitions 0, 3, 4 (whatever was on broker 1) elect new leaders
multipass exec kafka-2 -- sudo docker compose exec kafka \
  kafka-topics --describe --topic order_events --bootstrap-server ${B2}:9092

# Partition: 0  Leader: 2  Replicas: 1,2,3  Isr: 2,3   ← broker 1 gone from Isr
# Partition: 1  Leader: 2  Replicas: 2,3,1  Isr: 2,3
```

Leader election completes in under 30 seconds. Your Django producer has
`retries=5` with `retry.backoff.ms=100` — it retries the temporary errors during
leader election automatically. Application sees no failures.

```bash
# Bring broker 1 back
multipass start kafka-1

multipass exec kafka-1 -- sudo docker compose -f /home/ubuntu/docker-compose.yml up -d

# After replication catches up (~60s), broker 1 rejoins the ISR
multipass exec kafka-1 -- sudo docker compose exec kafka \
  kafka-topics --describe --topic order_events --bootstrap-server ${B1}:9092
# Isr: 1,2,3  ← fully restored
```

**The min.insync.replicas guarantee in practice:**

```
All 3 brokers up:  ISR=3, produces succeed
1 broker down:     ISR=2, produces succeed (meets min.insync.replicas=2)
2 brokers down:    ISR=1, produces FAIL with NOT_ENOUGH_REPLICAS

```

The failure on 2-broker loss is correct behavior. The cluster refuses to
acknowledge writes that can't be durably replicated. It's better to return an
error to the producer than to accept a write that will be lost if the remaining
broker also goes down.

**Clean up when done:**

```bash
multipass delete kafka-1 kafka-2 kafka-3
multipass purge
```

### What this still doesn't simulate

At genuine production scale — millions of orders per day across hundreds of
producers and consumer groups:

**Log compaction** becomes necessary for topics that represent current state,
not event history. `order_events` is an event log — retention-based deletion is
correct. An `inventory_levels` topic representing current stock per product
would use log compaction: keep only the latest record per key, delete old ones.
This keeps the topic size bounded regardless of update frequency.

**Schema registry** becomes mandatory once multiple teams own producers and
consumers for shared topics. Manual schema versioning (Pattern 4) works for one
team owning both ends. It breaks down when you have 5 producer teams and 10
consumer teams — you need contractual schema enforcement with backward/forward
compatibility checking at publish time.

**Tiered storage** for long retention. Keeping 90 days of order events on broker
disk at 50GB/day is expensive. Kafka's tiered storage (Confluent, MSK) offloads
old log segments to object storage (S3/GCS) automatically. Consumers reading
recent data hit local disk; consumers reading old data (backfills, analytics)
hit object storage transparently.

**Partition reassignment at scale.** Adding brokers to a running cluster doesn't
automatically rebalance partitions. You use `kafka-reassign-partitions` to move
partitions to new brokers — a process that involves significant replication
traffic and should be done during off-peak hours with throttling to avoid
saturating your inter-broker network.

The architectural end state for most companies at true scale: managed Kafka.
Confluent Cloud, Amazon MSK, Aiven — they handle broker management, replication,
upgrades, and tiered storage. The concepts from this module transfer directly.
Topics, partitions, consumer groups, delivery semantics — identical. What
changes is who is paged at 3am when a broker's disk fills up.

---

---

## Key Takeaways

**Kafka is a log, not a queue.** The permanence of the log — retained records,
per-consumer offsets, replay — is what makes fan-out to independent consumers,
late consumer onboarding, and recovery from consumer downtime possible. None of
these properties are available in a job queue by design.

**Partition count is the scale ceiling, set at topic creation.** Consumer
parallelism within a group is capped at partition count. Set it higher than you
think you need. Increasing it later is possible but disruptive.

**Consumer groups are the fan-out mechanism.** A new downstream consumer means a
new consumer group reading the same topic. Zero producer changes, zero changes
to existing consumers, and the new consumer can replay history.

**At-least-once delivery with idempotent processing is the practical default.**
Exactly-once semantics exist but cost operational complexity. Natural
idempotency (like `WHERE status = 'pending'`) is better than explicit
deduplication tables when the business logic allows it.

**Lag is the operational metric that matters.** A consumer with zero errors and
growing lag is failing. Instrument it. Alert on it. Module 06 wires this into
Prometheus and Grafana properly.

**min.insync.replicas=2 on a 3-broker cluster is the production minimum.** It
means you can lose one broker with zero data loss and full availability. Two
broker losses cause produces to fail — which is correct. Accepting writes you
can't durably store is worse than refusing them.

**The transactional outbox pattern closes the gap** between "order committed to
DB" and "event published to Kafka." Without it, a crash between those two
operations leaves orders permanently pending. For anything where that matters,
implement the outbox.
