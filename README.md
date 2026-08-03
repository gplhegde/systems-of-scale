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

A software engineer who:

- Has built and shipped production software but hasn't operated Kafka,
  Kubernetes, Spark, or similar infrastructure at scale
- Is transitioning from a startup generalist role to companies where distributed
  systems vocabulary is expected
- Wants to understand _why_ these systems are designed the way they are, not
  just how to run them

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
  underlying systems evolve and as the I find better ways to explain things.
