# Module 03 — Kubernetes

## Systems of Scale

**Scenario:** You have five FastCommerce services running in Docker Compose.
Deploying an update to the order API during peak traffic drops connections. A
crashed container sits dead until someone notices. One runaway service can
starve the others of memory. This module is about fixing all three — and
understanding the system well enough to operate it when things go wrong.

> **Stack:** Django, Postgres, Redis, minikube, kubectl, Locust
>
> **Apple Silicon:** minikube runs natively on ARM64. All images used have ARM64
> variants.
>
> **Prerequisites:** Docker Desktop installed and running. kubectl and minikube
> installed (covered in Section 3).

---

## Table of Contents

1. [The Problem](#1-the-problem)
2. [How It Works — Mental Model First](#2-how-it-works--mental-model-first)
3. [Local Setup](#3-local-setup)
4. [Core Implementation](#4-core-implementation)

---

## 1. The Problem

### The operational ceiling of Docker Compose

Docker Compose is excellent for what it is: a tool for defining and running
multi-container applications on a single host. For a small team running a
handful of services, it works well. The problems appear gradually, as the system
grows.

**Problem 1 — Deploying updates causes downtime.**

When you run `docker compose up --build` to deploy a new version of the order
API, Compose tears down the old container and starts the new one. During the gap
— which can be 5–30 seconds depending on image build time and startup duration —
requests to the order API either fail or queue up. If you're running under load,
that gap is visible to customers.

Let's make this concrete. Start the FastCommerce stack and run Locust against
it, then deploy an update:

```bash
# Terminal 1 — FastCommerce running via Compose
docker compose up

# Terminal 2 — Locust hammering the order API
locust -f locustfile.py --host=http://localhost:8000 \
  --headless --users 20 --spawn-rate 5

# Terminal 3 — Deploy an "update" (rebuild the API image)
docker compose up --build api
```

Watch Terminal 2. You'll see a spike in failures and response times during the
rebuild. The gap between old container stopping and new container being healthy
is dead time. There's no way to avoid it with Compose — it has no concept of
"keep the old version running until the new one is ready."

**Problem 2 — No meaningful health model.**

Compose has `healthcheck` — you can define a command that runs periodically and
marks a container healthy or unhealthy. But there's no integration between
health status and traffic routing. If the Django container is running but
returning 500 on every request (bad deploy, corrupted state, dependency
failure), Compose considers it healthy because the process is alive. Requests
keep hitting it. Errors keep returning.

What you actually need is: a container that fails health checks gets removed
from the load balancer immediately, before users see errors. Compose can't do
this. It has no load balancer to remove containers from.

**Problem 3 — Resource contention.**

Without resource limits, every container on the host competes for CPU and
memory. The analytics service kicks off a heavy aggregation job and consumes 4GB
of RAM. The order API starts getting OOM-killed because there's nothing left.
You add `mem_limit: 512m` to the Compose file, but this is just a cgroup limit —
it doesn't prevent the analytics job from being scheduled on the same host in
the first place, and it doesn't help the scheduler make intelligent placement
decisions.

**Problem 4 — Manual everything at scale.**

Five services, each with their own deploy cadence, each needing restart
policies, each potentially running multiple instances for availability. You end
up writing shell scripts around Compose. Those scripts handle restarts, health
polling, drain-and-replace logic. They become load-bearing infrastructure that
nobody fully understands. When they break at 2am, the on-call engineer is
reading bash.

### What Kubernetes is NOT the answer to

Before going further — Kubernetes is operationally expensive. Running a K8s
cluster (even a managed one) means:

- YAML manifests for every service, every environment
- Understanding a new abstraction layer with ~50 distinct object types
- A control plane that itself needs to be operated (or paid for as a managed
  service)
- A steeper debugging curve — when something breaks, the failure could be in the
  app, the pod, the node, the scheduler, the network plugin, or the ingress
  controller

A team of 3 engineers running 4 services that serve 10,000 users/day gets more
value from a well-configured single server, a simple PaaS (Fly.io, Render,
Railway), or even a well-maintained Compose setup than from operating
Kubernetes. The overhead isn't worth it at that scale.

The teams that genuinely benefit from Kubernetes are those where:

- The operational toil of NOT having orchestration has become measurable —
  failed deploys causing downtime, engineers manually restarting things at 3am
- Services need to scale independently — the API needs 10 instances, the
  background worker needs 3, the analytics service needs 1
- Multiple teams are deploying to the same infrastructure and need isolation
- Resource efficiency matters — the cluster scheduler can bin-pack workloads
  onto nodes more efficiently than manual allocation

For FastCommerce, we're at the point where Problem 1 (deploy downtime) is real
and measurable. That's the specific problem Kubernetes solves in this module.
The others come along for the ride.

---

## 2. How It Works — Mental Model First

### 2.1 The core idea — desired state

The single most important thing to understand about Kubernetes before touching
any YAML:

**You do not tell Kubernetes what to do. You tell it what you want, and it
figures out how to get there and how to keep things there.**

This is called the **desired state model**, and it's fundamentally different
from how most operational tooling works.

With Docker Compose, you run `docker compose up`. Compose executes a sequence of
imperative steps: pull images, create networks, start containers in dependency
order. If a container crashes an hour later, Compose restarts it (if you
configured `restart: unless-stopped`) — but only because you told it to. There's
no ongoing reconciliation.

With Kubernetes, you submit a manifest that says: "I want 3 replicas of this
container, with these environment variables, with these resource limits, and
with this health check." Kubernetes writes that desired state to its database.
Then a set of **controllers** runs in a continuous loop:

```
┌─────────────────────────────────────────────────────┐
│              The Reconciliation Loop                │
│                                                     │
│   1. Observe:  What is the current state?           │
│                (2 replicas running, 1 crashed)      │
│                                                     │
│   2. Diff:     How does current differ from         │
│                desired state?                       │
│                (desired=3, current=2, diff=1)       │
│                                                     │
│   3. Act:      Take the smallest action to          │
│                reduce the diff.                     │
│                (schedule a new pod)                 │
│                                                     │
│   4. Repeat.   Forever.                             │
└─────────────────────────────────────────────────────┘
```

This loop runs continuously, for every object in the cluster. You declare what
you want. Kubernetes keeps the world matching that declaration, regardless of
what happens — pod crashes, node failures, flapping network. This is what
"self-healing" means mechanically: it's not magic, it's the reconciliation loop
detecting drift from desired state and correcting it.

The practical implication: `kubectl apply -f deployment.yaml` does not "run"
anything directly. It updates the desired state in Kubernetes' database.
Controllers then detect the change and act on it. The distinction matters when
things go wrong — the place to look is often "what does Kubernetes think the
desired state is" vs "what is actually running."

### 2.2 The cluster — what's actually running

A Kubernetes cluster has two logical layers: the **control plane** and the
**worker nodes**.

```
┌─────────────────────────────────────────────────────────────────┐
│                        KUBERNETES CLUSTER                       │
│                                                                 │
│  ┌──────────────────────────────────────────────────────────┐   │
│  │                     CONTROL PLANE                        │   │
│  │                                                          │   │
│  │  ┌─────────────┐  ┌──────┐  ┌───────────┐  ┌────────┐  │   │
│  │  │  API Server │  │ etcd │  │ Scheduler │  │  Ctrl  │  │   │
│  │  │  (gateway)  │  │ (DB) │  │(placement)│  │ Manager│  │   │
│  │  └──────┬──────┘  └──────┘  └───────────┘  └────────┘  │   │
│  │         │                                               │   │
│  └─────────┼─────────────────────────────────────────────-─┘   │
│            │ kubectl / apps talk to the API server              │
│  ┌─────────┼──────────────┐  ┌──────────────────────────────┐  │
│  │   WORKER NODE 1        │  │       WORKER NODE 2          │  │
│  │                        │  │                              │  │
│  │  ┌─────────┐           │  │  ┌─────────┐                │  │
│  │  │ kubelet │           │  │  │ kubelet │                │  │
│  │  └─────────┘           │  │  └─────────┘                │  │
│  │  ┌──────┐  ┌──────┐    │  │  ┌──────┐  ┌──────┐        │  │
│  │  │ Pod  │  │ Pod  │    │  │  │ Pod  │  │ Pod  │        │  │
│  │  └──────┘  └──────┘    │  │  └──────┘  └──────┘        │  │
│  └────────────────────────┘  └──────────────────────────────┘  │
└─────────────────────────────────────────────────────────────────┘
```

**The API server** is the single entry point for all cluster operations.
`kubectl apply`, health checks, controller loops, the scheduler — everything
goes through the API server. It's stateless; all state is in etcd.

**etcd** is the cluster's database. It stores the desired state of every object
in the cluster. If etcd loses data, the cluster loses its memory of what should
be running. In production, etcd runs as a 3 or 5 node cluster with its own
replication and backups. In minikube, it's a single instance.

**The scheduler** watches for pods that have been created but not yet assigned
to a node. It scores each available node based on resource availability,
affinity rules, and taints/tolerations, then assigns the pod to the best node.
It does not start the pod — it just writes the node assignment to etcd.

**The controller manager** runs a collection of controllers — each one
responsible for a specific object type. The Deployment controller watches
Deployments and creates/updates ReplicaSets. The ReplicaSet controller watches
ReplicaSets and creates/deletes Pods. The Node controller watches for nodes that
stop heartbeating and marks them as unavailable. Each controller runs the
reconciliation loop for its object type.

**The kubelet** runs on every worker node. It watches the API server for pods
assigned to its node and ensures those pods are running. If a container in a pod
crashes, the kubelet restarts it. The kubelet also runs liveness and readiness
probes and reports results back to the API server.

For local development with minikube, control plane and worker node run on the
same machine (inside a VM). The separation is logical, not physical.

### 2.3 The core objects — bottom up

Kubernetes has around 50 built-in object types. You need to understand 8 of them
to operate a real application. Here they are in the order they build on each
other.

#### Pod

The smallest deployable unit in Kubernetes. A pod is one or more containers that
share:

- A network namespace (same IP address, can communicate via localhost)
- Storage volumes
- Lifecycle (they start and stop together)

```yaml
# You almost never write this directly — but this is what everything else
# creates under the hood
apiVersion: v1
kind: Pod
metadata:
  name: fastcommerce-api-xyz
spec:
  containers:
    - name: api
      image: fastcommerce-api:v2
      ports:
        - containerPort: 8000
```

**Why you almost never create pods directly:** pods are ephemeral. If the node
they're running on fails, the pod is gone and nothing recreates it. Pods have no
self-healing. You use higher-level objects (Deployments) that manage pods on
your behalf.

#### ReplicaSet

Ensures a specified number of pod replicas are running at all times. If a pod
crashes, the ReplicaSet controller creates a new one. If there are too many, it
deletes some.

**Why you almost never create ReplicaSets directly:** they don't manage updates.
If you change the pod template in a ReplicaSet, existing pods are not updated —
you'd have to delete them manually for the new template to take effect.
Deployments handle this.

#### Deployment

The object you actually create for stateless workloads. A Deployment manages
ReplicaSets and owns the rolling update logic. When you update the pod template
in a Deployment, it creates a new ReplicaSet with the new template and gradually
scales it up while scaling down the old one.

```
Deployment: fastcommerce-api (desired: 3 replicas)
│
├── ReplicaSet v1 (image: api:v1) — 3 pods running  ← before update
│
└── After kubectl apply with image: api:v2:
    ├── ReplicaSet v1 (image: api:v1) — scaling down
    └── ReplicaSet v2 (image: api:v2) — scaling up
```

#### Service

Pods have ephemeral IP addresses — they change every time a pod is created. A
Service provides a stable network endpoint (a fixed IP and DNS name) in front of
a set of pods. Traffic to the Service is load-balanced across all healthy pods
that match the Service's selector.

```
                    ┌─────────────────────────┐
Client request ────>│  Service: api-service   │
                    │  ClusterIP: 10.96.0.100 │
                    │  port: 80 → 8000        │
                    └────────────┬────────────┘
                                 │ routes to
                    ┌────────────┼────────────┐
                    ▼            ▼            ▼
                  Pod 1        Pod 2        Pod 3
               (api:v2)     (api:v2)     (api:v2)
               10.1.0.1     10.1.0.2     10.1.0.3
               (IPs change  every time   pods restart)
```

Services use **label selectors** to find their pods — not pod names or IPs. Any
pod with the label `app: fastcommerce-api` gets traffic from the Service. This
is how rolling updates work transparently: new pods get the label, old pods lose
traffic as they're terminated.

#### Ingress

A Service of type ClusterIP is only reachable inside the cluster. An Ingress
exposes HTTP/HTTPS routes from outside the cluster to Services inside it. It's a
set of routing rules — "requests to `/api/` go to the `api-service`" — that an
**Ingress controller** (nginx, Traefik, etc.) implements.

```
Internet ──> Ingress Controller ──> Ingress rules ──> Service ──> Pods
             (nginx pod in cluster)  (your YAML)
```

#### ConfigMap

Non-sensitive configuration stored as key-value pairs in the cluster. Mounted
into pods as environment variables or files. Changing a ConfigMap and restarting
pods picks up the new values without rebuilding the image.

```yaml
apiVersion: v1
kind: ConfigMap
metadata:
  name: fastcommerce-config
data:
  DJANGO_SETTINGS_MODULE: "fastcommerce.settings"
  ALLOWED_HOSTS: "*"
  DEBUG: "False"
```

#### Secret

Like a ConfigMap but for sensitive values. Kubernetes base64-encodes the values
(this is NOT encryption — base64 is trivially reversible). In production,
Secrets are protected by RBAC (who can read them) and optionally encrypted at
rest in etcd. For local development, they're essentially ConfigMaps with a label
that says "treat this as sensitive."

```yaml
apiVersion: v1
kind: Secret
metadata:
  name: fastcommerce-secrets
type: Opaque
data:
  POSTGRES_PASSWORD: ZmFzdHBhc3M= # base64 of "fastpass"
  SECRET_KEY: ZGV2LXNlY3JldC1rZXk=
```

#### PersistentVolume and PersistentVolumeClaim

Pods are stateless by default — data written to a container's filesystem is lost
when the pod restarts. A **PersistentVolume (PV)** is a piece of storage in the
cluster (a disk, an NFS share, a cloud disk). A **PersistentVolumeClaim (PVC)**
is a request for storage by a pod — "I need 5GB of storage." Kubernetes binds
the PVC to an available PV.

In practice: you create a PVC in your manifest. On minikube, Kubernetes
automatically creates a PV backed by a directory on the minikube VM. On a cloud
cluster, it provisions a cloud disk. The PVC is what your pod references — the
underlying PV is an implementation detail.

```
Pod (Postgres) ──references──> PVC (5Gi) ──bound to──> PV (actual disk)
```

Data in a PV survives pod restarts and rescheduling to different nodes. This is
how Postgres data persists when the Postgres pod restarts.

#### How the objects relate

```
┌─────────────────────────────────────────────────────────────────┐
│                         NAMESPACE: fastcommerce                 │
│                                                                 │
│  ┌────────────┐    manages    ┌────────────┐  manages           │
│  │ Deployment │──────────────>│ ReplicaSet │──────────> Pods    │
│  └────────────┘               └────────────┘            │  │  │ │
│                                                         │  │  │ │
│  ┌────────────┐    selects pods by label                │  │  │ │
│  │  Service   │<────────────────────────────────────────┘  │  │ │
│  └─────┬──────┘                                            │  │ │
│        │                                                   │  │ │
│  ┌─────▼──────┐                                            │  │ │
│  │  Ingress   │  routes external HTTP to Service           │  │ │
│  └────────────┘                                            │  │ │
│                                                            │  │ │
│  ┌────────────┐    mounted as env vars into ───────────────┘  │ │
│  │ ConfigMap  │                                               │ │
│  └────────────┘                                               │ │
│                                                               │ │
│  ┌────────────┐    mounted as env vars into ─────────────────┘ │
│  │   Secret   │                                                 │
│  └────────────┘                                                 │
│                                                                 │
│  ┌────────────┐    mounted as volume into ─────────────────────┘
│  │    PVC     │
│  └────────────┘
└─────────────────────────────────────────────────────────────────┘
```

### 2.4 How a rolling update actually works

This is the core mechanism that solves Problem 1 from Section 1. Walk through it
step by step.

Before the update: Deployment has 3 pods running `api:v1`, all behind the
Service, all receiving traffic.

```
Deployment (desired: 3, image: api:v1)
├── ReplicaSet-v1 (desired: 3)
│   ├── Pod-1 [api:v1] READY ✓  ← receiving traffic
│   ├── Pod-2 [api:v1] READY ✓  ← receiving traffic
│   └── Pod-3 [api:v1] READY ✓  ← receiving traffic
```

You run `kubectl apply -f deployment.yaml` with `image: api:v2`. Kubernetes
detects the change and starts a rolling update. With `maxSurge: 1` and
`maxUnavailable: 0`:

**Step 1:** Create one new pod with `api:v2`. Total pods: 4 (one over desired).

```
├── ReplicaSet-v1 (desired: 3) → 3 running
├── ReplicaSet-v2 (desired: 0) → scaling up
│   └── Pod-4 [api:v2] STARTING...
```

**Step 2:** Wait for Pod-4 to pass its **readiness probe**. Until it does, it
receives NO traffic from the Service. This is the key — the new pod is running
but invisible to clients until it proves it can serve requests.

```
│   └── Pod-4 [api:v2] READY ✓  ← now receiving traffic
```

**Step 3:** Now that the new pod is ready, terminate one old pod.

```
├── ReplicaSet-v1: Pod-1 [api:v1] TERMINATING
│                  Pod-2 [api:v1] READY ✓
│                  Pod-3 [api:v1] READY ✓
├── ReplicaSet-v2: Pod-4 [api:v2] READY ✓
```

**Step 4:** Repeat — bring up another `api:v2` pod, wait for readiness,
terminate another `api:v1` pod.

**Step 5:** All three slots filled with `api:v2`. ReplicaSet-v1 scales to 0 (but
is kept around for potential rollback).

```
Deployment (desired: 3, image: api:v2)
├── ReplicaSet-v1 (desired: 0) — kept for rollback
└── ReplicaSet-v2 (desired: 3)
    ├── Pod-4 [api:v2] READY ✓
    ├── Pod-5 [api:v2] READY ✓
    └── Pod-6 [api:v2] READY ✓
```

Throughout this process, there were always 3 pods receiving traffic (because
`maxUnavailable: 0` prevents taking a pod down before a new one is ready).
Clients see no downtime. Locust sees no failures.

**What if the new pods never become ready?** The rollout stalls. Old pods keep
running. No traffic is routed to the broken new pods. You get a window to
diagnose and either fix the image or run `kubectl rollout undo deployment/api`.
The cluster never leaves itself in a broken state — it holds the last known good
configuration until you tell it otherwise.

### 2.5 Networking inside a cluster

Kubernetes networking has three layers, each solving a different problem.

**Pod-to-pod networking**

Every pod gets its own IP address from a flat network — pods can reach each
other directly by IP, regardless of which node they're on. This is implemented
by a **Container Network Interface (CNI) plugin** (Calico, Flannel, Cilium,
etc.). minikube uses a built-in CNI.

Pod IPs are ephemeral. Every time a pod is created, it gets a new IP. This is
why you never talk to pods directly.

**Service networking — stable endpoints**

A Service gets a stable **ClusterIP** that never changes. kube-proxy on each
node maintains iptables rules that intercept traffic to the ClusterIP and
redirect it to one of the healthy pods behind the Service.

Inside the cluster, services are also reachable by DNS:

```
http://api-service                                  # same namespace
http://api-service.fastcommerce                     # cross-namespace
http://api-service.fastcommerce.svc.cluster.local   # fully qualified
```

This DNS resolution is provided by **CoreDNS**, which runs as a pod in the
cluster and watches for new Services. When you create a Service, CoreDNS
automatically creates a DNS record for it within seconds.

**External traffic — Service types and Ingress**

```
ClusterIP   — only reachable inside the cluster (default)
NodePort    — exposes the Service on a port on every node's IP
LoadBalancer — provisions an external load balancer (cloud only;
               on minikube, requires `minikube tunnel`)
Ingress     — HTTP/HTTPS routing rules, implemented by an Ingress
               controller running inside the cluster
```

For the FastCommerce API:

- Postgres and Redis use ClusterIP — only the Django app needs to reach them
- The Django API uses a LoadBalancer Service (or Ingress) — external traffic
  needs to reach it
- On minikube, `minikube tunnel` creates a network route from your Mac to the
  minikube VM so LoadBalancer Services get a reachable IP

### 2.6 The kubectl mental model

`kubectl` is the CLI that talks to the Kubernetes API server. Almost everything
you do with a cluster goes through it.

**Declarative vs imperative**

```bash
# Imperative — tell Kubernetes what to do
kubectl create deployment api --image=fastcommerce-api:v1
kubectl scale deployment api --replicas=3

# Declarative — tell Kubernetes what you want
kubectl apply -f deployment.yaml
```

Prefer declarative (`apply`) for everything that goes into version control.
Imperative commands are useful for one-off debugging operations. The reason:
`apply` is idempotent — running it twice produces the same result. Imperative
commands may fail if the resource already exists (`create`) or produce
unexpected results if the current state doesn't match what you assumed.

**Reading a manifest**

Every Kubernetes manifest has four top-level fields:

```yaml
apiVersion: apps/v1      # which API group and version this object belongs to
kind: Deployment         # what type of object this is
metadata:                # identity
  name: fastcommerce-api
  namespace: fastcommerce
  labels:                # arbitrary key-value pairs for selecting/filtering
    app: fastcommerce-api
spec:                    # the desired state — different for every kind
  replicas: 3
  ...
```

**Labels and selectors — how objects find each other**

Labels are key-value pairs attached to objects. Selectors are queries against
labels. This is how Services find their pods, how Deployments own their
ReplicaSets, and how you filter objects in `kubectl get`.

```yaml
# The Deployment creates pods with these labels:
spec:
  template:
    metadata:
      labels:
        app: fastcommerce-api    # ← pod has this label
        version: v2

# The Service selects pods with this label:
spec:
  selector:
    app: fastcommerce-api        # ← matches pods with app=fastcommerce-api
```

Any pod with the label `app: fastcommerce-api` gets traffic from this Service —
regardless of which Deployment created it, which node it's on, or what its IP
is. Labels are the glue that holds Kubernetes objects together.

**Essential kubectl commands**

```bash
# Apply a manifest (create or update)
kubectl apply -f manifest.yaml
kubectl apply -f k8s/          # apply all manifests in a directory

# Get objects
kubectl get pods -n fastcommerce
kubectl get pods -n fastcommerce -o wide    # includes node and IP
kubectl get all -n fastcommerce             # pods, services, deployments

# Describe — full details including events (most useful for debugging)
kubectl describe pod <pod-name> -n fastcommerce
kubectl describe deployment fastcommerce-api -n fastcommerce

# Logs
kubectl logs <pod-name> -n fastcommerce
kubectl logs <pod-name> -n fastcommerce -f          # follow
kubectl logs <pod-name> -n fastcommerce --previous  # last crashed container

# Execute a command in a running pod
kubectl exec -it <pod-name> -n fastcommerce -- bash
kubectl exec -it <pod-name> -n fastcommerce -- python manage.py shell

# Rolling updates
kubectl rollout status deployment/fastcommerce-api -n fastcommerce
kubectl rollout history deployment/fastcommerce-api -n fastcommerce
kubectl rollout undo deployment/fastcommerce-api -n fastcommerce

# Scaling
kubectl scale deployment fastcommerce-api --replicas=5 -n fastcommerce

# Delete
kubectl delete -f manifest.yaml
kubectl delete pod <pod-name> -n fastcommerce  # pod is recreated by ReplicaSet
```

---

## 3. Local Setup

### Install prerequisites

```bash
# kubectl — the Kubernetes CLI
brew install kubectl

# minikube — local Kubernetes cluster
brew install minikube

# Verify
kubectl version --client
minikube version
```

### Start minikube

```bash
# Start with enough resources for our stack
# 4 CPUs and 8GB RAM is comfortable for Django + Postgres + Redis + ingress
minikube start \
  --cpus=4 \
  --memory=8192 \
  --driver=docker \
  --kubernetes-version=v1.35.1

# Verify the cluster is running
kubectl cluster-info
kubectl get nodes
# NAME       STATUS   ROLES           AGE   VERSION
# minikube   Ready    control-plane   30s   v1.35.1
```

**Why `--driver=docker`?** On Apple Silicon, minikube can use Docker Desktop's
VM as the host for the K8s cluster rather than creating a separate VM via
HyperKit or QEMU. This uses fewer resources and has fewer ARM64 compatibility
issues. Docker Desktop must be running before you start minikube.

### Enable the ingress addon

minikube ships with optional addons. The ingress addon installs an nginx Ingress
controller into the cluster:

```bash
minikube addons enable ingress

# Verify the ingress controller is running
kubectl get pods -n ingress-nginx
# NAME                                        READY   STATUS    RESTARTS
# ingress-nginx-controller-<hash>             1/1     Running   0
```

This may take 60–90 seconds on first run (it pulls the nginx controller image).

### Enable the metrics server addon

The metrics server is required for `kubectl top` (resource usage) and for the
Horizontal Pod Autoscaler in Section 5:

```bash
minikube addons enable metrics-server

kubectl get pods -n kube-system | grep metrics-server
# metrics-server-<hash>   1/1   Running   0
```

### Build the FastCommerce image into minikube

minikube runs its own Docker daemon, separate from Docker Desktop. Images built
on your Mac are not automatically available inside the cluster. You have two
options:

**Option A — Point your shell at minikube's Docker daemon:**

```bash
# This command outputs environment variables to redirect docker commands
# to minikube's Docker daemon
eval $(minikube docker-env)

# Now build — the image goes directly into minikube's image cache
docker build -t fastcommerce-api:v1 ./src/django-api/

# Verify
docker images | grep fastcommerce
```

Run `eval $(minikube docker-env --unset)` to restore your normal Docker context
when you're done.

**Option B — Load a pre-built image into minikube:**

```bash
# Build normally with Docker Desktop
docker build -t fastcommerce-api:v1 ./src/django-api/

# Load the image into minikube
minikube image load fastcommerce-api:v1
```

We'll use Option A throughout this module.

**Critical: set `imagePullPolicy: Never` in your manifests** when using locally
built images. Without this, Kubernetes tries to pull the image from Docker Hub
and fails because `fastcommerce-api:v1` doesn't exist there.

```yaml
spec:
  containers:
    - name: api
      image: fastcommerce-api:v1
      imagePullPolicy: Never # use the locally built image
```

### Set up minikube tunnel

For LoadBalancer Services to get an externally reachable IP on Mac, you need
minikube tunnel running:

```bash
# Run this in a separate terminal — it needs to stay running
# It will ask for your sudo password (it adds routes to your Mac's network)
sudo minikube tunnel
```

With the tunnel running, LoadBalancer Services get an EXTERNAL-IP of `127.0.0.1`
— reachable from your Mac as `http://localhost`.

### Verify the setup before deploying FastCommerce

Deploy a test pod to confirm the cluster is working end-to-end:

```bash
# Run a single nginx pod
kubectl run nginx-test --image=nginx --port=80

# Expose it as a LoadBalancer service
kubectl expose pod nginx-test --type=LoadBalancer --port=80

# Wait for the external IP (with minikube tunnel running)
kubectl get service nginx-test --watch
# NAME         TYPE           CLUSTER-IP     EXTERNAL-IP   PORT(S)
# nginx-test   LoadBalancer   10.96.x.x      127.0.0.1     80:xxxxx/TCP

# Hit it
curl http://localhost
# <html><body><h1>Welcome to nginx!</h1></body></html>

# Clean up
kubectl delete pod nginx-test
kubectl delete service nginx-test
```

If that works, the cluster, network, and tunnel are all functioning.

---

## 4. Core Implementation

### Directory structure

```
module-03-kubernetes/
├── README.md                     ← this document (parts 1 and 2 combined)
├── locustfile.py
├── src/
│   └── django-api/               ← FastCommerce Django app (same as Module 02)
│       ├── Dockerfile
│       ├── requirements.txt
│       └── ...
└── k8s/
    ├── namespace.yaml
    ├── configmap.yaml
    ├── secret.yaml
    ├── postgres/
    │   ├── pvc.yaml
    │   ├── deployment.yaml
    │   └── service.yaml
    ├── redis/
    │   ├── deployment.yaml
    │   └── service.yaml
    ├── api/
    │   ├── deployment.yaml
    │   └── service.yaml
    └── ingress.yaml
```

All K8s manifests live in `k8s/`. Each service has its own subdirectory. We
apply them in order: namespace → config → storage → stateful services →
stateless services → ingress.

### Build the image

```bash
# Point at minikube's Docker daemon
eval $(minikube docker-env)

# Build
docker build -t fastcommerce-api:v1 ./src/django-api/

# Verify it's in minikube's image cache
docker images | grep fastcommerce
# fastcommerce-api   v1   <id>   <age>   <size>
```

### `k8s/namespace.yaml`

Namespaces provide a virtual boundary within the cluster. All FastCommerce
objects live in the `fastcommerce` namespace — isolated from the default
namespace and from any other workloads on the cluster.

```yaml
apiVersion: v1
kind: Namespace
metadata:
  name: fastcommerce
  labels:
    # Labels on the namespace itself — useful for network policies later
    project: fastcommerce
```

```bash
kubectl apply -f k8s/namespace.yaml
kubectl get namespaces | grep fastcommerce
```

### `k8s/configmap.yaml`

Non-sensitive Django configuration. These values are injected into pods as
environment variables. Changing this ConfigMap and rolling the Deployment picks
up the new values — no image rebuild required.

```yaml
apiVersion: v1
kind: ConfigMap
metadata:
  name: fastcommerce-config
  namespace: fastcommerce
data:
  # Django settings
  DJANGO_SETTINGS_MODULE: "fastcommerce.settings"
  DEBUG: "False"
  ALLOWED_HOSTS: "*"

  # Database connection — hostname is the Postgres Service name.
  # Inside the cluster, Services are reachable by their name.
  # "postgres-service" resolves to the ClusterIP of the Postgres Service.
  POSTGRES_HOST: "postgres-service"
  POSTGRES_DB: "fastdb"
  POSTGRES_PORT: "5432"

  # Redis — same pattern
  REDIS_URL: "redis://redis-service:6379/0"
```

### `k8s/secret.yaml`

Sensitive values. In Kubernetes, Secret values must be base64-encoded in the
manifest. This is NOT encryption — it is just encoding. Anyone with
`kubectl get secret` access can decode them trivially. Real secret management
uses external systems (HashiCorp Vault, AWS Secrets Manager, Sealed Secrets)
that are out of scope here.

```bash
# Generate base64 values
echo -n "fastpass" | base64      # ZmFzdHBhc3M=
echo -n "fastuser" | base64      # ZmFzdHVzZXI=
echo -n "dev-secret-key-change-in-production" | base64
```

```yaml
apiVersion: v1
kind: Secret
metadata:
  name: fastcommerce-secrets
  namespace: fastcommerce
# Opaque is the default Secret type — arbitrary key-value pairs.
# Other types exist for TLS certs (kubernetes.io/tls) and
# Docker registry credentials (kubernetes.io/dockerconfigjson).
type: Opaque
data:
  # All values must be base64 encoded
  POSTGRES_USER: ZmFzdHVzZXI=
  POSTGRES_PASSWORD: ZmFzdHBhc3M=
  SECRET_KEY: ZGV2LXNlY3JldC1rZXktY2hhbmdlLWluLXByb2R1Y3Rpb24=
```

```bash
kubectl apply -f k8s/configmap.yaml
kubectl apply -f k8s/secret.yaml

# Verify (values are shown base64-encoded)
kubectl get secret fastcommerce-secrets -n fastcommerce -o yaml
```

### `k8s/postgres/pvc.yaml`

Postgres needs storage that survives pod restarts. A PersistentVolumeClaim
requests storage from the cluster. On minikube, this is automatically backed by
a directory in the minikube VM.

```yaml
apiVersion: v1
kind: PersistentVolumeClaim
metadata:
  name: postgres-pvc
  namespace: fastcommerce
spec:
  # StorageClass determines what kind of storage is provisioned.
  # "standard" is minikube's default — a hostPath volume on the VM.
  # On cloud clusters, you'd use "gp2" (AWS), "standard" (GCP), etc.
  storageClassName: standard
  accessModes:
    # ReadWriteOnce: the volume can be mounted read-write by a single node.
    # This is correct for Postgres — only one pod should write to it.
    # Other modes: ReadOnlyMany (multiple nodes, read-only),
    # ReadWriteMany (multiple nodes, read-write — requires NFS or similar).
    - ReadWriteOnce
  resources:
    requests:
      storage: 10Gi
```

```bash
kubectl apply -f k8s/postgres/pvc.yaml

kubectl get pvc -n fastcommerce
# NAME           STATUS   VOLUME       CAPACITY   ACCESS MODES
# postgres-pvc   Bound    pvc-<hash>   5Gi        RWO
```

`STATUS: Bound` means Kubernetes found a PersistentVolume to satisfy the claim.
If it shows `Pending`, the cluster can't provision storage — on minikube this
usually means the `standard` storage class isn't enabled.

### `k8s/postgres/deployment.yaml`

```yaml
apiVersion: apps/v1
kind: Deployment
metadata:
  name: postgres
  namespace: fastcommerce
spec:
  # Postgres is stateful — running multiple replicas with a single PVC
  # using ReadWriteOnce would cause data corruption. Keep replicas at 1.
  # For production HA Postgres, use a StatefulSet with separate PVCs per
  # replica, or a managed database service. This is out of scope here.
  replicas: 1
  selector:
    matchLabels:
      app: postgres
  template:
    metadata:
      labels:
        app: postgres
    spec:
      containers:
        - name: postgres
          image: postgres:18-alpine
          ports:
            - containerPort: 5432
          env:
            # envFrom injects all keys from a ConfigMap or Secret as env vars.
            # Individual env entries (below) reference specific keys.
            - name: POSTGRES_DB
              valueFrom:
                configMapKeyRef:
                  name: fastcommerce-config
                  key: POSTGRES_DB
            - name: POSTGRES_USER
              valueFrom:
                secretKeyRef:
                  name: fastcommerce-secrets
                  key: POSTGRES_USER
            - name: POSTGRES_PASSWORD
              valueFrom:
                secretKeyRef:
                  name: fastcommerce-secrets
                  key: POSTGRES_PASSWORD
          volumeMounts:
            - name: postgres-storage
              # This is the path inside the container where Postgres stores data.
              # Mounting the PVC here means data survives pod restarts.
              mountPath: /var/lib/postgresql
          resources:
            requests:
              # requests: what the scheduler uses to decide pod placement.
              # Kubernetes guarantees these resources are available on the node.
              memory: "256Mi"
              cpu: "250m" # 250m = 0.25 cores
            limits:
              # limits: the hard cap. Exceeding memory limit = OOMKill.
              # Exceeding CPU limit = throttling (not killed).
              memory: "512Mi"
              cpu: "500m"
          livenessProbe:
            # Liveness: is this container alive? If it fails, kubelet restarts
            # the container. Use for deadlock detection — situations where the
            # process is running but permanently stuck.
            exec:
              command: ["pg_isready", "-U", "fastuser", "-d", "fastdb"]
            initialDelaySeconds: 30 # wait before first probe (startup time)
            periodSeconds: 10 # how often to probe
            failureThreshold: 3 # consecutive failures before restart
      volumes:
        - name: postgres-storage
          persistentVolumeClaim:
            claimName: postgres-pvc # references the PVC we created above
```

### `k8s/postgres/service.yaml`

```yaml
apiVersion: v1
kind: Service
metadata:
  name: postgres-service
  namespace: fastcommerce
spec:
  # ClusterIP: only reachable from inside the cluster.
  # Correct for Postgres — it should never be accessible from outside.
  type: ClusterIP
  selector:
    # Routes traffic to any pod with label app=postgres.
    # Matches the labels in the Deployment's pod template above.
    app: postgres
  ports:
    - port: 5432 # port the Service listens on (used by clients)
      targetPort: 5432 # port on the pod to forward traffic to
```

```bash
kubectl apply -f k8s/postgres/
kubectl get pods -n fastcommerce
# NAME                        READY   STATUS    RESTARTS
# postgres-<hash>             1/1     Running   0

kubectl get service -n fastcommerce
# NAME               TYPE        CLUSTER-IP     PORT(S)
# postgres-service   ClusterIP   10.96.x.x      5432/TCP
```

### `k8s/redis/deployment.yaml`

```yaml
apiVersion: apps/v1
kind: Deployment
metadata:
  name: redis
  namespace: fastcommerce
spec:
  replicas: 1
  selector:
    matchLabels:
      app: redis
  template:
    metadata:
      labels:
        app: redis
    spec:
      containers:
        - name: redis
          image: redis:7-alpine
          ports:
            - containerPort: 6379
          resources:
            requests:
              memory: "64Mi"
              cpu: "100m"
            limits:
              memory: "128Mi"
              cpu: "200m"
          livenessProbe:
            exec:
              command: ["redis-cli", "ping"]
            initialDelaySeconds: 10
            periodSeconds: 10
```

### `k8s/redis/service.yaml`

```yaml
apiVersion: v1
kind: Service
metadata:
  name: redis-service
  namespace: fastcommerce
spec:
  type: ClusterIP
  selector:
    app: redis
  ports:
    - port: 6379
      targetPort: 6379
```

```bash
kubectl apply -f k8s/redis/
kubectl get pods -n fastcommerce
```

### `k8s/api/deployment.yaml`

This is the most important manifest. Read the comments carefully.

```yaml
apiVersion: apps/v1
kind: Deployment
metadata:
  name: fastcommerce-api
  namespace: fastcommerce
  annotations:
    # Annotations are like labels but for non-identifying metadata.
    # This one is just documentation — record why this is the replica count.
    deployment.kubernetes.io/reason:
      "3 replicas for HA; matches partition count if Kafka is added"
spec:
  replicas: 3
  selector:
    matchLabels:
      app: fastcommerce-api
  strategy:
    type: RollingUpdate
    rollingUpdate:
      # maxSurge: how many extra pods above the desired count are allowed
      # during an update. 1 means we'll temporarily have 4 pods (3 + 1).
      # Setting this higher speeds up the rollout but uses more resources.
      maxSurge: 1
      # maxUnavailable: how many pods can be unavailable during an update.
      # 0 means: never take a pod down before a new one is ready.
      # This guarantees zero downtime at the cost of needing extra capacity
      # (the surge pod) during the rollout.
      maxUnavailable: 0
  template:
    metadata:
      labels:
        app: fastcommerce-api
    spec:
      # initContainers run to completion BEFORE the main containers start.
      # This solves the Django migration problem: we need the database
      # schema to be up to date before the API starts serving requests.
      # Without this, pods might start and fail on missing tables.
      initContainers:
        - name: run-migrations
          image: fastcommerce-api:v1
          imagePullPolicy: Never
          command: ["python", "manage.py", "migrate", "--noinput"]
          envFrom:
            - configMapRef:
                name: fastcommerce-config
            - secretRef:
                name: fastcommerce-secrets
      containers:
        - name: api
          image: fastcommerce-api:v1
          # Never: use the image already present in the node's image cache.
          # Always: always pull from registry (default for :latest tag).
          # IfNotPresent: pull only if not in cache (default for versioned tags).
          # We use Never because our image is built locally into minikube.
          imagePullPolicy: Never
          ports:
            - containerPort: 8000
          # envFrom injects ALL keys from the ConfigMap and Secret as env vars.
          # More concise than listing each key individually when you need all of them.
          envFrom:
            - configMapRef:
                name: fastcommerce-config
            - secretRef:
                name: fastcommerce-secrets
          resources:
            requests:
              memory: "256Mi"
              cpu: "250m"
            limits:
              memory: "512Mi"
              cpu: "500m"

          livenessProbe:
            # Liveness: is the container stuck? Restart it if so.
            # Use a lightweight endpoint that doesn't hit the database —
            # a DB outage should not cause pod restarts (that won't fix it
            # and will cause a thundering herd when DB recovers).
            httpGet:
              path: /api/health/
              port: 8000
            initialDelaySeconds: 30
            periodSeconds: 15
            failureThreshold: 3

          readinessProbe:
            # Readiness: is the container ready to serve traffic?
            # Pods that fail readiness are removed from the Service's
            # endpoint list — traffic stops reaching them.
            # This is what enables zero-downtime deploys and self-healing.
            # Can be more thorough than liveness (hit the DB, check cache).
            httpGet:
              path: /api/health/
              port: 8000
            initialDelaySeconds: 10
            periodSeconds: 5
            failureThreshold: 2
            # successThreshold: how many consecutive successes before a
            # previously-failing pod is marked ready again. Default is 1.
            successThreshold: 1

          # lifecycle hooks run at specific points in the container lifecycle.
          preStop:
            # preStop runs BEFORE the container receives SIGTERM.
            # This sleep gives the load balancer time to stop sending new
            # requests to this pod before Gunicorn starts shutting down.
            # Without this, in-flight requests may be dropped as the pod
            # is removed from the Service endpoints.
            exec:
              command: ["sleep", "5"]

      # terminationGracePeriodSeconds: how long Kubernetes waits after
      # sending SIGTERM before sending SIGKILL.
      # Must be longer than your preStop sleep + your app's shutdown time.
      # 30s is the default. We use 60s to be safe with slow requests.
      terminationGracePeriodSeconds: 60
```

### `k8s/api/service.yaml`

```yaml
apiVersion: v1
kind: Service
metadata:
  name: api-service
  namespace: fastcommerce
spec:
  # LoadBalancer: provisions an external load balancer.
  # On cloud providers, this creates an actual cloud load balancer.
  # On minikube with tunnel running, this assigns 127.0.0.1 as EXTERNAL-IP.
  type: LoadBalancer
  selector:
    app: fastcommerce-api
  ports:
    - port: 80 # external port (what Locust and curl talk to)
      targetPort: 8000 # pod port (what Gunicorn listens on)
```

### `k8s/ingress.yaml`

An alternative to the LoadBalancer Service — and what you'd use in production to
serve multiple services under one IP with path-based routing.

```yaml
apiVersion: networking.k8s.io/v1
kind: Ingress
metadata:
  name: fastcommerce-ingress
  namespace: fastcommerce
  annotations:
    # Tell the nginx ingress controller to use these settings.
    # These annotations are nginx-specific — other controllers use different ones.
    nginx.ingress.kubernetes.io/rewrite-target: /
    # How long to wait for a response from the upstream pod.
    # Increase for long-running requests (reports, exports).
    nginx.ingress.kubernetes.io/proxy-read-timeout: "60"
spec:
  ingressClassName: nginx # use the nginx ingress controller
  rules:
    - http:
        paths:
          - path: /api
            pathType: Prefix
            backend:
              service:
                name: api-service
                port:
                  number: 80
          - path: /admin
            pathType: Prefix
            backend:
              service:
                name: api-service
                port:
                  number: 80
```

### Apply everything and verify

```bash
# Apply in order — dependencies first
kubectl apply -f k8s/namespace.yaml
kubectl apply -f k8s/configmap.yaml
kubectl apply -f k8s/secret.yaml
kubectl apply -f k8s/postgres/
kubectl apply -f k8s/redis/
kubectl apply -f k8s/api/
kubectl apply -f k8s/ingress.yaml

# Watch everything come up
kubectl get pods -n fastcommerce --watch
# NAME                                READY   STATUS            RESTARTS
# postgres-<hash>                     1/1     Running           0
# redis-<hash>                        1/1     Running           0
# fastcommerce-api-<hash>             0/1     Init:0/1          0   ← migration running
# fastcommerce-api-<hash>             0/1     PodInitializing   0   ← migration done
# fastcommerce-api-<hash>             1/1     Running           0   ← ready
# fastcommerce-api-<hash>             1/1     Running           0
# fastcommerce-api-<hash>             1/1     Running           0
```

The `Init:0/1` phase is the migration init container running. Only after it
exits successfully do the main containers start.

```bash
# Seed the database
kubectl exec -it \
  $(kubectl get pod -n fastcommerce -l app=fastcommerce-api -o jsonpath='{.items[0].metadata.name}') \
  -n fastcommerce -- python manage.py seed_data

# Get the external IP (requires minikube tunnel running)
kubectl get service api-service -n fastcommerce
# NAME          TYPE           CLUSTER-IP     EXTERNAL-IP   PORT(S)
# api-service   LoadBalancer   10.96.x.x      127.0.0.1     80:xxxxx/TCP

# Hit the API
curl http://localhost/api/health/
# {"status": "ok", "service": "fastcommerce-api"}

curl http://localhost/api/products/
# [...list of products...]

# Place an order
curl -X POST http://localhost/api/orders/ \
  -H "Content-Type: application/json" \
  -d '{"customer_id": 1, "items": [{"product_id": 1, "quantity": 1}]}'
```

### The minikube dashboard

minikube ships with a web dashboard that gives a visual overview of everything
running in the cluster:

```bash
minikube dashboard
```

This opens a browser tab showing pods, deployments, services, and resource
usage. Useful for getting a feel for what's running before diving into `kubectl`
commands.

### Useful shortcuts for the rest of this module

```bash
# Set the default namespace so you don't have to type -n fastcommerce every time
kubectl config set-context --current --namespace=fastcommerce

# Now these work without -n fastcommerce:
kubectl get pods
kubectl logs <pod-name>
kubectl exec -it <pod-name> -- bash

# Alias for the API pod name (changes every deploy — this re-evaluates each time)
alias apipod='kubectl get pod -l app=fastcommerce-api -o jsonpath="{.items[0].metadata.name}"'
kubectl exec -it $(apipod) -- bash
```

---

## 5. Production Patterns

### Pattern 1 — Rolling Update Strategy Tuning

**The problem it solves:** The default rolling update settings (`maxSurge: 25%`,
`maxUnavailable: 25%`) are a compromise that works for most cases but is wrong
for both ends of the spectrum. A payment service that can never have fewer than
N healthy instances needs `maxUnavailable: 0`. A batch processing service with
no traffic during deploys can use `maxUnavailable: 100%` to replace all pods
simultaneously and deploy faster. The defaults assume you haven't thought about
this.

**Understanding the knobs:**

```
Desired replicas: 3

maxSurge: 1, maxUnavailable: 0  (zero-downtime, slower)
─────────────────────────────────────────────────────
Step 1: Start 1 new pod         → 3 old + 1 new = 4 running
Step 2: New pod becomes ready   → terminate 1 old
Step 3: Repeat                  → 2 old + 2 new
Step 4: Repeat                  → 1 old + 3 new
Step 5: Done                    → 0 old + 3 new
At no point are fewer than 3 pods serving traffic.
Cost: needs capacity for 4 pods during rollout.

maxSurge: 0, maxUnavailable: 1  (resource-constrained, faster)
─────────────────────────────────────────────────────────────
Step 1: Terminate 1 old pod     → 2 old running (briefly reduced capacity)
Step 2: Start 1 new pod         → 2 old + 1 new
Step 3: New pod becomes ready   → terminate 1 old
Step 4: Repeat until done
Minimum pods serving traffic: 2 (one below desired).
Cost: brief capacity reduction, no extra resources needed.

maxSurge: 3, maxUnavailable: 3  (fastest, only for non-critical)
─────────────────────────────────────────────────────────────────
Replace all pods simultaneously.
Cost: brief full outage during rollout.
```

**The implementation — for FastCommerce's order API:**

```yaml
# k8s/api/deployment.yaml — strategy section
strategy:
  type: RollingUpdate
  rollingUpdate:
    maxSurge: 1
    maxUnavailable: 0
```

This is the right setting for a customer-facing API. You're saying: never reduce
capacity below 3, and use one extra pod slot to bring in the new version before
retiring the old.

**Verify the rollout behaviour:**

```bash
# Watch the rollout step by step
kubectl rollout status deployment/fastcommerce-api -n fastcommerce

# See the history of rollouts (last 10 by default)
kubectl rollout history deployment/fastcommerce-api -n fastcommerce

# Annotate the rollout reason — shows up in history
kubectl annotate deployment fastcommerce-api \
  kubernetes.io/change-cause="v2: add order confirmation endpoint" \
  -n fastcommerce

# Roll back to the previous version
kubectl rollout undo deployment/fastcommerce-api -n fastcommerce

# Roll back to a specific revision
kubectl rollout undo deployment/fastcommerce-api \
  --to-revision=2 -n fastcommerce
```

**The tradeoff:** `maxUnavailable: 0` requires capacity for one extra pod during
rollouts. On a resource-constrained cluster, the new pod may not be schedulable
if all nodes are at capacity — the rollout stalls with the new pod in `Pending`
state and the old pods still serving traffic. This is preferable to a broken
deploy, but you should monitor for stalled rollouts.

### Pattern 2 — Pod Disruption Budgets

**The problem it solves:** Kubernetes performs voluntary disruptions — events
where pods are intentionally removed from a node. These include node drains (for
maintenance, upgrades, or decommissioning) and cluster autoscaler scale-downs.
Without a PodDisruptionBudget (PDB), Kubernetes might drain a node and evict all
3 API pods simultaneously, causing a complete outage.

A PDB tells Kubernetes: "when performing voluntary disruptions, ensure at least
N pods of this type are always available."

**The implementation:**

```yaml
# k8s/api/pdb.yaml
apiVersion: policy/v1
kind: PodDisruptionBudget
metadata:
  name: fastcommerce-api-pdb
  namespace: fastcommerce
spec:
  # minAvailable: the minimum number of pods that must be available
  # during a voluntary disruption.
  # Can be an absolute number (2) or a percentage ("66%").
  # With 3 replicas, minAvailable: 2 means at most 1 pod can be
  # evicted at a time.
  minAvailable: 2
  selector:
    matchLabels:
      app: fastcommerce-api

  # Alternative: maxUnavailable instead of minAvailable
  # maxUnavailable: 1  ← at most 1 pod can be down at any time
  # Use minAvailable when you care about absolute availability.
  # Use maxUnavailable when you care about the disruption rate.
```

```bash
kubectl apply -f k8s/api/pdb.yaml

# Verify
kubectl get pdb -n fastcommerce
# NAME                    MIN AVAILABLE   MAX UNAVAILABLE   ALLOWED DISRUPTIONS
# fastcommerce-api-pdb    2               N/A               1

# Test it — drain the minikube node (the only node we have)
kubectl drain minikube --ignore-daemonsets --delete-emptydir-data

# You'll see:
# evicting pod fastcommerce/fastcommerce-api-<hash>
# error when evicting pods/"fastcommerce-api-<hash>" ...
# Cannot evict pod as it would violate the pod's disruption budget.
# (Only 2 pods, PDB requires 2 — can't evict any more)

# Uncordon to bring the node back
kubectl uncordon minikube
```

**The tradeoff:** PDBs only constrain voluntary disruptions. An involuntary
disruption — a node failure, a kernel panic, a VM being terminated — bypasses
the PDB entirely. For protection against involuntary disruptions, you need pods
spread across multiple nodes (via `topologySpreadConstraints` or pod
anti-affinity rules), which is only meaningful on a multi-node cluster.

Also: a PDB with `minAvailable` equal to your replica count (`minAvailable: 3`
on a 3-replica deployment) will prevent ALL voluntary disruptions — including
node drains and cluster upgrades. This is a common misconfiguration that blocks
cluster maintenance indefinitely.

### Pattern 3 — Resource Requests and Limits

**The problem it solves:** Without resource declarations, the Kubernetes
scheduler has no information to make placement decisions. It may put all 3 API
pods on the same node, or schedule a memory-hungry analytics job next to your
latency-sensitive order API. Resource requests are how you communicate resource
needs to the scheduler. Resource limits are how you prevent one workload from
starving others.

**Requests vs limits — the distinction matters:**

```
Resource Requests:
- Used by the SCHEDULER to decide pod placement
- The scheduler only places a pod on a node if the node has enough
  unreserved capacity to satisfy the request
- Kubernetes GUARANTEES the pod gets at least this much
- Does NOT mean the pod actually uses this much

Resource Limits:
- Enforced at RUNTIME by the kubelet via cgroups
- Memory limit: exceeding it triggers OOMKill (pod is restarted)
- CPU limit: exceeding it causes throttling (not killed, just slowed)
- A pod using less than its limit is fine — limits are a ceiling, not a floor
```

**The noisy neighbour problem without resource declarations:**

```
Node (4 CPU, 8GB RAM) — no requests/limits configured

Pod A (API):      actually uses 1 CPU, 512MB   ← no declared needs
Pod B (API):      actually uses 1 CPU, 512MB   ← no declared needs
Pod C (Analytics): kicks off at 2am and uses 3.5 CPU, 6GB RAM

Result: Pod A and Pod B get starved. API latency spikes at 2am.
Nobody knows why until someone looks at node-level metrics.
```

**The same scenario with resource declarations:**

```
Node (4 CPU, 8GB RAM)

Pod A (API):      requests 0.25 CPU, 256MB  limits 0.5 CPU, 512MB
Pod B (API):      requests 0.25 CPU, 256MB  limits 0.5 CPU, 512MB
Pod C (Analytics): requests 2 CPU, 4GB      limits 3 CPU, 6GB

Scheduler sees: 2.5 CPU requested out of 4 available. Places Pod C.
At runtime: Pod C is limited to 3 CPU, 6GB. Cannot exceed these.
Pod A and Pod B are guaranteed their 0.25 CPU and 256MB.
```

**Setting the right values:**

Don't guess. Measure. For the FastCommerce API:

```bash
# With the stack running under moderate Locust load, check actual usage
kubectl top pods -n fastcommerce

# NAME                            CPU(cores)   MEMORY(bytes)
# fastcommerce-api-<hash>         85m          180Mi
# fastcommerce-api-<hash>         92m          176Mi
# fastcommerce-api-<hash>         78m          182Mi
# postgres-<hash>                 12m          45Mi
# redis-<hash>                    4m           8Mi
```

Rule of thumb: set requests at ~80% of observed average usage. Set limits at
~150–200% of observed peak usage. This gives headroom for traffic spikes while
preventing runaway processes.

```yaml
# k8s/api/deployment.yaml — tuned based on observed usage
resources:
  requests:
    memory: "200Mi" # ~80% of observed 180Mi average
    cpu: "100m" # ~80% of observed 85m average
  limits:
    memory: "512Mi" # headroom for traffic spikes
    cpu: "500m" # throttle before starving other pods
```

**Three resource quality classes — know which one your pod is:**

```
Guaranteed: requests == limits for ALL containers in the pod
  → Highest priority. Last to be evicted under node pressure.
  → Use for latency-sensitive services.

Burstable: requests < limits (or only one is set)
  → Middle priority. Evicted after BestEffort under pressure.
  → Most production workloads.

BestEffort: no requests or limits set
  → Lowest priority. First to be evicted.
  → Never use for production workloads.
```

For the FastCommerce API (customer-facing, latency-sensitive), setting
`requests == limits` makes it Guaranteed — it will not be evicted under node
memory pressure. For background workers where some latency variance is
acceptable, Burstable is fine.

### Pattern 4 — Horizontal Pod Autoscaler

**The problem it solves:** Flash sales. Overnight lows. Traffic patterns you
can't predict precisely enough to manually set a static replica count. The
Horizontal Pod Autoscaler (HPA) watches resource metrics and scales the
Deployment up and down automatically.

**The implementation:**

```yaml
# k8s/api/hpa.yaml
apiVersion: autoscaling/v2
kind: HorizontalPodAutoscaler
metadata:
  name: fastcommerce-api-hpa
  namespace: fastcommerce
spec:
  scaleTargetRef:
    apiVersion: apps/v1
    kind: Deployment
    name: fastcommerce-api
  minReplicas: 2 # never scale below 2 — minimum availability
  maxReplicas: 10 # never scale above 10 — cost ceiling
  metrics:
    - type: Resource
      resource:
        name: cpu
        target:
          type: Utilization
          # Scale up when average CPU across all pods exceeds 70% of their request.
          # With request=100m, this triggers at ~70m average.
          # Scale down when average drops below 70% (with a cooldown period).
          averageUtilization: 70
    - type: Resource
      resource:
        name: memory
        target:
          type: Utilization
          averageUtilization: 80
  behavior:
    scaleUp:
      # Scale up aggressively — don't wait when traffic spikes
      stabilizationWindowSeconds: 30 # wait 30s before scaling up again
      policies:
        - type: Pods
          value: 2 # add at most 2 pods per scaling event
          periodSeconds: 30
    scaleDown:
      # Scale down conservatively — avoid flapping
      stabilizationWindowSeconds: 300 # wait 5 minutes before scaling down
      policies:
        - type: Pods
          value: 1 # remove at most 1 pod per scaling event
          periodSeconds: 60
```

```bash
kubectl apply -f k8s/api/hpa.yaml

# Watch the HPA
kubectl get hpa -n fastcommerce --watch
# NAME                    REFERENCE                   TARGETS         MINPODS   MAXPODS   REPLICAS
# fastcommerce-api-hpa    Deployment/fastcommerce-api 12%/70%         2         10        3

# The HPA requires the metrics-server addon — ensure it's enabled
minikube addons enable metrics-server
```

**Trigger the HPA by generating load:**

```bash
# In one terminal — watch HPA and pods
watch -n 5 'kubectl get hpa,pods -n fastcommerce'

# In another terminal — generate load with Locust
locust -f locustfile.py --host=http://localhost \
  --headless --users 100 --spawn-rate 10 --run-time 120s
```

Watch the TARGETS column in the HPA output. When CPU utilization climbs above
70%, the HPA adds pods. When Locust stops and utilization drops, the HPA waits 5
minutes (the scale-down stabilization window) then removes pods.

**The tradeoff:** The HPA scale-up latency is typically 60–90 seconds from
"metric exceeds threshold" to "new pods are ready." A truly sudden spike (Black
Friday, a viral moment) will see elevated latency for that 60–90 second window
while pods are starting. Options for handling this:

- **Pre-scaling:** manually scale up before known events
- **KEDA (Kubernetes Event-Driven Autoscaling):** scale on external metrics like
  Kafka consumer lag (more relevant once we add Kafka back)
- **Predictive scaling:** scale based on historical traffic patterns

### Pattern 5 — Graceful Shutdown

**The problem it solves:** When Kubernetes terminates a pod (during a rolling
update, a scale-down, or a node drain), it sends SIGTERM to the container and
then waits `terminationGracePeriodSeconds` before sending SIGKILL. If your
application doesn't handle SIGTERM properly, in-flight requests are dropped,
connections are closed abruptly, and background jobs are cut off mid-execution.

**The full shutdown sequence in Kubernetes:**

```
1. Pod is marked for deletion
2. Pod is removed from Service endpoints
   → New requests stop being routed to this pod
   → In-flight requests are still being processed
3. preStop hook runs (if configured)
   → In our case: sleep 5s
   → This gap ensures the load balancer has time to propagate the
      endpoint removal before we start shutting down
4. SIGTERM is sent to the main container process
   → Gunicorn should finish in-flight requests and shut down
5. terminationGracePeriodSeconds countdown starts (60s in our config)
6. If the process hasn't exited by the deadline: SIGKILL

The gap between step 2 and step 3 is why preStop matters:
endpoint removal propagation is not instantaneous. Without the sleep,
the load balancer may still route requests to the pod while it's
already starting to shut down.
```

**Verify graceful shutdown is working:**

```bash
# Watch what happens to in-flight requests during a rolling update
# Terminal 1: Locust running against the API
locust -f locustfile.py --host=http://localhost \
  --headless --users 20 --spawn-rate 5

# Terminal 2: Trigger a rolling update
# (update the image tag in deployment.yaml first, then apply)
kubectl set image deployment/fastcommerce-api \
  api=fastcommerce-api:v2 -n fastcommerce

# Watch for any failures in the Locust output
# With proper graceful shutdown: 0 failures
# Without preStop hook: you'll see spikes of connection errors
```

**Gunicorn's role:** Gunicorn handles SIGTERM by finishing the current requests
on each worker and then exiting cleanly. The default worker timeout (30s) means
any request taking longer than 30s will be forcibly terminated even with
graceful shutdown. For long-running requests (file exports, report generation),
increase the timeout or use async workers.

```dockerfile
# src/django-api/Dockerfile — Gunicorn with appropriate timeout
CMD ["gunicorn", "fastcommerce.wsgi:application",
     "--bind", "0.0.0.0:8000",
     "--workers", "2",
     "--timeout", "60",       # worker timeout — must be < terminationGracePeriodSeconds
     "--graceful-timeout", "30"]  # time to finish in-flight requests on shutdown
```

---

## 6. Breaking It — Failure Modes and Observability

### Diagnostic toolkit — know these before you need them

```bash
# The most useful command for diagnosing stuck or failing pods
kubectl describe pod <pod-name> -n fastcommerce
# Look at: Events section at the bottom — this is where failures are explained

# Logs from the current container
kubectl logs <pod-name> -n fastcommerce

# Logs from the PREVIOUS container (crucial when a pod is crash-looping)
kubectl logs <pod-name> -n fastcommerce --previous

# Resource usage — requires metrics-server
kubectl top pods -n fastcommerce
kubectl top nodes

# All events in the namespace, sorted by time
kubectl get events -n fastcommerce --sort-by='.lastTimestamp'

# Shell into a running pod
kubectl exec -it <pod-name> -n fastcommerce -- bash

# Run a temporary debug pod with network tools
kubectl run debug --rm -it --image=nicolaka/netshoot \
  -n fastcommerce -- bash
# Inside: curl api-service, nslookup postgres-service, tcpdump, etc.
```

### Failure 1 — Kill a pod mid-request

This tests the self-healing property and measures the recovery time under load.

```bash
# Terminal 1: Locust running
locust -f locustfile.py --host=http://localhost \
  --headless --users 20 --spawn-rate 5

# Terminal 2: Watch pods
kubectl get pods -n fastcommerce --watch

# Terminal 3: Kill a pod
kubectl delete pod \
  $(kubectl get pod -n fastcommerce -l app=fastcommerce-api \
    -o jsonpath='{.items[0].metadata.name}') \
  -n fastcommerce
```

**What you'll observe:**

In Terminal 2, the deleted pod immediately shows `Terminating`. Within 5–10
seconds a new pod appears in `ContainerCreating`, then `Running`, then
`1/1 Ready`. The Deployment's ReplicaSet detects the pod count dropped to 2
(below desired 3) and creates a new one.

In Terminal 1 (Locust), you should see zero failures — the deleted pod's
in-flight requests complete (graceful shutdown), the Service stops routing to it
as soon as it's terminated, and the remaining 2 pods absorb the traffic while
the replacement comes up.

The recovery time from pod death to new pod ready is typically 20–40 seconds
(image pull skipped since we use `imagePullPolicy: Never`; dominated by Django
startup time). During this window, 2 pods serve traffic instead of 3 — capacity
reduced but service available.

```bash
# How long did it actually take?
kubectl describe pod <new-pod-name> -n fastcommerce | grep -A5 "Events:"
# Events:
#   Type    Reason     Age   Message
#   Normal  Scheduled  45s   Successfully assigned to node minikube
#   Normal  Pulled     44s   Container image "fastcommerce-api:v1" already present
#   Normal  Created    44s   Created container run-migrations
#   Normal  Started    44s   Started container run-migrations
#   Normal  Created    41s   Created container api
#   Normal  Started    41s   Started container api
```

### Failure 2 — Deploy a bad image

This tests the rollout protection mechanism — the guarantee that a broken deploy
doesn't take down the service.

```bash
# Build a "broken" image — the API crashes on startup
# (simulate by setting a bad environment variable)
eval $(minikube docker-env)
docker build -t fastcommerce-api:broken ./src/django-api/

# Deploy the broken image
kubectl set image deployment/fastcommerce-api \
  api=fastcommerce-api:broken -n fastcommerce

# Watch the rollout
kubectl rollout status deployment/fastcommerce-api -n fastcommerce
# Waiting for deployment "fastcommerce-api" rollout to finish:
# 1 out of 3 new replicas have been updated...
# (it stalls here — the new pod never becomes ready)

# Look at what's happening
kubectl get pods -n fastcommerce
# NAME                                READY   STATUS             RESTARTS
# fastcommerce-api-<old-hash>-xxx     1/1     Running            0   ← still serving
# fastcommerce-api-<old-hash>-yyy     1/1     Running            0   ← still serving
# fastcommerce-api-<old-hash>-zzz     1/1     Running            0   ← still serving
# fastcommerce-api-<new-hash>-abc     0/1     CrashLoopBackOff   3   ← broken, not serving
```

The critical observation: **the three old pods are still running and serving
traffic.** The broken pod failed its readiness probe, was never added to the
Service endpoints, and the rollout stalled. The cluster held the last known good
state.

```bash
# Describe the broken pod to see why it's failing
kubectl describe pod fastcommerce-api-<new-hash>-abc -n fastcommerce
# Events:
#   Warning  BackOff    2m    Back-off restarting failed container

kubectl logs fastcommerce-api-<new-hash>-abc -n fastcommerce --previous
# (shows the startup error)

# Roll back to the working version
kubectl rollout undo deployment/fastcommerce-api -n fastcommerce
# deployment.apps/fastcommerce-api rolled back

kubectl rollout status deployment/fastcommerce-api -n fastcommerce
# deployment "fastcommerce-api" successfully rolled out

kubectl get pods -n fastcommerce
# All 3 pods running the previous image, READY 1/1
```

**CrashLoopBackOff explained:** when a container exits with a non-zero code,
Kubernetes restarts it immediately. If it keeps crashing, Kubernetes applies
exponential backoff (10s, 20s, 40s, 80s...) to avoid hammering the node.
`CrashLoopBackOff` means "this container keeps crashing and I'm backing off
before retrying." Check `kubectl logs --previous` to see what happened before
the last crash.

### Failure 3 — OOMKill

Exhaust a pod's memory limit and observe the automatic recovery.

```bash
# Lower the API memory limit temporarily to make this easy to trigger
kubectl set resources deployment/fastcommerce-api \
  --limits=memory=100Mi -n fastcommerce

# Generate load to push memory usage above 100Mi
locust -f locustfile.py --host=http://localhost \
  --headless --users 50 --spawn-rate 10 --run-time 60s &

# Watch pods — you'll see OOMKilled
kubectl get pods -n fastcommerce --watch
# NAME                            READY   STATUS      RESTARTS
# fastcommerce-api-<hash>         0/1     OOMKilled   0
# fastcommerce-api-<hash>         1/1     Running     1   ← restarted

# The RESTARTS column increments each time
```

```bash
# Confirm it was OOMKill (not a regular crash)
kubectl describe pod <pod-name> -n fastcommerce | grep -A3 "Last State:"
# Last State:   Terminated
#   Reason:     OOMKilled
#   Exit Code:  137
#   Started:    ...
#   Finished:   ...
```

Exit code 137 = 128 + 9 (SIGKILL). OOMKill is always exit code 137.

```bash
# Restore the memory limit
kubectl set resources deployment/fastcommerce-api \
  --limits=memory=512Mi -n fastcommerce
```

**The tradeoff to understand:** OOMKill is an abrupt termination — no graceful
shutdown, no SIGTERM, just SIGKILL. In-flight requests are dropped. This is why
setting limits correctly (with enough headroom for traffic spikes) matters. An
OOMKilled pod under load creates a negative feedback loop: the remaining pods
receive more traffic, they use more memory, they get OOMKilled too.

### Failure 4 — Readiness probe failure

This tests that a pod failing its readiness check is automatically removed from
the Service without killing the pod.

```bash
# Exec into a running API pod and make the health endpoint return 500
# (in a real scenario, this might be a downstream dependency failing)
kubectl exec -it \
  $(kubectl get pod -n fastcommerce -l app=fastcommerce-api \
    -o jsonpath='{.items[0].metadata.name}') \
  -n fastcommerce -- bash

# Inside the pod — create a flag file that makes the health endpoint fail
# (your /api/health/ view would need to check for this in a real test;
# alternatively, stop the gunicorn process: kill 1)
kill 1   # kills the main gunicorn process, triggers restart
exit
```

A cleaner way to test readiness failure: temporarily change the probe path to
something that returns 404:

```bash
kubectl patch deployment fastcommerce-api -n fastcommerce \
  --type='json' \
  -p='[{"op": "replace", "path": "/spec/template/spec/containers/0/readinessProbe/httpGet/path", "value": "/api/nonexistent/"}]'

# Watch — pods fail readiness, READY column shows 0/1
kubectl get pods -n fastcommerce --watch
# NAME                            READY   STATUS    RESTARTS
# fastcommerce-api-<hash>         0/1     Running   0   ← running but not ready
# fastcommerce-api-<hash>         0/1     Running   0
# fastcommerce-api-<hash>         0/1     Running   0

# The Service still exists but has no endpoints — all traffic fails
kubectl get endpoints api-service -n fastcommerce
# NAME          ENDPOINTS   AGE
# api-service   <none>      10m

# Restore the correct probe path
kubectl patch deployment fastcommerce-api -n fastcommerce \
  --type='json' \
  -p='[{"op": "replace", "path": "/spec/template/spec/containers/0/readinessProbe/httpGet/path", "value": "/api/health/"}]'

# Pods recover, endpoints repopulate, traffic resumes
kubectl get endpoints api-service -n fastcommerce
# NAME          ENDPOINTS                                             AGE
# api-service   172.17.0.4:8000,172.17.0.5:8000,172.17.0.6:8000     10m
```

The key observation: liveness probe failure restarts the container. Readiness
probe failure removes the pod from the Service without restarting it. This is
the right behaviour when the issue is a bad deploy (restart won't help) vs a
transient dependency failure (the pod should recover on its own).

### Reading the signals

```
# Pod is starting normally
Events:
  Normal  Scheduled   pod assigned to node minikube
  Normal  Pulled      image already present on node
  Normal  Created     container created
  Normal  Started     container started

# Pod is crash-looping — look at logs --previous for the cause
Status: CrashLoopBackOff
Events:
  Warning BackOff     back-off restarting failed container

# Pod is stuck pending — scheduler can't place it
Status: Pending
Events:
  Warning FailedScheduling  0/1 nodes are available: 1 Insufficient memory.

# Pod was OOMKilled
Last State: Terminated, Reason: OOMKilled, Exit Code: 137

# Image can't be pulled (wrong tag, wrong registry, no imagePullSecret)
Events:
  Warning Failed      Failed to pull image: rpc error: ... not found

# Init container failed — main containers never start
Status: Init:Error
Events:
  Warning BackOff     back-off restarting failed init container
```

---

## 7. Load Testing with Locust

### The scenario

We run three load tests in sequence:

1. **Baseline** — 3 replicas, moderate load, establish normal numbers
2. **Zero-downtime deploy** — Locust running, trigger a rolling update, measure
   failures (goal: zero)
3. **Scale under live traffic** — increase replicas while Locust runs, measure
   throughput improvement

### `locustfile.py`

```python
import random
from locust import HttpUser, task, between

PRODUCT_IDS = list(range(1, 9))
CUSTOMER_IDS = list(range(1, 6))


class FastCommerceUser(HttpUser):
    wait_time = between(0.5, 2)

    @task(3)
    def place_order(self):
        with self.client.post(
            '/api/orders/',
            json={
                'customer_id': random.choice(CUSTOMER_IDS),
                'items': [{
                    'product_id': random.choice(PRODUCT_IDS),
                    'quantity': random.randint(1, 2)
                }]
            },
            catch_response=True,
            name='POST /api/orders/'
        ) as response:
            if response.status_code in (201, 400):
                response.success()
            else:
                response.failure(f'Unexpected {response.status_code}: {response.text[:100]}')

    @task(2)
    def list_products(self):
        self.client.get('/api/products/', name='GET /api/products/')

    @task(1)
    def get_order(self):
        order_id = random.randint(1, 20)
        with self.client.get(
            f'/api/orders/{order_id}/',
            catch_response=True,
            name='GET /api/orders/:id/'
        ) as response:
            if response.status_code in (200, 404):
                response.success()
            else:
                response.failure(f'Unexpected {response.status_code}')
```

### Test 1 — Baseline

```bash
# Ensure 3 replicas and fresh seed data
kubectl scale deployment fastcommerce-api --replicas=3 -n fastcommerce
kubectl exec -it $(kubectl get pod -n fastcommerce -l app=fastcommerce-api \
  -o jsonpath='{.items[0].metadata.name}') \
  -n fastcommerce -- python manage.py seed_data --clear

# Run baseline — 20 users, 2 minutes
locust -f locustfile.py --host=http://localhost \
  --headless --users 20 --spawn-rate 4 --run-time 120s \
  --html reports/baseline.html
```

Expected on M-series Mac with 3 replicas:

- Throughput: 40–70 req/s
- p50 latency: 50–100ms
- p99 latency: 300–600ms
- Failures: 0% (excluding 400s from stock exhaustion)

Note the p99. This is the number to watch during the rolling update — if
graceful shutdown is working correctly, p99 should not spike significantly
during the deploy.

### Test 2 — Zero-downtime rolling update under live traffic

This is the core test for this module. The goal: deploy a new version while
Locust is running and observe zero HTTP failures.

**Step 1: Prepare a v2 image**

```bash
eval $(minikube docker-env)

# Make a trivial change to the API so there's actually a different image
# (e.g., add a version field to the health endpoint response)
# Then build v2
docker build -t fastcommerce-api:v2 ./src/django-api/
```

**Step 2: Start Locust and watch the rollout simultaneously**

```bash
# Terminal 1 — Locust
locust -f locustfile.py --host=http://localhost \
  --headless --users 30 --spawn-rate 5 --run-time 180s \
  --html reports/rolling-update.html &

# Terminal 2 — watch pods during rollout
watch -n 2 'kubectl get pods -n fastcommerce'

# Terminal 3 — trigger the rolling update after 30s of baseline
sleep 30
kubectl set image deployment/fastcommerce-api \
  api=fastcommerce-api:v2 -n fastcommerce

# Watch the rollout
kubectl rollout status deployment/fastcommerce-api -n fastcommerce
```

**What you'll see in Terminal 2 during the update:**

```
NAME                                READY   STATUS        RESTARTS
fastcommerce-api-<v1-hash>-1        1/1     Running       0
fastcommerce-api-<v1-hash>-2        1/1     Running       0
fastcommerce-api-<v1-hash>-3        1/1     Terminating   0   ← being replaced
fastcommerce-api-<v2-hash>-1        1/1     Running       0   ← new pod ready
fastcommerce-api-<v2-hash>-2        0/1     Running       0   ← starting up
```

At no point are fewer than 3 pods in `READY` state (because
`maxUnavailable: 0`).

**What you should see in Locust:**

- Throughput: stable throughout
- Failures: 0
- p99 latency: slight bump during rollout (pods handling shutdown, new pods
  warming up JIT caches) but no spike to error territory

If you see failures, the most likely cause is the preStop hook not being long
enough for load balancer propagation. Increase the sleep from 5s to 10s.

**Compare the two reports:**

```bash
# Open both reports and compare
open reports/baseline.html
open reports/rolling-update.html
```

The throughput graph in the rolling-update report should show no dip or flat
line — the system served traffic continuously through the deploy.

### Test 3 — Scale under live traffic

```bash
# Start with 2 replicas to create some load pressure
kubectl scale deployment fastcommerce-api --replicas=2 -n fastcommerce

# Terminal 1 — Locust with higher load
locust -f locustfile.py --host=http://localhost \
  --headless --users 60 --spawn-rate 10 --run-time 180s \
  --html reports/scaling.html &

# Terminal 2 — watch throughput and pods
watch -n 3 'kubectl get pods,hpa -n fastcommerce 2>/dev/null'

# After 60 seconds of running with 2 replicas, scale to 5
sleep 60
kubectl scale deployment fastcommerce-api --replicas=5 -n fastcommerce

# Watch the new pods come up and throughput increase in Locust
```

The Locust throughput graph should show a clear step-up when the new pods become
ready. This demonstrates the linear relationship between replica count and
throughput for a stateless service — the kind of scaling behaviour that makes
Kubernetes worth operating.

### Numbers to capture and reference

After running all three tests, you have concrete numbers for this hardware:

| Scenario              | Replicas | Throughput | p99               | Failures |
| --------------------- | -------- | ---------- | ----------------- | -------- |
| Baseline              | 3        | ~X req/s   | ~Yms              | 0%       |
| During rolling update | 3        | ~X req/s   | ~Yms + small bump | 0%       |
| 2 replicas under load | 2        | ~Z req/s   | higher            | 0%       |
| 5 replicas under load | 5        | ~W req/s   | lower             | 0%       |

Fill in your actual numbers. The ratio between 2-replica and 5-replica
throughput should be close to 2.5x — confirming the linear scaling property.

---

## 8. At 10x Scale — Where This Breaks

### Single-node minikube vs a real cluster

Everything in this module ran on a single node. The scheduler had no real work
to do — there was only one place to put pods. At 10x scale, the interesting
scheduler behaviours emerge:

**Pod anti-affinity** becomes critical. Without it, the scheduler might place
all 3 API replicas on the same node. If that node goes down, all 3 replicas go
down together. In production, you add:

```yaml
# k8s/api/deployment.yaml — spec.template.spec section
affinity:
  podAntiAffinity:
    # preferredDuringSchedulingIgnoredDuringExecution: try to spread pods
    # across nodes, but don't fail scheduling if it's not possible.
    # Use requiredDuring... to hard-enforce — but this can cause pods to
    # stay Pending if there aren't enough nodes.
    preferredDuringSchedulingIgnoredDuringExecution:
      - weight: 100
        podAffinityTerm:
          labelSelector:
            matchExpressions:
              - key: app
                operator: In
                values:
                  - fastcommerce-api
          # Spread across different nodes (hostname = unique per node)
          topologyKey: kubernetes.io/hostname
```

**Topology spread constraints** (a more modern alternative):

```yaml
topologySpreadConstraints:
  - maxSkew: 1 # at most 1 pod difference between any two nodes
    topologyKey: kubernetes.io/hostname
    whenUnsatisfiable: DoNotSchedule
    labelSelector:
      matchLabels:
        app: fastcommerce-api
```

### StatefulSets — what Postgres and Kafka actually need

We ran Postgres as a Deployment with a single replica. This works but has a
subtle problem: if the node running Postgres becomes unavailable, Kubernetes
will try to reschedule the Postgres pod on another node. But the PVC
(ReadWriteOnce) can only be mounted by one node at a time. The new pod may get
stuck in `Pending` waiting for the PVC to be released from the old node (which
may take minutes or require manual intervention).

For stateful workloads that need stable network identities and stable storage,
Kubernetes has **StatefulSets**:

```yaml
apiVersion: apps/v1
kind: StatefulSet # not Deployment
metadata:
  name: postgres
spec:
  serviceName: "postgres" # headless service for stable DNS
  replicas: 1
  selector:
    matchLabels:
      app: postgres
  template:
    # ... pod template same as before
  volumeClaimTemplates: # each replica gets its OWN PVC
    - metadata:
        name: postgres-storage
      spec:
        accessModes: ["ReadWriteOnce"]
        resources:
          requests:
            storage: 5Gi
```

StatefulSets give pods stable, predictable names (`postgres-0`, `postgres-1`)
and stable DNS entries (`postgres-0.postgres.fastcommerce.svc.cluster.local`).
They scale up and down in order, and each pod gets its own PVC that is NOT
deleted when the pod is deleted. These properties are what Postgres, Kafka,
Elasticsearch, and other stateful systems need to operate correctly in
Kubernetes.

For production Postgres specifically: the right answer at 10x scale is either a
managed database (AWS RDS, Cloud SQL) or the CloudNative PG operator, which
manages Postgres replication, failover, and backups as a Kubernetes-native
resource. Raw StatefulSets for Postgres require significant operational
expertise to get right.

### Helm — why raw YAML doesn't scale

This module has 10+ YAML files across 4 directories. For one environment. If you
have staging, production, and feature environments, you need three copies of all
these files with slightly different values (different image tags, different
replica counts, different resource limits). Keeping them in sync manually is
error-prone.

**Helm** is the Kubernetes package manager. It templates your manifests with a
values file:

```yaml
# values.yaml — one file per environment
replicaCount: 3
image:
  tag: v2
resources:
  requests:
    memory: "200Mi"
    cpu: "100m"
postgres:
  storageSize: 10Gi
```

```yaml
# templates/deployment.yaml — templated manifest
spec:
  replicas: { { .Values.replicaCount } }
  template:
    spec:
      containers:
        - image: fastcommerce-api:{{ .Values.image.tag }}
          resources:
            requests:
              memory: { { .Values.resources.requests.memory } }
```

```bash
# Deploy to staging
helm install fastcommerce ./chart -f values-staging.yaml

# Deploy to production with a different values file
helm install fastcommerce ./chart -f values-production.yaml

# Upgrade
helm upgrade fastcommerce ./chart -f values-production.yaml --set image.tag=v3

# Rollback
helm rollback fastcommerce 1
```

Helm also manages the full lifecycle — upgrade, rollback, dependency resolution
(your chart can depend on the official Postgres chart). For any system deployed
to more than one environment, Helm (or a similar tool like Kustomize) is not
optional. Raw YAML becomes unmaintainable quickly.

### Cluster Autoscaler — node-level scaling

The HPA scales pods. But if all nodes are full, new pods stay `Pending`
regardless of HPA. The **Cluster Autoscaler** scales nodes:

```
Cluster Autoscaler watches for:
  - Pods stuck in Pending because of insufficient node resources
    → Provision a new node from the cloud provider's API
  - Nodes that have been underutilised for N minutes
    → Drain the node and terminate it to save cost
```

On AWS (EKS), this means the cluster can grow from 3 nodes to 20 nodes during a
flash sale and shrink back to 3 nodes overnight — automatically. The cost scales
with actual usage rather than peak capacity.

The Cluster Autoscaler only works on cloud providers that have node group APIs
(AWS Auto Scaling Groups, GCP Managed Instance Groups, Azure VMSS). It doesn't
work on minikube — you'd need a real multi-node cluster to see it in action.

### Managed Kubernetes — what you stop managing

Running a Kubernetes cluster means:

- Operating the control plane (API server, etcd, scheduler, controller manager)
- Keeping control plane nodes updated and available
- Managing etcd backups
- Upgrading Kubernetes versions across the cluster
- Managing the node OS, security patches, and kubelet versions

Managed Kubernetes (AWS EKS, GCP GKE, Azure AKS) eliminates the control plane
operations. The cloud provider runs the API server, etcd, and controller
manager. You manage worker nodes and workloads. EKS further simplifies node
management with managed node groups — AWS handles OS patching and kubelet
upgrades.

The inflection point: when the engineering time spent operating Kubernetes
infrastructure exceeds the cost of the managed service. For most teams, that
inflection point is below 5 engineers. The practical path for FastCommerce at
10x scale is EKS or GKE with Helm-managed workloads — not self-operated
clusters.

What transfers from this module to managed Kubernetes: everything. Pod specs,
Deployments, Services, Ingress, PVCs, ConfigMaps, Secrets, HPA, PDB — all
identical. The only things that differ are the control plane (managed for you)
and the storage classes (cloud-provider-specific names).

---

## Stitching Parts 1 and 2 into one file

```bash
# In the module-03-kubernetes directory
cat module-03-kubernetes-part1.md \
    <(echo "") \
    <(tail -n +2 module-03-kubernetes-part2.md) \
    > README.md

echo "Combined: $(wc -l < README.md) lines"
```

---

## Quick Reference

```bash
# Cluster info
kubectl cluster-info
kubectl get nodes
minikube status

# Apply manifests
kubectl apply -f k8s/
kubectl apply -f k8s/api/deployment.yaml

# Pod operations
kubectl get pods -n fastcommerce
kubectl get pods -n fastcommerce -o wide        # includes node and IP
kubectl describe pod <name> -n fastcommerce     # events and full config
kubectl logs <name> -n fastcommerce -f          # follow logs
kubectl logs <name> -n fastcommerce --previous  # last crashed container
kubectl exec -it <name> -n fastcommerce -- bash

# Deployments and rollouts
kubectl get deployments -n fastcommerce
kubectl rollout status deployment/fastcommerce-api -n fastcommerce
kubectl rollout history deployment/fastcommerce-api -n fastcommerce
kubectl rollout undo deployment/fastcommerce-api -n fastcommerce
kubectl set image deployment/fastcommerce-api api=fastcommerce-api:v2 -n fastcommerce

# Scaling
kubectl scale deployment fastcommerce-api --replicas=5 -n fastcommerce
kubectl get hpa -n fastcommerce

# Services and endpoints
kubectl get services -n fastcommerce
kubectl get endpoints -n fastcommerce

# Resources
kubectl top pods -n fastcommerce
kubectl top nodes

# Events (sorted by time — first place to look when something is wrong)
kubectl get events -n fastcommerce --sort-by='.lastTimestamp'

# minikube
minikube start --cpus=4 --memory=8192 --driver=docker
minikube tunnel          # run in separate terminal for LoadBalancer IPs
minikube dashboard       # web UI
minikube docker-env      # print env vars to redirect docker to minikube
eval $(minikube docker-env)   # redirect docker commands to minikube
eval $(minikube docker-env --unset)   # restore normal docker context
minikube stop
minikube delete          # destroy the cluster entirely
```

---

## Key Takeaways

**Desired state is the core model.** You declare what you want; Kubernetes
reconciles continuously to make it so and keep it so. Self-healing is the
reconciliation loop detecting drift and correcting it — not magic.

**Pods are ephemeral; Services are stable.** Never talk to a pod IP directly.
Services provide stable ClusterIPs backed by DNS that survive pod restarts and
rescheduling.

**Readiness probes gate traffic; liveness probes gate restarts.** These are
different. A pod failing readiness is removed from the Service — traffic stops
reaching it. A pod failing liveness is restarted. Use both, set them correctly,
and make your health endpoint meaningful.

**`maxUnavailable: 0` is the zero-downtime deploy setting.** Combined with
readiness probes and the preStop hook, it guarantees that old pods serve traffic
until new pods are ready and load balancer propagation has settled.

**PodDisruptionBudgets protect against voluntary disruptions.** Node drains,
cluster upgrades, autoscaler scale-downs — PDBs prevent Kubernetes from evicting
too many pods simultaneously during these events.

**Resource requests are for the scheduler; limits are for runtime.** Set
requests based on observed usage. Set limits with headroom. Pods with no
resources set are BestEffort — first to be evicted under pressure.

**StatefulSets for stateful workloads; Deployments for stateless.** Postgres,
Kafka, Elasticsearch need stable network identities and stable storage per
replica. Deployments give you none of that. In production, use managed databases
and Kafka before reaching for StatefulSets.

**Helm before you have more than one environment.** Raw YAML manifests
duplicated across staging and production diverge immediately. Helm templates
solve this with a single chart and per-environment values files.

**The operational overhead of Kubernetes is real.** For most teams below a
certain scale, a managed PaaS or a well-run single server is the right answer.
The concepts here transfer directly to managed Kubernetes (EKS, GKE, AKS) when
you get there — the control plane operations are just someone else's problem.

---

_Next module: **Module 04 — Apache Spark** — Overnight sales analytics on
millions of FastCommerce order records. Batch processing, distributed
computation, and why SQL alone stops being sufficient at a certain data volume._
