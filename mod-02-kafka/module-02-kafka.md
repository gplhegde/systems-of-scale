# Module 02 — Apache Kafka

### Systems at Scale (Local) — E-Commerce Series

> **Goal:** Understand event-driven architecture from first principles, then
> extend ShopLocal so placing an order publishes an event to Kafka, consumed
> independently by an order-processor service and a notification service.
>
> **Platform:** macOS Apple Silicon (M1/M2/M3/M4) **Prerequisite:** Module 01 —
> Docker & Docker Compose (this module extends that project) **Time estimate:**
> 10–14 hours across 2–3 sessions

---

## Table of Contents

1. [Why Kafka Exists — The Problem](#1-why-kafka-exists--the-problem)
2. [Kafka vs a Job Queue (Celery/Redis) — When to Use Which](#2-kafka-vs-a-job-queue-celeryredis--when-to-use-which)
3. [How Kafka Actually Works](#3-how-kafka-actually-works)
4. [Core Concepts](#4-core-concepts)
5. [Installing Kafka Locally on Apple Silicon](#5-installing-kafka-locally-on-apple-silicon)
6. [Kafka Fundamentals — Hands-On with the CLI](#6-kafka-fundamentals--hands-on-with-the-cli)
7. [Kafka with Python](#7-kafka-with-python)
8. [The Project — ShopLocal Order Events](#8-the-project--shoplocal-order-events)
9. [Project Walkthrough — Step by Step](#9-project-walkthrough--step-by-step)
10. [Common Errors & Fixes](#10-common-errors--fixes)
11. [What You've Learned](#11-what-youve-learned)
12. [Git Repo Structure](#12-git-repo-structure)

---

## 1. Why Kafka Exists — The Problem

Picture your Django `order_list` view from Module 01. When a customer places an
order, you might want several things to happen:

- Deduct stock (already done, synchronously, in the same request)
- Send a confirmation email
- Notify the warehouse to start packing
- Update a real-time analytics dashboard
- Trigger a fraud-check service

The naive approach is to do all of this inline, in the same Django view, before
returning a response. Three problems emerge immediately:

**1. The request gets slow.** Every synchronous call (email API, fraud service,
analytics write) adds latency. The customer is staring at a spinner while your
server talks to five other systems.

**2. A failure in one thing breaks everything.** If the email service times out,
does the whole order fail? You probably don't want a slow email provider to mean
customers can't buy anything.

**3. Tight coupling.** Your order view now needs to know about every downstream
system. Adding a new consumer (say, a loyalty points service) means modifying
the order view's code, redeploying Django, and hoping you didn't break anything.

**The first attempt at a fix: a job queue.** This is where Celery + Redis comes
in, which you likely already use. The order view pushes a task onto a queue
("send_confirmation_email", "notify_warehouse") and returns immediately. A
separate worker process picks up tasks and executes them.

This solves problems 1 and 2. But it has limits:

- **Once a worker picks up a task and it's processed, it's gone.** If you later
  add a new consumer (loyalty points), it can't see past events — only new ones
  going forward, and even then only if you remember to add a new task type and
  dispatch to it everywhere.
- **No replay.** If your analytics consumer was down for an hour and you fix it,
  those events are gone. You can't "rewind" a Redis queue.
- **Harder to fan out.** Getting multiple independent consumers to each see
  _every_ message (not compete for them) requires extra plumbing in Celery/Redis
  that doesn't come naturally.

**Kafka's approach is different: it's a durable, ordered, replayable log.**
Instead of "here's a task, whoever picks it up first does it and it's gone,"
Kafka says: "here's an event, written permanently to a log. Any number of
independent consumers can read it, each tracking their own position in the log,
and they can replay from any point in history."

This is the core mental shift: **a queue is about distributing work; a log is
about distributing facts.** Kafka is built around the second idea.

---

## 2. Kafka vs a Job Queue (Celery/Redis) — When to Use Which

This is a question you'll get asked in interviews, so it's worth being precise.

|                            | Celery + Redis (job queue)                                    | Kafka (event log)                                                                                                        |
| -------------------------- | ------------------------------------------------------------- | ------------------------------------------------------------------------------------------------------------------------ |
| **Model**                  | Task distribution — one worker consumes and removes each task | Event log — many independent consumers each read at their own pace                                                       |
| **Once consumed**          | Task is gone                                                  | Event stays in the log (until retention expires)                                                                         |
| **Replay**                 | Not possible                                                  | Replay from any offset, any time                                                                                         |
| **Adding a new consumer**  | Requires code changes to dispatch the new task type           | Just start a new consumer group reading the same topic — zero changes upstream                                           |
| **Ordering guarantees**    | Limited, depends on broker config                             | Strict ordering within a partition                                                                                       |
| **Throughput**             | Good for moderate volume                                      | Built for very high throughput (millions of events/sec)                                                                  |
| **Operational complexity** | Simple — Redis is a single process                            | Higher — Kafka needs careful capacity planning at scale                                                                  |
| **Best for**               | "Do this specific thing" (send an email, resize an image)     | "This fact happened" (order placed, payment confirmed, user signed up) — especially when multiple unrelated systems care |

**Rule of thumb:** if you're telling a worker to _do_ something, a job queue is
usually simpler and sufficient. If you're recording that something _happened_
and you don't know — or don't want to hardcode — who all needs to react to it,
Kafka's decoupling is worth the operational overhead.

Many real systems use both: Kafka for cross-service events, Celery for task
execution within a service.

---

## 3. How Kafka Actually Works

### The log abstraction

At its core, a Kafka **topic** is an append-only log file. New messages are
always written to the end. Consumers read sequentially, tracking their own
position (the **offset**). Nothing is deleted when read — messages live until a
retention policy (time-based or size-based) removes them.

```
Topic: order_events
┌────┬────┬────┬────┬────┬────┐
│ 0  │ 1  │ 2  │ 3  │ 4  │ 5  │  ← offsets (each message's position in the log)
└────┴────┴────┴────┴────┴────┘
  ↑                        ↑
  Consumer A is at offset 2   New messages appended here
  Consumer B is at offset 5
```

Consumer A and Consumer B can be completely independent services, reading the
same topic at different speeds, with no knowledge of each other.

### Partitions — how Kafka scales

A topic isn't really one log — it's split into **partitions**, each an
independently ordered log. This is the unit of parallelism:

```
Topic: order_events (3 partitions)

Partition 0:  [msg][msg][msg][msg]
Partition 1:  [msg][msg][msg]
Partition 2:  [msg][msg][msg][msg][msg]
```

Each message is assigned to a partition based on its **key** (or round-robin if
no key). All messages with the same key always land in the same partition, which
guarantees ordering _for that key_. There's no ordering guarantee _across_
partitions.

For our order system: if you key messages by `customer_id`, all events for a
given customer arrive in order — but events for different customers may be
processed in any relative order. That's almost always what you want.

### Brokers, replication, and the cluster

A Kafka **broker** is a single Kafka server process. A **cluster** is multiple
brokers working together. Partitions are distributed across brokers — partition
0 might live on broker 1, partition 1 on broker 2, etc.

For fault tolerance, each partition is replicated across multiple brokers. One
replica is the **leader** (handles all reads/writes for that partition); others
are **followers** (passively copy the leader's data). If the leader's broker
dies, a follower is promoted.

For local learning, you'll run a single broker — replication concepts matter for
production, but you don't need 3 brokers to understand consumer groups,
partitions, and ordering.

### Consumer groups — the fan-out mechanism

This is the single most important concept for understanding Kafka's flexibility.

A **consumer group** is a named set of consumers that cooperatively read a
topic. Kafka guarantees: within a group, **each partition is read by exactly one
consumer**. This gives you horizontal scaling — add more consumer instances to a
group, Kafka rebalances partitions across them, and you process more messages in
parallel.

But the real power: **different consumer groups are completely independent of
each other.** Each group tracks its own offset per partition. This means:

```
Topic: order_events (3 partitions)

Consumer Group "order-processor"     Consumer Group "notification-service"
  ├─ Consumer 1 → Partition 0          ├─ Consumer 1 → Partition 0
  ├─ Consumer 2 → Partition 1          ├─ Consumer 1 → Partition 1 (and 2)
  └─ Consumer 3 → Partition 2

  This group is at offset 47           This group is at offset 12
  (further ahead — processes faster)   (further behind — independent pace)
```

Both groups see **every single message** in the topic. They don't compete with
each other. One can be far ahead, one can be far behind, one can be down for
maintenance and catch up later by replaying from where it left off. This is
exactly the "add a new consumer without touching the publisher" property that a
job queue can't give you.

### Where Zookeeper fits in (and why you'll see KRaft too)

Historically, Kafka used **Zookeeper** — a separate distributed coordination
service — to manage cluster metadata (which broker is the leader for which
partition, cluster membership, configuration). You'll see Zookeeper in almost
every Kafka tutorial and Docker Compose example, including this one, because
it's still the most common setup in production today and in documentation.

Newer Kafka versions support **KRaft** mode, which removes the Zookeeper
dependency entirely — Kafka manages its own metadata using the Raft consensus
algorithm. KRaft is the future direction, but Zookeeper-based setups remain
extremely common in real-world systems and almost all tutorials, so we'll use
Zookeeper here for compatibility with what you'll encounter in the wild. The
concepts (topics, partitions, consumer groups) are identical either way — only
the cluster metadata management differs.

---

## 4. Core Concepts

Reference glossary — come back to this as needed.

### Topic

A named category of messages. Analogous to a table in a database, or a channel.
You create topics explicitly (or Kafka auto-creates them on first write, which
is bad practice in production).

### Partition

A topic is split into one or more partitions, each an ordered, immutable log.
Partitions are the unit of parallelism and the unit of ordering guarantee.

### Offset

A message's position within a partition. Monotonically increasing integer,
unique per partition (not globally unique across the topic).

### Producer

A client that writes (publishes) messages to a topic.

### Consumer

A client that reads (subscribes to) messages from a topic.

### Consumer Group

A named group of consumers that share the work of reading a topic. Kafka assigns
each partition to exactly one consumer within a group.

### Broker

A single Kafka server. Stores partitions, serves producer/consumer requests.

### Cluster

A set of brokers working together.

### Replication Factor

How many copies of each partition exist across brokers, for fault tolerance. In
this module, replication factor is 1 (single broker, no replication) — fine for
learning, never acceptable in production.

### Retention

How long Kafka keeps messages before deleting them (time-based, e.g. 7 days, or
size-based). Unlike a queue, messages aren't deleted on read.

### Key

An optional value attached to each message, used to determine which partition it
goes to (via hashing). Messages with the same key always land in the same
partition, preserving order for that key.

### Serialization

Messages in Kafka are just bytes. You choose how to serialize (JSON, Avro,
Protobuf). We'll use JSON for simplicity — readable, no schema registry needed,
though less efficient than binary formats at scale.

---

## 5. Installing Kafka Locally on Apple Silicon

We'll run Kafka entirely through Docker — no local JVM installation needed.

### Apple Silicon note

Confluent's Kafka images (the most widely used) now publish native `linux/arm64`
builds, so you get full performance without emulation. We'll use
`confluentinc/cp-kafka` and `confluentinc/cp-zookeeper`, both ARM64-native as of
recent versions.

### Verify Docker is running

```bash
docker --version
docker compose version
```

If you completed Module 01, Docker Desktop should already be running.

### Quick standalone test (before integrating into the project)

Create a scratch directory to test Kafka in isolation first:

```bash
mkdir -p /tmp/kafka-test && cd /tmp/kafka-test
```

Create `docker-compose.yml`:

```yaml
version: "3.9"

services:
  zookeeper:
    image: confluentinc/cp-zookeeper:7.6.0
    environment:
      ZOOKEEPER_CLIENT_PORT: 2181
      ZOOKEEPER_TICK_TIME: 2000
    ports:
      - "2181:2181"

  kafka:
    image: confluentinc/cp-kafka:7.6.0
    depends_on:
      - zookeeper
    ports:
      - "9092:9092"
    environment:
      KAFKA_BROKER_ID: 1
      KAFKA_ZOOKEEPER_CONNECT: zookeeper:2181
      # Two listeners: one for connections from other containers, one for your Mac
      KAFKA_LISTENER_SECURITY_PROTOCOL_MAP: PLAINTEXT:PLAINTEXT,PLAINTEXT_HOST:PLAINTEXT
      KAFKA_ADVERTISED_LISTENERS: PLAINTEXT://kafka:29092,PLAINTEXT_HOST://localhost:9092
      KAFKA_LISTENERS: PLAINTEXT://0.0.0.0:29092,PLAINTEXT_HOST://0.0.0.0:9092
      KAFKA_INTER_BROKER_LISTENER_NAME: PLAINTEXT
      KAFKA_OFFSETS_TOPIC_REPLICATION_FACTOR: 1
      KAFKA_AUTO_CREATE_TOPICS_ENABLE: "false"
```

**Why two listeners?** This trips up almost everyone the first time. Kafka
advertises its address to clients so they know where to connect. Containers on
the Docker network need to reach Kafka via `kafka:29092` (the service name).
Your Mac (outside Docker) needs to reach it via `localhost:9092`. Without both
listeners configured, either your host tools or your other containers will fail
to connect — this is the single most common Kafka-in-Docker bug.

Start it:

```bash
docker compose up -d
docker compose logs -f kafka
# Wait for: "started (kafka.server.KafkaServer)"
# Ctrl+C to stop following logs (containers keep running)
```

---

## 6. Kafka Fundamentals — Hands-On with the CLI

Kafka ships with CLI tools inside the broker image. We'll exec into the
container to use them — this is the standard way to interact with Kafka without
installing anything extra on your Mac.

### 6.1 Create a topic

```bash
docker compose exec kafka kafka-topics \
  --create \
  --topic test-topic \
  --bootstrap-server localhost:9092 \
  --partitions 3 \
  --replication-factor 1
```

`--bootstrap-server` is how every Kafka CLI tool and client finds the cluster —
it's the entry point address. "Bootstrap" because the client uses this one
broker to discover the full cluster metadata.

### 6.2 List and describe topics

```bash
# List all topics
docker compose exec kafka kafka-topics --list --bootstrap-server localhost:9092

# Describe a topic — see its partitions, replicas, leader
docker compose exec kafka kafka-topics \
  --describe \
  --topic test-topic \
  --bootstrap-server localhost:9092
```

Output looks like:

```
Topic: test-topic   PartitionCount: 3   ReplicationFactor: 1
  Partition: 0  Leader: 1  Replicas: 1  Isr: 1
  Partition: 1  Leader: 1  Replicas: 1  Isr: 1
  Partition: 2  Leader: 1  Replicas: 1  Isr: 1
```

### 6.3 Produce messages from the CLI

```bash
docker compose exec kafka kafka-console-producer \
  --topic test-topic \
  --bootstrap-server localhost:9092
```

This opens an interactive prompt. Type messages and press Enter:

```
> hello kafka
> this is message two
> ^C   (Ctrl+C to exit)
```

### 6.4 Consume messages from the CLI

In a new terminal:

```bash
docker compose exec kafka kafka-console-consumer \
  --topic test-topic \
  --bootstrap-server localhost:9092 \
  --from-beginning
```

You'll see both messages printed. `--from-beginning` reads from offset 0;
without it, you'd only see new messages produced after the consumer starts.

Leave this running, switch to the producer terminal, type a new message — watch
it appear instantly in the consumer terminal.

### 6.5 Understanding consumer groups via CLI

Stop the consumer (Ctrl+C). Start it again, but this time with a named group:

```bash
docker compose exec kafka kafka-console-consumer \
  --topic test-topic \
  --bootstrap-server localhost:9092 \
  --group my-test-group \
  --from-beginning
```

Let it consume everything, then Ctrl+C. Now check the group's committed offsets:

```bash
docker compose exec kafka kafka-consumer-groups \
  --describe \
  --group my-test-group \
  --bootstrap-server localhost:9092
```

Output shows `CURRENT-OFFSET`, `LOG-END-OFFSET`, and `LAG` per partition. Lag is
how far behind the consumer is — a critical metric in production (high lag means
your consumer can't keep up).

Run the same consumer command again (same group). Notice: **it doesn't replay
old messages** — it resumes from where it left off, because the group's offset
was committed. This is the core behavior to internalize: offsets are per-group,
and consumption resumes from the last committed position.

To force a replay for an existing group, you'd reset its offset:

```bash
docker compose exec kafka kafka-consumer-groups \
  --bootstrap-server localhost:9092 \
  --group my-test-group \
  --topic test-topic \
  --reset-offsets --to-earliest --execute
```

### 6.6 Two independent groups reading the same topic

This demonstrates the fan-out property directly. Open two terminals:

**Terminal A:**

```bash
docker compose exec kafka kafka-console-consumer \
  --topic test-topic --bootstrap-server localhost:9092 \
  --group group-a --from-beginning
```

**Terminal B:**

```bash
docker compose exec kafka kafka-console-consumer \
  --topic test-topic --bootstrap-server localhost:9092 \
  --group group-b --from-beginning
```

Both see all messages from the beginning, independently. Now produce a new
message — both terminals receive it immediately. Neither group "steals" messages
from the other. This is the property that makes adding a new consumer service
trivial in Kafka, unlike a job queue.

### 6.7 Clean up the test environment

```bash
cd /tmp/kafka-test
docker compose down -v
```

---

## 7. Kafka with Python

We'll use **confluent-kafka-python**, the official Confluent client. It's a thin
wrapper over `librdkafka` (a C library), making it fast and reliable — the
standard choice for production Python services.

### 7.1 Installation note for Apple Silicon

```bash
pip install confluent-kafka
```

This installs a pre-built wheel with `librdkafka` bundled — no separate C
library installation needed, and it works natively on ARM64. If you ever see a
compilation error, it almost always means pip fell back to building from source
because no wheel matched your Python version; upgrading pip
(`pip install --upgrade pip`) usually fixes it by allowing it to find the
correct wheel.

### 7.2 A minimal producer

```python
from confluent_kafka import Producer
import json

producer = Producer({
    'bootstrap.servers': 'localhost:9092',
})

def delivery_report(err, msg):
    """Called once for each message, indicating delivery success or failure."""
    if err is not None:
        print(f'Delivery failed: {err}')
    else:
        print(f'Delivered to {msg.topic()} [partition {msg.partition()}] at offset {msg.offset()}')

event = {'order_id': 1, 'status': 'placed'}

producer.produce(
    topic='test-topic',
    key=str(event['order_id']),
    value=json.dumps(event),
    callback=delivery_report
)

# produce() is asynchronous — it queues the message locally.
# flush() blocks until all queued messages are actually sent and acknowledged.
producer.flush()
```

**Why `flush()` matters:** `produce()` returns immediately after adding the
message to an internal buffer — it does NOT mean the message reached Kafka. The
client batches messages for efficiency and sends them in the background.
`flush()` blocks until the buffer is empty and all delivery callbacks have
fired. Forgetting to call `flush()` before your script exits is the most common
reason messages "go missing" — the process dies before the background send
completes.

### 7.3 A minimal consumer

```python
from confluent_kafka import Consumer
import json

consumer = Consumer({
    'bootstrap.servers': 'localhost:9092',
    'group.id': 'my-python-group',
    'auto.offset.reset': 'earliest',  # if no committed offset exists, start from the beginning
})

consumer.subscribe(['test-topic'])

try:
    while True:
        msg = consumer.poll(timeout=1.0)  # wait up to 1 second for a message

        if msg is None:
            continue
        if msg.error():
            print(f'Consumer error: {msg.error()}')
            continue

        event = json.loads(msg.value().decode('utf-8'))
        print(f'Received: {event} (partition={msg.partition()}, offset={msg.offset()})')

finally:
    consumer.close()  # commits final offsets, leaves the consumer group cleanly
```

**`auto.offset.reset` explained:** this only matters the FIRST time a consumer
group reads a topic (no committed offset yet). `'earliest'` means start from the
beginning of the log; `'latest'` means only see new messages from now on. Once
the group has committed any offset, this setting is irrelevant — it always
resumes from the committed position.

### 7.4 Offset commit strategies

By default, `confluent-kafka` auto-commits offsets periodically (every 5
seconds) in the background. This is convenient but has a subtle danger: **if
your consumer crashes after processing a message but before the next
auto-commit, you'll reprocess that message on restart.** This is called
"at-least-once" delivery — your application logic must be idempotent (safe to
run twice) if this matters.

For more control, disable auto-commit and commit manually after successfully
processing each message:

```python
consumer = Consumer({
    'bootstrap.servers': 'localhost:9092',
    'group.id': 'my-python-group',
    'auto.offset.reset': 'earliest',
    'enable.auto.commit': False,  # we'll commit manually
})

consumer.subscribe(['test-topic'])

try:
    while True:
        msg = consumer.poll(timeout=1.0)
        if msg is None or msg.error():
            continue

        event = json.loads(msg.value().decode('utf-8'))

        # Process the event — e.g., write to database
        process_event(event)

        # Only commit AFTER successful processing
        consumer.commit(msg)

finally:
    consumer.close()
```

This is the pattern we'll use in the project — it guarantees you never lose a
message due to a crash, at the cost of potentially reprocessing one (which is
why idempotency matters, covered below).

---

## 8. The Project — ShopLocal Order Events

We're extending the Module 01 Django app. The order placement flow becomes
event-driven:

```
┌─────────────┐     publishes      ┌───────────────┐
│   Django    │ ──────────────────>│  Kafka topic   │
│ (order API) │   "order_placed"   │  order_events  │
└─────────────┘                    └───────┬────────┘
                                            │
                    ┌───────────────────────┼───────────────────────┐
                    │                       │                       │
            consumer group           consumer group                 │
          "order-processor"       "notification-service"            │
                    │                       │                       │
                    ▼                       ▼                       │
          updates order status      logs/prints a mock        (you could add
          in Postgres                notification              more consumers
          (simulates warehouse                                 here later —
           confirmation)                                       analytics, etc.)
```

**Key design decision:** the Django view does NOT do any of the order processing
inline anymore. It validates the order, deducts stock (still synchronous — this
needs strong consistency, you can't oversell), creates the order in `PENDING`
status, publishes an `order_placed` event, and returns immediately. Two
independent background consumer services pick up the event:

1. **order-processor** — simulates confirming the order (in reality: checking
   warehouse availability, payment capture). After "processing," it updates the
   order status to `CONFIRMED` and publishes a follow-up `order_confirmed`
   event.
2. **notification-service** — independently consumes `order_placed`, simulates
   sending a confirmation email (prints to console — no real email provider
   needed for this exercise).

This demonstrates the exact fan-out property from Section 3: both consumers see
every order event, independently, without Django knowing or caring how many
consumers exist.

### What you'll build

**New services (each a standalone Python script, containerized):**

- `order-processor/` — Kafka consumer that "confirms" orders
- `notification-service/` — Kafka consumer that logs mock notifications

**Modified Django app:**

- `shop/events.py` — a Kafka producer wrapper
- `views.py` updated — `order_list` POST publishes an event after creating the
  order
- New endpoint `GET /shop/orders/<id>/events/` — for debugging, shows event
  publish status

**New topic:** `order_events`, 3 partitions, keyed by `customer_id` (keeps all
of one customer's order events in order)

### Final project structure (additions to Module 01)

```
module-02-kafka/
├── docker-compose.yml              ← extends Module 01's compose with Kafka + consumers
├── .env.example
├── nginx/
│   └── nginx.conf                  ← unchanged from Module 01
├── fastcommerce/                      ← copied from Module 01, with additions below
│   ├── Dockerfile
│   ├── requirements.txt            ← + confluent-kafka
│   ├── shop/
│   │   ├── events.py               ← NEW: Kafka producer wrapper
│   │   ├── views.py                ← MODIFIED: publish event on order creation
│   │   └── ... (everything else unchanged)
├── order-processor/
│   ├── Dockerfile
│   ├── requirements.txt
│   ├── consumer.py
│   └── db.py                       ← raw psycopg2 connection (no Django ORM needed)
└── notification-service/
    ├── Dockerfile
    ├── requirements.txt
    └── consumer.py
```

---

## 9. Project Walkthrough — Step by Step

### Step 1 — Copy Module 01's project

```bash
cd ~/projects/systems-at-scale-local
cp -r module-01-docker module-02-kafka
cd module-02-kafka
```

### Step 2 — Add Kafka producer wrapper to Django

**`fastcommerce/shop/events.py`** (new file):

```python
"""
Kafka producer wrapper for publishing domain events.

Design choice: we create ONE producer instance per process (module-level singleton)
rather than one per request. Creating a Producer is relatively expensive (it opens
connections, starts background threads) — reusing it across requests is standard practice.
"""
from confluent_kafka import Producer
import json
import os
import logging

logger = logging.getLogger(__name__)

KAFKA_BOOTSTRAP_SERVERS = os.environ.get('KAFKA_BOOTSTRAP_SERVERS', 'kafka:29092')
ORDER_EVENTS_TOPIC = 'order_events'

_producer = None


def get_producer():
    """Lazily initialize a single shared Producer instance."""
    global _producer
    if _producer is None:
        _producer = Producer({
            'bootstrap.servers': KAFKA_BOOTSTRAP_SERVERS,
            # 'all' = wait for the message to be fully acknowledged.
            # Safer than the default; matters more once you have replication.
            'acks': 'all',
        })
    return _producer


def _delivery_callback(err, msg):
    if err is not None:
        logger.error(f'Event delivery failed: {err}')
    else:
        logger.info(
            f'Event delivered: topic={msg.topic()} partition={msg.partition()} offset={msg.offset()}'
        )


def publish_order_event(event_type, order):
    """
    Publish an order-related event to Kafka.

    Keyed by customer_id so all events for one customer stay in order
    within a partition (see Module 02 README section 3 for why this matters).
    """
    event = {
        'event_type': event_type,           # 'order_placed', 'order_confirmed', etc.
        'order_id': order.id,
        'customer_id': order.customer_id,
        'customer_email': order.customer.email,
        'total_price': str(order.total_price),
        'status': order.status,
        'items': [
            {
                'product_id': item.product_id,
                'product_name': item.product.name,
                'quantity': item.quantity,
                'unit_price': str(item.unit_price),
            }
            for item in order.items.all()
        ],
    }

    producer = get_producer()
    producer.produce(
        topic=ORDER_EVENTS_TOPIC,
        key=str(order.customer_id),
        value=json.dumps(event),
        callback=_delivery_callback,
    )
    # poll(0) processes any pending delivery callbacks without blocking.
    # We don't call flush() here — that would block the HTTP request until
    # Kafka acknowledges, defeating the purpose of async event publishing.
    producer.poll(0)

    return event
```

**Why `poll(0)` instead of `flush()`?** This is an important production-pattern
detail. `flush()` blocks until delivery is confirmed — using it here would make
your Django request wait on Kafka, exactly what we wanted to avoid. `poll(0)` is
non-blocking; it just triggers any already-completed delivery callbacks (for
logging) without waiting. The message is queued and will be sent by the
producer's background thread. We accept a small risk: if the process crashes
microseconds after `produce()` and before the background thread sends it, the
message could be lost. For order events at this learning scale, that tradeoff is
fine and matches how most real systems are built — perfect delivery guarantees
from a web request would require synchronous acks, which most teams find too
slow for user-facing latency.

### Step 3 — Modify the order creation view

**`shoplocal/api/views.py`** — update the imports and `order_list` function:

```python
# Add to imports at the top
from .events import publish_order_event

# ... (keep all existing code) ...

# Replace the order_list function:
@api_view(['GET', 'POST'])
def order_list(request):
    if request.method == 'GET':
        orders = Order.objects.select_related('customer').prefetch_related('items__product')
        serializer = OrderSerializer(orders, many=True)
        return Response(serializer.data)

    serializer = OrderCreateSerializer(data=request.data)
    if serializer.is_valid():
        order = serializer.save()

        # Publish event AFTER the database transaction commits successfully.
        # The order is created with status=PENDING; the order-processor
        # consumer will move it to CONFIRMED asynchronously.
        publish_order_event('order_placed', order)

        return Response(OrderSerializer(order).data, status=status.HTTP_201_CREATED)
    return Response(serializer.errors, status=status.HTTP_400_BAD_REQUEST)
```

### Step 4 — Update requirements.txt

**`shoplocal/requirements.txt`** — add this line:

```
confluent-kafka==2.5.0
```

### Step 5 — Build the order-processor service

This is a standalone Python service — NOT Django. It connects directly to
Postgres with `psycopg2`, since pulling in all of Django just to update one row
would be unnecessary overhead. This is a deliberate, common pattern in
microservices: lightweight consumers don't need the full framework.

```bash
mkdir -p order-processor
```

**`order-processor/requirements.txt`**:

```
confluent-kafka==2.5.0
psycopg2-binary==2.9.9
```

**`order-processor/db.py`**:

```python
"""Minimal raw-SQL database access — no ORM needed for this small service."""
import psycopg2
import os
import time


def get_connection():
    """
    Connect with retries — the consumer container may start before
    Postgres is fully ready, even with Docker Compose healthchecks
    (there's a small window during initial cluster startup).
    """
    max_retries = 10
    for attempt in range(max_retries):
        try:
            return psycopg2.connect(
                host=os.environ.get('POSTGRES_HOST', 'db'),
                dbname=os.environ.get('POSTGRES_DB', 'shopdb'),
                user=os.environ.get('POSTGRES_USER', 'shopuser'),
                password=os.environ.get('POSTGRES_PASSWORD', 'shoppass'),
            )
        except psycopg2.OperationalError as e:
            print(f'DB connection attempt {attempt + 1}/{max_retries} failed: {e}')
            time.sleep(3)
    raise RuntimeError('Could not connect to database after retries')


def confirm_order(conn, order_id):
    """
    Update order status to 'confirmed'.

    Idempotency note: this UPDATE is safe to run more than once — setting
    status to 'confirmed' when it's already 'confirmed' has no harmful effect.
    This matters because our consumer uses at-least-once delivery (manual
    commit after processing) — a crash could cause this function to run twice
    for the same message.
    """
    with conn.cursor() as cur:
        cur.execute(
            "UPDATE api_order SET status = %s, updated_at = NOW() "
            "WHERE id = %s AND status = 'pending'",
            ('confirmed', order_id)
        )
        rows_updated = cur.rowcount
        conn.commit()
        return rows_updated > 0
```

**`order-processor/consumer.py`**:

```python
"""
Order Processor — Consumer Group: "order-processor"

Simulates the warehouse/fulfillment confirmation step. In a real system this
might check warehouse stock systems, trigger payment capture, etc. Here we
simulate a short processing delay and then mark the order confirmed.
"""
from confluent_kafka import Consumer
import json
import os
import time
import logging

from db import get_connection, confirm_order

logging.basicConfig(level=logging.INFO, format='%(asctime)s [order-processor] %(message)s')
logger = logging.getLogger(__name__)

KAFKA_BOOTSTRAP_SERVERS = os.environ.get('KAFKA_BOOTSTRAP_SERVERS', 'kafka:29092')
TOPIC = 'order_events'
GROUP_ID = 'order-processor'


def main():
    consumer = Consumer({
        'bootstrap.servers': KAFKA_BOOTSTRAP_SERVERS,
        'group.id': GROUP_ID,
        'auto.offset.reset': 'earliest',
        'enable.auto.commit': False,  # manual commit after successful processing
    })
    consumer.subscribe([TOPIC])

    db_conn = get_connection()
    logger.info(f'Started. Listening on topic="{TOPIC}" as group="{GROUP_ID}"')

    try:
        while True:
            msg = consumer.poll(timeout=1.0)

            if msg is None:
                continue
            if msg.error():
                logger.error(f'Consumer error: {msg.error()}')
                continue

            event = json.loads(msg.value().decode('utf-8'))

            # We only care about order_placed events — ignore others
            # (this topic may later carry order_confirmed, order_cancelled, etc.)
            if event.get('event_type') != 'order_placed':
                consumer.commit(msg)
                continue

            order_id = event['order_id']
            logger.info(f'Processing order {order_id} for customer {event["customer_id"]}...')

            # Simulate processing time (warehouse check, payment capture, etc.)
            time.sleep(2)

            updated = confirm_order(db_conn, order_id)
            if updated:
                logger.info(f'Order {order_id} confirmed.')
            else:
                logger.warning(f'Order {order_id} was not in pending state — skipped (already processed?)')

            # Commit AFTER successful processing — see Module 02 docs section 7.4
            consumer.commit(msg)

    except KeyboardInterrupt:
        logger.info('Shutting down...')
    finally:
        consumer.close()
        db_conn.close()


if __name__ == '__main__':
    main()
```

**`order-processor/Dockerfile`**:

```dockerfile
FROM python:3.12-slim

RUN apt-get update && apt-get install -y \
    libpq-dev \
    gcc \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY . .

CMD ["python", "consumer.py"]
```

### Step 6 — Build the notification-service

```bash
mkdir -p notification-service
```

**`notification-service/requirements.txt`**:

```
confluent-kafka==2.5.0
```

**`notification-service/consumer.py`**:

```python
"""
Notification Service — Consumer Group: "notification-service"

Independently consumes the SAME topic as order-processor. This is the
fan-out property in action: this consumer group has its own offsets and
sees every event, with zero awareness of the order-processor service.

In a real system this would call an email provider (SendGrid, SES, etc).
Here we just log a formatted "email" to demonstrate the pattern.
"""
from confluent_kafka import Consumer
import json
import os
import logging

logging.basicConfig(level=logging.INFO, format='%(asctime)s [notification-service] %(message)s')
logger = logging.getLogger(__name__)

KAFKA_BOOTSTRAP_SERVERS = os.environ.get('KAFKA_BOOTSTRAP_SERVERS', 'kafka:29092')
TOPIC = 'order_events'
GROUP_ID = 'notification-service'


def send_mock_email(event):
    items_summary = '\n'.join(
        f'    - {item["quantity"]}x {item["product_name"]} (${item["unit_price"]} each)'
        for item in event['items']
    )
    logger.info(
        f'\n'
        f'  ┌─────────────────────────────────────────────\n'
        f'  │ MOCK EMAIL to {event["customer_email"]}\n'
        f'  │ Subject: Order #{event["order_id"]} Confirmation\n'
        f'  │\n'
        f'  │ Thanks for your order!\n'
        f'{items_summary}\n'
        f'  │\n'
        f'  │ Total: ${event["total_price"]}\n'
        f'  └─────────────────────────────────────────────'
    )


def main():
    consumer = Consumer({
        'bootstrap.servers': KAFKA_BOOTSTRAP_SERVERS,
        'group.id': GROUP_ID,
        'auto.offset.reset': 'earliest',
        'enable.auto.commit': False,
    })
    consumer.subscribe([TOPIC])

    logger.info(f'Started. Listening on topic="{TOPIC}" as group="{GROUP_ID}"')

    try:
        while True:
            msg = consumer.poll(timeout=1.0)

            if msg is None:
                continue
            if msg.error():
                logger.error(f'Consumer error: {msg.error()}')
                continue

            event = json.loads(msg.value().decode('utf-8'))

            if event.get('event_type') != 'order_placed':
                consumer.commit(msg)
                continue

            send_mock_email(event)
            consumer.commit(msg)

    except KeyboardInterrupt:
        logger.info('Shutting down...')
    finally:
        consumer.close()


if __name__ == '__main__':
    main()
```

**`notification-service/Dockerfile`**:

```dockerfile
FROM python:3.12-slim

WORKDIR /app

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY . .

CMD ["python", "consumer.py"]
```

### Step 7 — Update Docker Compose

Replace **`docker-compose.yml`** entirely with this version (adds Zookeeper,
Kafka, and both consumer services to the Module 01 setup):

```yaml
version: "3.9"

services:
  zookeeper:
    image: confluentinc/cp-zookeeper:7.6.0
    restart: unless-stopped
    environment:
      ZOOKEEPER_CLIENT_PORT: 2181
      ZOOKEEPER_TICK_TIME: 2000

  kafka:
    image: confluentinc/cp-kafka:7.6.0
    restart: unless-stopped
    depends_on:
      - zookeeper
    ports:
      - "9092:9092" # exposed so you can use CLI tools / inspect from your Mac
    environment:
      KAFKA_BROKER_ID: 1
      KAFKA_ZOOKEEPER_CONNECT: zookeeper:2181
      KAFKA_LISTENER_SECURITY_PROTOCOL_MAP: PLAINTEXT:PLAINTEXT,PLAINTEXT_HOST:PLAINTEXT
      KAFKA_ADVERTISED_LISTENERS: PLAINTEXT://kafka:29092,PLAINTEXT_HOST://localhost:9092
      KAFKA_LISTENERS: PLAINTEXT://0.0.0.0:29092,PLAINTEXT_HOST://0.0.0.0:9092
      KAFKA_INTER_BROKER_LISTENER_NAME: PLAINTEXT
      KAFKA_OFFSETS_TOPIC_REPLICATION_FACTOR: 1
      KAFKA_AUTO_CREATE_TOPICS_ENABLE: "false"
    healthcheck:
      test:
        [
          "CMD",
          "kafka-topics",
          "--bootstrap-server",
          "localhost:9092",
          "--list",
        ]
      interval: 10s
      timeout: 10s
      retries: 10
      start_period: 30s

  # One-off container that creates the topic, then exits.
  # Using AUTO_CREATE_TOPICS_ENABLE=false (above) is a deliberate production-like
  # practice — topics should be explicitly created with intentional partition counts,
  # not silently auto-created with defaults on first write.
  kafka-init:
    image: confluentinc/cp-kafka:7.6.0
    depends_on:
      kafka:
        condition: service_healthy
    entrypoint: ["sh", "-c"]
    command:
      - |
        kafka-topics --create --if-not-exists \
          --topic order_events \
          --bootstrap-server kafka:29092 \
          --partitions 3 \
          --replication-factor 1
        echo "Topic created."

  db:
    image: postgres:16
    restart: unless-stopped
    environment:
      POSTGRES_DB: ${POSTGRES_DB:-shopdb}
      POSTGRES_USER: ${POSTGRES_USER:-shopuser}
      POSTGRES_PASSWORD: ${POSTGRES_PASSWORD:-shoppass}
    volumes:
      - postgres_data:/var/lib/postgresql
    healthcheck:
      test:
        [
          "CMD-SHELL",
          "pg_isready -U ${POSTGRES_USER:-shopuser} -d ${POSTGRES_DB:-shopdb}",
        ]
      interval: 10s
      timeout: 5s
      retries: 5

  redis:
    image: redis:7-alpine
    restart: unless-stopped
    healthcheck:
      test: ["CMD", "redis-cli", "ping"]
      interval: 10s
      timeout: 5s
      retries: 5

  web:
    build:
      context: ./fastcommerce
      dockerfile: Dockerfile
    restart: unless-stopped
    environment:
      DJANGO_SETTINGS_MODULE: shoplocal.settings.development
      SECRET_KEY: ${SECRET_KEY:-dev-secret-key}
      DEBUG: "True"
      POSTGRES_HOST: db
      POSTGRES_DB: ${POSTGRES_DB:-shopdb}
      POSTGRES_USER: ${POSTGRES_USER:-shopuser}
      POSTGRES_PASSWORD: ${POSTGRES_PASSWORD:-shoppass}
      REDIS_URL: redis://redis:6379/0
      KAFKA_BOOTSTRAP_SERVERS: kafka:29092
      ALLOWED_HOSTS: "*"
    depends_on:
      db:
        condition: service_healthy
      redis:
        condition: service_healthy
      kafka:
        condition: service_healthy
    volumes:
      - ./shoplocal:/app
    ports:
      - "8000:8000"
    command: >
      sh -c "python manage.py migrate &&
             python manage.py runserver 0.0.0.0:8000"

  order-processor:
    build:
      context: ./order-processor
      dockerfile: Dockerfile
    restart: unless-stopped
    environment:
      KAFKA_BOOTSTRAP_SERVERS: kafka:29092
      POSTGRES_HOST: db
      POSTGRES_DB: ${POSTGRES_DB:-shopdb}
      POSTGRES_USER: ${POSTGRES_USER:-shopuser}
      POSTGRES_PASSWORD: ${POSTGRES_PASSWORD:-shoppass}
    depends_on:
      kafka:
        condition: service_healthy
      db:
        condition: service_healthy
      kafka-init:
        condition: service_completed_successfully

  notification-service:
    build:
      context: ./notification-service
      dockerfile: Dockerfile
    restart: unless-stopped
    environment:
      KAFKA_BOOTSTRAP_SERVERS: kafka:29092
    depends_on:
      kafka:
        condition: service_healthy
      kafka-init:
        condition: service_completed_successfully

volumes:
  postgres_data:
```

**Note:** for this module we're running Django directly with `runserver`
(development mode) rather than through Nginx, to keep iteration fast while
you're learning Kafka. You already know how to add Nginx back from Module 01 if
you want the full production-like stack.

**`.env.example`** (same as Module 01):

```bash
SECRET_KEY=your-secret-key-here
POSTGRES_DB=shopdb
POSTGRES_USER=shopuser
POSTGRES_PASSWORD=shoppass
```

### Step 8 — Run it

```bash
cp .env.example .env

# Build and start everything
docker compose up --build
```

Watch the logs. You should see, in order:

1. `zookeeper` and `kafka` start up
2. `kafka-init` runs, creates the topic, exits with code 0
3. `db` and `redis` become healthy
4. `web`, `order-processor`, and `notification-service` all start

In a new terminal, seed the database:

```bash
docker compose exec web python manage.py seed_data
docker compose exec web python manage.py createsuperuser
```

### Step 9 — Place an order and watch the event flow

```bash
curl -X POST http://localhost:8000/shop/orders/ \
  -H "Content-Type: application/json" \
  -d '{"customer_id": 1, "items": [{"product_id": 1, "quantity": 2}]}'
```

The response comes back **immediately** with status `pending` — Django didn't
wait for Kafka or for the consumers.

Now watch the logs:

```bash
docker compose logs -f order-processor notification-service
```

Within a couple of seconds you should see:

```
order-processor      | Processing order 11 for customer 1...
notification-service | MOCK EMAIL to alice@example.com ...
order-processor      | Order 11 confirmed.
```

Check the order status updated in the database:

```bash
curl http://localhost:8000/api/orders/11/
# "status": "confirmed"
```

**This is the whole point of the module**: the HTTP response was fast and didn't
depend on either consumer. Both consumers reacted independently, at their own
pace, to the same event.

### Step 10 — See the fan-out and replay properties directly

**Prove independence:** stop the notification service, place an order, confirm
order-processor still works fine:

```bash
docker compose stop notification-service

curl -X POST http://localhost:8000/api/orders/ \
  -H "Content-Type: application/json" \
  -d '{"customer_id": 2, "items": [{"product_id": 2, "quantity": 1}]}'

docker compose logs -f order-processor
# Still processes and confirms the order normally
```

**Prove replay:** restart notification-service — it will pick up exactly where
it left off, since its committed offset stalled while it was down:

```bash
docker compose start notification-service
docker compose logs -f notification-service
# It catches up and sends the "missed" notification for the order placed while it was stopped
```

This is the property a Celery/Redis queue cannot give you as naturally — a
stopped consumer doesn't lose messages, and other consumers are unaffected by it
being down.

### Step 11 — Inspect Kafka directly while it's running

```bash
# List topics
docker compose exec kafka kafka-topics --list --bootstrap-server localhost:9092

# Check consumer group lag for both groups
docker compose exec kafka kafka-consumer-groups \
  --describe --group order-processor --bootstrap-server localhost:9092

docker compose exec kafka kafka-consumer-groups \
  --describe --group notification-service --bootstrap-server localhost:9092

# Watch raw events flowing through the topic
docker compose exec kafka kafka-console-consumer \
  --topic order_events \
  --bootstrap-server localhost:9092 \
  --from-beginning \
  --property print.key=true
```

---

## 10. Common Errors & Fixes

### Consumers can't connect: "Failed to resolve 'kafka:29092'"

This means a consumer container started before Kafka was ready, or you're
running the consumer script directly on your Mac (where `kafka` isn't a
resolvable hostname — only `localhost` is). Containers use `kafka:29092`; your
Mac uses `localhost:9092`. Make sure you're not mixing these up.

### "UNKNOWN_TOPIC_OR_PART" error

The topic doesn't exist yet. Since we set
`KAFKA_AUTO_CREATE_TOPICS_ENABLE=false`, check that `kafka-init` ran
successfully:

```bash
docker compose logs kafka-init
# Should show "Topic created."

# If it failed, run it manually:
docker compose exec kafka kafka-topics --create --if-not-exists \
  --topic order_events --bootstrap-server localhost:9092 \
  --partitions 3 --replication-factor 1
```

### Order stuck in "pending", never becomes "confirmed"

Check the order-processor logs for errors:

```bash
docker compose logs order-processor
```

Common cause: the consumer crashed and Docker restarted it (restart policy
`unless-stopped`), but it's now stuck retrying a bad connection. Check Postgres
connectivity:

```bash
docker compose exec order-processor python -c "from db import get_connection; get_connection(); print('OK')"
```

### Messages appear duplicated in consumer logs

This is expected behavior under "at-least-once" delivery if a consumer crashed
after processing but before committing. Our `confirm_order()` SQL is idempotent
(`WHERE status = 'pending'` guard) specifically to handle this safely — a
duplicate "order_placed" reprocessing simply updates 0 rows the second time,
which we log as a warning, not an error.

### "Group coordinator not available" right after startup

Kafka itself just started and hasn't finished electing group coordinators yet.
This resolves itself within a few seconds. If it persists beyond 30 seconds,
check Zookeeper connectivity:

```bash
docker compose logs zookeeper
docker compose logs kafka | grep -i error
```

### Consumer group shows huge lag that never decreases

Either the consumer crashed silently (check `docker compose ps` — is it still
running?) or it's stuck in an infinite retry loop on a bad message. Check logs
for repeated identical errors. As a last resort during learning, you can reset
the group's offset (loses unprocessed messages — only for dev):

```bash
docker compose stop order-processor
docker compose exec kafka kafka-consumer-groups \
  --bootstrap-server localhost:9092 \
  --group order-processor --topic order_events \
  --reset-offsets --to-latest --execute
docker compose start order-processor
```

### Apple Silicon: container exits immediately with exec format error

Hasn't happened with the images used in this module (Confluent's Kafka/Zookeeper
images are ARM64-native as of 7.x), but if you ever substitute a different Kafka
image and hit this, add `platform: linux/amd64` to that service in the compose
file.

### `psycopg2` fails to build inside order-processor image

Make sure `libpq-dev` and `gcc` are in the Dockerfile's `apt-get install` line
(see Step 5) — `psycopg2-binary` usually avoids this, but if you ever switch to
plain `psycopg2`, these system packages are required.

---

## 11. What You've Learned

**Concepts:**

- Why event logs solve problems job queues structurally can't (replay,
  independent fan-out, no consumer coupling)
- The distinction between distributing _work_ (queue) and distributing _facts_
  (log) — and when to reach for each
- Topics, partitions, offsets, and how partitioning by key preserves per-entity
  ordering
- Consumer groups as the mechanism for both horizontal scaling (within a group)
  and independent fan-out (across groups)
- At-least-once delivery semantics and why idempotent processing logic is
  required
- Why Zookeeper still appears in most real-world Kafka deployments today, and
  where KRaft fits as the newer alternative

**Practical skills:**

- Running a Kafka broker plus Zookeeper locally via Docker Compose, including
  the dual-listener configuration needed to be reachable from both other
  containers and your host machine
- Using the Kafka CLI tools (`kafka-topics`, `kafka-console-producer/consumer`,
  `kafka-consumer-groups`) to create topics, produce/consume manually, and
  inspect consumer lag
- Writing a Python producer with `confluent-kafka`, understanding the difference
  between `poll(0)` (non-blocking) and `flush()` (blocking) and when to use each
- Writing a Python consumer with manual offset commits, committing only after
  successful processing
- Designing a topic schema (event_type, keyed by customer_id) for a real domain
- Building a multi-service architecture: a Django web app and two independent,
  lightweight Python consumer services, all orchestrated by one Compose file
- Proving the fan-out and replay properties hands-on by stopping/restarting a
  consumer mid-flow

**The expanded ShopLocal system:**

- Order placement is now decoupled from order confirmation and notification
- Two independent consumer groups react to the same event stream
- The system demonstrably tolerates one consumer being down without affecting
  the other or losing data

---

## 12. Git Repo Structure

```
systems-at-scale-local/
├── README.md
├── module-01-docker/
│   └── ... (unchanged)
└── module-02-kafka/
    ├── module-02-kafka.md            ← this document
    ├── docker-compose.yml
    ├── .env.example
    ├── nginx/
    │   └── nginx.conf
    ├── shoplocal/
    │   ├── Dockerfile
    │   ├── requirements.txt
    │   ├── manage.py
    │   ├── shoplocal/...
    │   └── api/
    │       ├── events.py             ← NEW
    │       ├── views.py              ← MODIFIED
    │       └── ... (rest unchanged)
    ├── order-processor/
    │   ├── Dockerfile
    │   ├── requirements.txt
    │   ├── consumer.py
    │   └── db.py
    └── notification-service/
        ├── Dockerfile
        ├── requirements.txt
        └── consumer.py
```

**Update your repo README.md module table:**

```markdown
| #   | Topic                           | Status      |
| --- | ------------------------------- | ----------- |
| 01  | Docker & Docker Compose         | ✅ Complete |
| 02  | Apache Kafka                    | ✅ Complete |
| 03  | Kubernetes                      | 🔜 Next     |
| 04  | Apache Spark                    | ⏳ Planned  |
| 05  | Redis Advanced                  | ⏳ Planned  |
| 06  | Prometheus + Grafana            | ⏳ Planned  |
| 07  | Elasticsearch                   | ⏳ Planned  |
| 08  | gRPC                            | ⏳ Planned  |
| 09  | Terraform                       | ⏳ Planned  |
| 10  | CI/CD — GitHub Actions + ArgoCD | ⏳ Planned  |
```

---

_Next module: **Module 03 — Kubernetes** — Take this exact multi-service system
(Django, Postgres, Redis, Kafka, two consumers) and deploy it to a local
Kubernetes cluster (minikube), replacing Docker Compose's orchestration with
Deployments, Services, and ConfigMaps._
