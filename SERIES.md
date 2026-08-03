# Systems of Scale

**A practitioner's guide to production distributed systems — running entirely on
your laptop.**

---

## What This Series Is

Most distributed systems content falls into one of two traps: it's either a
hello-world tutorial that stops before anything gets interesting, or it assumes
you have a cloud budget, a team, and a production system already running.

This series is neither. It's for engineers who already know how to build
software and want to understand how production systems actually behave — the
failure modes, the operational patterns, the tradeoffs that experienced
engineers debate. The constraint: everything runs locally, on an Apple Silicon
Mac (or any machine that is not Windows!), using Docker and occasionally
Multipass VMs to simulate real cluster topologies.

The thesis: you don't need 100 cloud nodes to learn how a system breaks under
load. You need the right setup, a load generator, and enough understanding to
interpret what you're seeing.

---

## Who This Is For

A senior or staff engineer who:

- Has built and shipped production software but hasn't operated Kafka,
  Kubernetes, Spark, or similar infrastructure at scale
- Is transitioning from a startup generalist role (Django/Postgres/Redis) to
  companies where distributed systems vocabulary is expected
- Wants to understand _why_ these systems are designed the way they are, not
  just how to run them
- Plans to talk about this work in interviews or technical writing — so depth
  and accuracy matter more than speed

This is not an introductory series. Fundamentals are covered because they're
necessary to reason about what comes next, not because the reader is new to
engineering.

---

## What "Local" Actually Means

- **Docker Compose** for single-node setups and initial exploration
- **Multipass** (Ubuntu VMs on Apple Silicon) for genuine multi-node cluster
  simulation — 3-broker Kafka, multi-node Kubernetes, etc. — when the
  single-node setup structurally can't demonstrate the concept
- **Locust** for load generation — real HTTP traffic, configurable concurrency,
  measurable throughput and latency
- **No cloud account required** for any module

The point is not to pretend a local cluster is identical to a cloud deployment.
It's to understand the concepts, hit real limits, and know exactly what changes
when you scale out.

---

## Series Structure

Each module is independent and self-contained. Modules may build on earlier ones
for context and back-link to them, but a reader arriving at any module cold
should be able to follow it without reading the rest.

Each module covers one technology or system concept through a concrete scenario
drawn from a real domain — e-commerce, ticketing, content platforms, financial
systems. The domain is chosen to make the technology's tradeoffs obvious, not
for novelty.

### Planned Modules

| #   | Technology                      | Scenario                                                        |
| --- | ------------------------------- | --------------------------------------------------------------- |
| 01  | Docker & Docker Compose         | E-commerce API — containerizing a multi-service app             |
| 02  | Apache Kafka                    | High-throughput order event pipeline — single broker to cluster |
| 03  | Kubernetes                      | Zero-downtime deploys under live traffic                        |
| 04  | Apache Spark                    | Overnight sales analytics on millions of order records          |
| 05  | Redis — Advanced Patterns       | Rate limiting, distributed locks, leaderboards                  |
| 06  | Prometheus + Grafana            | Observability for a distributed system                          |
| 07  | Elasticsearch                   | Search at scale — from LIKE queries to relevance ranking        |
| 08  | gRPC                            | Internal service communication — REST vs RPC tradeoffs          |
| 09  | Terraform                       | Infrastructure as code — reproducible environments              |
| 10  | CI/CD — GitHub Actions + ArgoCD | GitOps — code to production without manual steps                |

---

## Module Format

Every module follows this structure. The sections exist for a reason — don't
skip them.

---

### 1. The Problem

_One to two paragraphs. No setup, no preamble._

Frame the specific production problem this module addresses. What breaks without
this technology? What does failure look like in practice? What is the engineer
staring at when they decide they need this?

This section should be recognizable to anyone who has operated software at
moderate scale. It should make the reader think "yes, I've felt that."

---

### 2. How It Works — Mental Model First

_Concepts before commands. Always._

Explain how the technology actually works — not the API surface, but the
underlying model. What data structures does it use? What guarantees does it make
and why? What does it explicitly NOT guarantee?

Include:

- The core abstraction (the log, the pod, the shard, etc.)
- The unit of scale (partition, node, replica)
- The consistency/availability tradeoffs it makes
- Where the complexity lives (what is simple, what is not)

Use ASCII diagrams freely. The goal is a mental model the reader can reason from
when something goes wrong — not a vocabulary list.

---

### 3. Local Setup

_Exact, reproducible, opinionated._

Get the system running locally. Every command, every config file, every
environment variable.

Conventions:

- Explain _why_ each configuration option exists, not just what value to set. If
  a config option is cargo-culted from Stack Overflow, say so.
- Call out Apple Silicon specifics explicitly — image variants, architecture
  flags, known issues
- Keep Docker Compose files minimal — only what the module needs, no speculative
  services
- Where Multipass VMs are used to simulate a real cluster, explain exactly why
  Docker Compose alone is insufficient for this concept

The test: a reader should be able to follow this section from a fresh terminal
and have a running system at the end, without consulting anything outside this
document.

---

### 4. Core Implementation

_A real working example, not a toy._

Build something that uses the technology to solve the problem from Section 1.
Every module has a different scenario, but the implementation should:

- Be complete enough to run end-to-end
- Use real data volumes (seed scripts, realistic payloads)
- Make deliberate decisions — and explain them. Not "we use 3 partitions" but
  "we use 3 partitions because our consumer group has 3 instances and partition
  count caps consumer parallelism within a group"
- Include the code that matters, not boilerplate. Configuration and plumbing in
  the repo; logic and decisions in the article.

The repository is the source of truth for full code. The article annotates the
decisions.

---

### 5. Production Patterns

_Where senior engineers actually spend their time._

Three to five specific patterns used in real production systems. Each pattern
follows this structure:

**Pattern Name**

_The problem it solves:_ What breaks without this pattern? What does the failure
look like?

_The implementation:_ Concrete code or configuration, specific to this module's
setup.

_The tradeoff:_ What does this pattern cost? What edge cases does it introduce?
What would make you choose differently?

This section should be the densest and most referenceable part of the module. It
should answer the interview question "how would you handle X in production" for
the technology being covered.

Examples of what belongs here:

- Kafka: consumer group rebalancing strategies, exactly-once semantics
  tradeoffs, schema evolution without downtime
- Kubernetes: pod disruption budgets, horizontal pod autoscaler tuning, graceful
  shutdown
- Redis: cache stampede prevention, distributed lock expiry edge cases, memory
  eviction policy selection

---

### 6. Breaking It — Failure Modes and Observability

_What goes wrong, how you know, how you recover._

This section does three things:

**Deliberately break the system.** Not theoretical failures — actually trigger
them. Kill a broker. Saturate a consumer. Fill a disk. Inject a slow dependency.
Document exactly what commands cause which failures.

**Observe the failure.** What does it look like in logs? What metric moves?
What's the first signal that something is wrong, and is it the right signal or a
lagging indicator? Include actual log output and metric graphs (described, if
not captured as images).

**Recover.** What's the recovery procedure? Is it manual or automatic? What data
is lost, if any? What's the blast radius of this failure mode?

The goal: a reader who has worked through this section should be able to answer
"what happens when X fails?" in an interview with something more specific than
"it depends."

---

### 7. Load Testing with Locust

_Prove it with numbers, not intuition._

Use Locust to generate realistic load against the implementation from Section 4.
This section follows a deliberate arc:

**Establish a baseline.** Run a realistic workload — what does the system handle
comfortably? What are the latency percentiles (p50, p95, p99)? What's
throughput?

**Find the wall.** Increase load until something degrades. Where does the
bottleneck show up first? Is it what you expected? If not, why not?

**Understand the bottleneck.** Use the observability from Section 6 to diagnose
what's actually saturating. Is it CPU? I/O? Network? A specific component? A
configuration limit?

**Tune and re-test.** Make one targeted change. Re-run the same load. Measure
the difference. Repeat if needed.

The goal is not to achieve high numbers. It is to build the habit of measuring,
interpreting, and making evidence-based changes. The numbers themselves are
artifacts of the local hardware and will differ for every reader — the process
is what matters.

---

### 8. At 10x Scale — Where This Breaks

_Honest about limits._

The local setup in this module can handle X. In production at 10x, here's what
breaks first, and what the architectural response is.

This section does not pretend the local setup is production. It explicitly names
what changes:

- What component becomes the bottleneck first
- What the standard industry response to that bottleneck is
- What new failure modes the scaled architecture introduces
- What you'd instrument to know you're approaching the limit before you hit it

This is the bridge between "I understand this locally" and "I know what I'm
looking at in a real system."

---

## Repository Structure

```
systems-of-scale/
├── README.md               ← series overview, module index
├── SERIES.md               ← this document
├── module-01-docker/
│   ├── README.md           ← the module article (publishable)
│   ├── docker-compose.yml
│   ├── locustfile.py
│   └── src/
│       └── ...
├── module-02-kafka/
│   ├── README.md
│   ├── docker-compose.yml
│   ├── multipass/          ← VM setup scripts, if used
│   │   └── setup.sh
│   ├── locustfile.py
│   └── src/
│       └── ...
└── ...
```

Each module's `README.md` is the article. It lives next to the code it
references, so code and prose stay in sync.

---

## Writing Conventions

**Voice:** First person, practitioner to practitioner. Not instructional ("now
you will...") and not academic ("it has been shown that..."). Write as if
explaining to a senior colleague over a shared terminal.

**On "why":** Every non-obvious decision gets a sentence explaining the
reasoning. Configuration values are not magic numbers. Tradeoffs are named
explicitly, not implied.

**On completeness:** If a command is in the article, it works. If a code sample
is in the article, it runs. Nothing is pseudocode unless clearly labeled. The
repository is always the source of truth; the article is always consistent with
it.

**On length:** Each section is as long as it needs to be and no longer. The load
testing section for a simple Redis pattern may be two pages. The same section
for a 3-broker Kafka cluster under consumer rebalancing may be ten. Length
follows substance, not a word count.

**On diagrams:** ASCII in the article source. Excalidraw for published versions.
Diagrams show system topology and data flow — not UML, not class diagrams.

**On known gaps:** If something is out of scope for the local setup (TLS,
authentication, multi-region replication), say so explicitly and point to where
a reader should go next. Don't pretend the local setup covers things it doesn't.

---

## What This Series Is Not

- **Not a comprehensive reference.** The official documentation is the
  reference. This series is opinionated about what matters for an engineer
  building intuition about production systems.
- **Not a certification prep guide.** There are no multiple choice questions, no
  exam objectives.
- **Not cloud-provider specific.** AWS/GCP/Azure have managed versions of
  everything here. Understanding the underlying system makes you better at using
  the managed version — but this series doesn't cover managed services directly.
- **Not finished.** Modules will be added, revised, and corrected as the
  underlying systems evolve and as the author finds better ways to explain
  things.
