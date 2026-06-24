# Module 01 — Docker & Docker Compose

### Systems at Scale (Local) — E-Commerce Series

> **Goal:** Understand containers from first principles, then containerize a
> Django + Postgres + Redis e-commerce app that runs identically on any machine
> with one command.
>
> **Platform:** macOS Apple Silicon (M1/M2/M3/M4) **Assumed knowledge:** Strong
> Python, Django, comfortable with the terminal **Time estimate:** 8–12 hours
> across 2–3 sessions

---

## Table of Contents

1. [Why Containers Exist — The Problem](#1-why-containers-exist--the-problem)
2. [How Docker Actually Works](#2-how-docker-actually-works)
3. [Core Concepts](#3-core-concepts)
4. [Installing Docker on Apple Silicon](#4-installing-docker-on-apple-silicon)
5. [Docker Fundamentals — Hands-On](#5-docker-fundamentals--hands-on)
6. [Writing a Dockerfile](#6-writing-a-dockerfile)
7. [Docker Compose — Multi-Container Apps](#7-docker-compose--multi-container-apps)
8. [The Project — ShopLocal E-Commerce App](#8-the-project--shoplocal-e-commerce-app)
9. [Project Walkthrough — Step by Step](#9-project-walkthrough--step-by-step)
10. [Common Errors & Fixes](#10-common-errors--fixes)
11. [What You've Learned](#11-what-youve-learned)
12. [Git Repo Structure](#12-git-repo-structure)

---

## 1. Why Containers Exist — The Problem

Before containers, deploying software meant wrestling with the environment.
You'd write code on your Mac, it worked perfectly. You'd push to a Linux server,
it would break — different Python version, missing system library, wrong locale
setting, a package that compiled differently on a different OS.

The classic fix was virtual machines (VMs). A VM emulates an entire computer —
CPU, memory, disk, OS. You ship the whole VM image. It works, but VMs are heavy:
a typical VM is 10–20GB, takes minutes to boot, and runs a full OS kernel even
if your app only needs 50MB.

**Containers solve this differently.** Instead of emulating hardware, containers
share the host OS kernel but isolate the filesystem, processes, and network.
Think of it this way:

- A **VM** is like renting an entire apartment building. You get your own power
  grid, plumbing, structure.
- A **container** is like renting a unit in a building with shared
  infrastructure. You have your own space, your own locks, but the plumbing and
  power grid are shared.

The result: a container starts in milliseconds, uses ~10MB overhead, and is as
isolated as you need for almost every use case.

**Why this matters for you as a Django developer:**

Your Django app depends on: a specific Python version, pip packages from
`requirements.txt`, environment variables for secrets, a running Postgres
server, possibly Redis. Right now, you manage all of that manually on each
machine. Docker packages all of it — your app AND its environment — into a
portable unit.

---

## 2. How Docker Actually Works

### The Linux kernel primitives

Docker is not magic. It uses two Linux kernel features that have existed since
the late 2000s:

**Namespaces** — isolate what a process can see. A containerized process has its
own view of:

- The filesystem (it can't see the host's files)
- The network (it has its own IP, ports)
- Process IDs (PID 1 inside the container is just your app, not the host's init)
- Users (root inside the container is not root on the host)

**cgroups (control groups)** — limit what a process can use:

- CPU: "this container gets at most 0.5 cores"
- Memory: "this container gets at most 512MB RAM"
- Disk I/O

Docker wraps these primitives with a clean CLI and a layered filesystem. That's
fundamentally all it is.

### Apple Silicon note — why this matters

Your M1/M2/M3/M4 Mac uses the ARM64 architecture. Most Docker images on Docker
Hub were historically built for AMD64 (x86_64, Intel/AMD). Docker Desktop on
Apple Silicon runs Linux in a lightweight VM (using Apple's Virtualization
Framework) and supports both ARM64 native images and AMD64 images via emulation
(Rosetta 2).

In practice: **always pull ARM64-native images when they exist** — they're
faster and don't need emulation. Most major images (Python, Postgres, Redis,
Nginx) now publish `linux/arm64` variants. When you see `--platform linux/amd64`
in old tutorials, that's for machines where the native image doesn't exist yet.
You mostly won't need it.

### The layer system

A Docker image is a stack of read-only layers. Each instruction in a
`Dockerfile` creates a new layer on top of the previous one:

```
Layer 4: COPY . /app          ← your source code
Layer 3: RUN pip install ...  ← your dependencies
Layer 2: RUN apt-get install  ← system packages
Layer 1: FROM python:3.12     ← base Python image
Layer 0: (scratch — base OS filesystem)
```

When you rebuild an image after changing your source code, Docker only rebuilds
Layer 4 onwards. Layers 1–3 are cached. This is why **order matters in a
Dockerfile**: put things that change rarely (system packages, pip install)
before things that change often (your source code).

When a container runs, Docker adds a thin **writable layer** on top. The image
layers stay read-only. This is why multiple containers can share the same image
without interfering with each other.

---

## 3. Core Concepts

These are the terms you'll use constantly. Get them clear before touching any
commands.

### Image

A read-only template. Like a class definition in Python — it describes what the
container will look like but doesn't run anything. Images are built from
`Dockerfile`s or pulled from a registry.

```
Image = snapshot of a filesystem + metadata (what command to run, what port to expose)
```

### Container

A running instance of an image. Like an object instantiated from a class. You
can run many containers from the same image simultaneously. Containers are
ephemeral by default — when they stop, any data written inside them is gone
(unless you use volumes).

```
Container = Image + writable layer + running process
```

### Dockerfile

A text file with instructions for building an image. Each instruction is a
layer. You write it once, Docker builds the image from it.

### Registry

A server that stores and distributes images. Docker Hub (`hub.docker.com`) is
the default public registry. You pull images from it and can push your own
images to it. Companies run private registries (AWS ECR, Google Artifact
Registry, etc.).

### Volume

A mechanism for persisting data outside the container's lifecycle. If your
Postgres container stores data in a volume, that data survives even if you
delete and recreate the container.

```
Volume = a named directory managed by Docker, mounted into containers
```

### Network

By default, containers are isolated from each other. Docker creates virtual
networks so containers can communicate. In a Docker Compose setup, all services
on the same Compose file share a default network and can reach each other by
service name (e.g., your Django container can connect to `postgres:5432`).

### Docker Compose

A tool for defining and running multi-container applications. You write a
`docker-compose.yml` describing all your services (Django, Postgres, Redis,
etc.) and Docker Compose starts them all with one command, wires up the network,
and manages volumes.

---

## 4. Installing Docker on Apple Silicon

### Step 1 — Install Docker Desktop

```bash
# Using Homebrew (recommended)
brew install --cask docker
```

Or download directly from: https://docs.docker.com/desktop/install/mac-install/
Choose **Mac with Apple Silicon**.

### Step 2 — Start Docker Desktop

Open Docker Desktop from Applications. Wait for the whale icon in the menu bar
to stop animating — that means the Docker engine is running.

### Step 3 — Verify installation

```bash
docker --version
# Docker version 27.x.x, build ...

docker compose --version
# Docker Compose version v2.x.x

# Run the hello-world container to confirm everything works
docker run hello-world
```

You should see:
`Hello from Docker! This message shows that your installation appears to be working correctly.`

### Step 4 — Configure for Apple Silicon

Open Docker Desktop → Settings → General. Ensure **"Use Virtualization
Framework"** is checked (it should be by default on Apple Silicon). This uses
Apple's native hypervisor rather than the older HyperKit, giving you
significantly better performance.

Optionally under Resources, allocate:

- CPUs: half your cores (e.g., 4 on an M2 with 8 cores)
- Memory: 4–8GB depending on your total RAM

---

## 5. Docker Fundamentals — Hands-On

Work through these commands before touching the project. Understanding them
individually makes Docker Compose less magical.

### 5.1 Pulling and running an image

```bash
# Pull a Python image (ARM64 native on Apple Silicon)
docker pull python:3.12-slim

# List images you have locally
docker images

# Run a container interactively
docker run -it python:3.12-slim bash
```

You're now inside a container. Try:

```bash
python --version   # Python 3.12.x
ls /               # root filesystem of the container
exit               # back to your Mac
```

The `-it` flags mean: `-i` (keep stdin open) + `-t` (allocate a pseudo-TTY).
Together they give you an interactive shell.

### 5.2 Container lifecycle

```bash
# Run a container in the background (detached mode)
docker run -d --name my-python python:3.12-slim sleep infinity

# List running containers
docker ps

# List all containers (including stopped ones)
docker ps -a

# Execute a command in a running container
docker exec -it my-python bash

# Stop a container
docker stop my-python

# Remove a container
docker rm my-python

# Stop and remove in one step
docker rm -f my-python
```

### 5.3 Port mapping

Containers have their own network stack. To reach a port inside a container from
your Mac, you must explicitly map it:

```bash
# Run an Nginx container, map container port 80 to your Mac's port 8080
docker run -d -p 8080:80 --name my-nginx nginx

# Open http://localhost:8080 in your browser — you'll see the Nginx welcome page

docker rm -f my-nginx
```

The format is `-p HOST_PORT:CONTAINER_PORT`.

### 5.4 Volumes

```bash
# Run Postgres with a named volume for data persistence
docker run -d \
  --name my-postgres \
  -e POSTGRES_PASSWORD=secret \
  -e POSTGRES_DB=mydb \
  -v postgres-data:/var/lib/postgresql \
  -p 5432:5432 \
  postgres:18

# List volumes
docker volume ls

# Connect to the database
docker exec -it my-postgres psql -U postgres -d mydb

# Inside psql:
# CREATE TABLE test (id serial PRIMARY KEY, name text);
# INSERT INTO test (name) VALUES ('hello');
# SELECT * FROM test;
# \q

# Stop and REMOVE the container
docker rm -f my-postgres

# Recreate the container with the SAME volume
docker run -d \
  --name my-postgres \
  -e POSTGRES_PASSWORD=secret \
  -e POSTGRES_DB=mydb \
  -v postgres-data:/var/lib/postgresql/data \
  -p 5432:5432 \
  postgres:16

# Your data is still there
docker exec -it my-postgres psql -U postgres -d mydb -c "SELECT * FROM test;"

# Clean up
docker rm -f my-postgres
docker volume rm postgres-data
```

This is why volumes matter: the container is ephemeral, the data is not.

### 5.5 Environment variables

```bash
# Pass environment variables into a container
docker run -d \
  --name my-postgres \
  -e POSTGRES_PASSWORD=secret \
  -e POSTGRES_USER=shopuser \
  -e POSTGRES_DB=shopdb \
  -p 5432:5432 \
  postgres:16

docker rm -f my-postgres
```

In a real app, you'd put these in a `.env` file rather than typing them on the
command line.

### 5.6 Logs and inspection

```bash
docker run -d --name my-postgres -e POSTGRES_PASSWORD=secret postgres:16

# View logs
docker logs my-postgres

# Follow logs (like tail -f)
docker logs -f my-postgres

# Inspect container metadata (IP, volumes, env vars, etc.)
docker inspect my-postgres

# Resource usage
docker stats my-postgres

docker rm -f my-postgres
```

---

## 6. Writing a Dockerfile

Now you understand images and containers. Let's build your own image.

### 6.1 A minimal Python image

Create a test directory:

```bash
mkdir /tmp/docker-test && cd /tmp/docker-test
```

Create `app.py`:

```python
# app.py
print("Hello from inside Docker!")
```

Create `Dockerfile`:

```dockerfile
# Start from the official Python 3.12 slim image (minimal Debian, no extras)
FROM python:3.12-slim

# Set the working directory inside the container
# All subsequent commands run from here
WORKDIR /app

# Copy your application code into the image
COPY app.py .

# The command to run when the container starts
CMD ["python", "app.py"]
```

Build and run:

```bash
# Build the image, tag it as "hello-python"
docker build -t hello-python .

# Run it
docker run hello-python
# Output: Hello from inside Docker!
```

### 6.2 A Django image — with build caching in mind

This is the pattern you'll use for the project. Notice the order carefully:

```dockerfile
FROM python:3.12-slim

# Install system dependencies first (changes rarely → cached early)
RUN apt-get update && apt-get install -y \
    libpq-dev \
    gcc \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app

# Copy ONLY requirements first (changes less often than source code)
# This means "pip install" is cached unless requirements.txt changes
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# Copy source code last (changes most often → rebuild only this layer)
COPY . .

# Collect static files
RUN python manage.py collectstatic --noinput

EXPOSE 8000

CMD ["gunicorn", "shoplocal.wsgi:application", "--bind", "0.0.0.0:8000", "--workers", "2"]
```

**Why this order?** If you `COPY . .` first and then `pip install`, every time
you change a single line of Python code, Docker invalidates the cache and
re-runs `pip install` — even though your requirements didn't change. By
separating the COPY of requirements from the COPY of source, you get fast
rebuilds.

### 6.3 .dockerignore

Like `.gitignore` but for Docker. Prevents unnecessary files from being sent to
the Docker build context (speeds up builds and keeps images lean):

```
# .dockerignore
__pycache__/
*.pyc
*.pyo
.env
.git
.gitignore
*.md
venv/
.venv/
node_modules/
*.log
db.sqlite3
```

---

## 7. Docker Compose — Multi-Container Apps

Your Django app needs Postgres and Redis. You could run three separate
`docker run` commands, manually create a network, link them together. Or you use
Docker Compose.

### 7.1 How Compose works

Docker Compose reads a `docker-compose.yml` file and:

1. Creates a shared network for all services
2. Starts each service as a container
3. Handles service dependencies (e.g., wait for Postgres before starting Django)
4. Maps ports, mounts volumes, injects environment variables

Services on the same Compose network can reach each other by **service name**.
If you have a service called `db`, your Django app connects to `db:5432` — not
`localhost:5432`.

### 7.2 Anatomy of docker-compose.yml

```yaml
version: "3.9" # Compose file format version

services: # Define your containers here
  web: # Service name (also the hostname on the Docker network)
    build: . # Build image from Dockerfile in current directory
    ports:
      - "8000:8000" # HOST:CONTAINER
    environment:
      - DATABASE_URL=postgres://user:pass@db:5432/shopdb
    depends_on:
      - db # Start db before web
    volumes:
      - .:/app # Mount current directory into /app (for development)

  db:
    image: postgres:16 # Use official image, don't build
    environment:
      POSTGRES_USER: user
      POSTGRES_PASSWORD: pass
      POSTGRES_DB: shopdb
    volumes:
      - postgres_data:/var/lib/postgresql/data # Named volume for persistence

  redis:
    image: redis:7-alpine

volumes:
  postgres_data: # Declare named volumes here
```

### 7.3 Essential Compose commands

```bash
# Start all services (build images if needed), in the foreground
docker compose up

# Start all services in the background
docker compose up -d

# Build/rebuild images
docker compose build

# Build and start (useful when you've changed the Dockerfile)
docker compose up --build

# Stop all services (containers remain)
docker compose stop

# Stop and REMOVE containers (volumes are preserved)
docker compose down

# Stop and REMOVE containers AND volumes (nuclear option — wipes database)
docker compose down -v

# View logs for all services
docker compose logs

# Follow logs for a specific service
docker compose logs -f web

# Run a one-off command in a service container
docker compose exec web bash
docker compose exec web python manage.py shell
docker compose exec db psql -U user shopdb

# List running services
docker compose ps

# Scale a service (run multiple instances)
docker compose up --scale web=3
```

---

## 8. The Project — ShopLocal E-Commerce App

You're going to build a Dockerized Django e-commerce backend. By the end,
`docker compose up` will give you:

- A Django REST API for products, orders, and customers
- Postgres as the database
- Redis for caching and session storage
- Nginx as a reverse proxy (the way real production setups work)
- A management command to seed the database with sample data

This exact setup will be extended in every subsequent module (Kafka, Kubernetes,
etc.).

### What you'll build

**API endpoints:**

- `GET /api/products/` — list all products (cached in Redis)
- `POST /api/products/` — create a product
- `GET /api/products/<id>/` — product detail
- `POST /api/orders/` — place an order (deducts stock)
- `GET /api/orders/` — list all orders
- `GET /api/customers/` — list all customers
- `GET /health/` — health check endpoint

**Models:**

- `Customer` — name, email, created_at
- `Product` — name, description, price, stock_quantity
- `Order` — customer, items (M2M through OrderItem), status, total_price
- `OrderItem` — order, product, quantity, unit_price

### Final project structure

```
module-01-docker/
├── docker-compose.yml
├── docker-compose.dev.yml          # Development overrides
├── .env.example
├── .dockerignore
├── nginx/
│   └── nginx.conf
└── shoplocal/
    ├── Dockerfile
    ├── requirements.txt
    ├── manage.py
    ├── shoplocal/
    │   ├── __init__.py
    │   ├── settings/
    │   │   ├── __init__.py
    │   │   ├── base.py
    │   │   └── development.py
    │   ├── urls.py
    │   └── wsgi.py
    └── api/
        ├── __init__.py
        ├── admin.py
        ├── apps.py
        ├── models.py
        ├── serializers.py
        ├── views.py
        ├── urls.py
        ├── cache.py
        └── management/
            └── commands/
                ├── __init__.py
                └── seed_data.py
```

---

## 9. Project Walkthrough — Step by Step

### Step 1 — Create the project directory

```bash
mkdir -p ~/projects/systems-at-scale-local/module-01-docker/shoplocal
cd ~/projects/systems-at-scale-local/module-01-docker
```

### Step 2 — Django app setup

```bash
cd shoplocal

# Create a virtual environment (for local editing with IDE support — Docker won't use this)
python3 -m venv .venv
source .venv/bin/activate

pip install django djangorestframework psycopg2-binary redis django-redis gunicorn
pip freeze > requirements.txt

django-admin startproject shoplocal .
python manage.py startapp api

deactivate
```

### Step 3 — Settings

Create `shoplocal/settings/` as a package:

```bash
mkdir shoplocal/settings
touch shoplocal/settings/__init__.py
mv shoplocal/settings.py shoplocal/settings/base.py
```

**`shoplocal/settings/base.py`** — replace the entire file with:

```python
import os
from pathlib import Path

BASE_DIR = Path(__file__).resolve().parent.parent.parent

SECRET_KEY = os.environ.get('SECRET_KEY', 'dev-secret-key-change-in-production')

DEBUG = os.environ.get('DEBUG', 'False') == 'True'

ALLOWED_HOSTS = os.environ.get('ALLOWED_HOSTS', 'localhost,127.0.0.1').split(',')

INSTALLED_APPS = [
    'django.contrib.admin',
    'django.contrib.auth',
    'django.contrib.contenttypes',
    'django.contrib.sessions',
    'django.contrib.messages',
    'django.contrib.staticfiles',
    'rest_framework',
    'api',
]

MIDDLEWARE = [
    'django.middleware.security.SecurityMiddleware',
    'django.contrib.sessions.middleware.SessionMiddleware',
    'django.middleware.common.CommonMiddleware',
    'django.middleware.csrf.CsrfViewMiddleware',
    'django.contrib.auth.middleware.AuthenticationMiddleware',
    'django.contrib.messages.middleware.MessageMiddleware',
    'django.middleware.clickjacking.XFrameOptionsMiddleware',
]

ROOT_URLCONF = 'shoplocal.urls'

TEMPLATES = [
    {
        'BACKEND': 'django.template.backends.django.DjangoTemplates',
        'DIRS': [],
        'APP_DIRS': True,
        'OPTIONS': {
            'context_processors': [
                'django.template.context_processors.debug',
                'django.template.context_processors.request',
                'django.contrib.auth.context_processors.auth',
                'django.contrib.messages.context_processors.messages',
            ],
        },
    },
]

WSGI_APPLICATION = 'shoplocal.wsgi.application'

# Database — read from environment variable
DATABASES = {
    'default': {
        'ENGINE': 'django.db.backends.postgresql',
        'NAME': os.environ.get('POSTGRES_DB', 'shopdb'),
        'USER': os.environ.get('POSTGRES_USER', 'shopuser'),
        'PASSWORD': os.environ.get('POSTGRES_PASSWORD', 'shoppass'),
        'HOST': os.environ.get('POSTGRES_HOST', 'db'),
        'PORT': os.environ.get('POSTGRES_PORT', '5432'),
    }
}

# Redis cache
REDIS_URL = os.environ.get('REDIS_URL', 'redis://redis:6379/0')

CACHES = {
    'default': {
        'BACKEND': 'django_redis.cache.RedisCache',
        'LOCATION': REDIS_URL,
        'OPTIONS': {
            'CLIENT_CLASS': 'django_redis.client.DefaultClient',
        }
    }
}

SESSION_ENGINE = 'django.contrib.sessions.backends.cache'
SESSION_CACHE_ALIAS = 'default'

AUTH_PASSWORD_VALIDATORS = [
    {'NAME': 'django.contrib.auth.password_validation.UserAttributeSimilarityValidator'},
    {'NAME': 'django.contrib.auth.password_validation.MinimumLengthValidator'},
    {'NAME': 'django.contrib.auth.password_validation.CommonPasswordValidator'},
    {'NAME': 'django.contrib.auth.password_validation.NumericPasswordValidator'},
]

LANGUAGE_CODE = 'en-us'
TIME_ZONE = 'UTC'
USE_I18N = True
USE_TZ = True

STATIC_URL = '/static/'
STATIC_ROOT = BASE_DIR / 'staticfiles'

DEFAULT_AUTO_FIELD = 'django.db.models.BigAutoField'

REST_FRAMEWORK = {
    'DEFAULT_RENDERER_CLASSES': [
        'rest_framework.renderers.JSONRenderer',
    ],
    'DEFAULT_PAGINATION_CLASS': 'rest_framework.pagination.PageNumberPagination',
    'PAGE_SIZE': 20,
}
```

**`shoplocal/settings/development.py`**:

```python
from .base import *

DEBUG = True

ALLOWED_HOSTS = ['*']

# In development, show full error pages
REST_FRAMEWORK['DEFAULT_RENDERER_CLASSES'].append(
    'rest_framework.renderers.BrowsableAPIRenderer'
)
```

Update `manage.py` to use the development settings:

```python
#!/usr/bin/env python
"""Django's command-line utility for administrative tasks."""
import os
import sys

def main():
    os.environ.setdefault('DJANGO_SETTINGS_MODULE', 'shoplocal.settings.development')
    try:
        from django.core.management import execute_from_command_line
    except ImportError as exc:
        raise ImportError(
            "Couldn't import Django."
        ) from exc
    execute_from_command_line(sys.argv)

if __name__ == '__main__':
    main()
```

Update `shoplocal/wsgi.py`:

```python
import os
from django.core.wsgi import get_wsgi_application

os.environ.setdefault('DJANGO_SETTINGS_MODULE', 'shoplocal.settings.base')
application = get_wsgi_application()
```

### Step 4 — Models

**`api/models.py`**:

```python
from django.db import models
from django.core.validators import MinValueValidator
from decimal import Decimal


class Customer(models.Model):
    name = models.CharField(max_length=255)
    email = models.EmailField(unique=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ['-created_at']

    def __str__(self):
        return f"{self.name} <{self.email}>"


class Product(models.Model):
    name = models.CharField(max_length=255)
    description = models.TextField(blank=True)
    price = models.DecimalField(
        max_digits=10,
        decimal_places=2,
        validators=[MinValueValidator(Decimal('0.01'))]
    )
    stock_quantity = models.PositiveIntegerField(default=0)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ['name']

    def __str__(self):
        return f"{self.name} (${self.price})"

    @property
    def in_stock(self):
        return self.stock_quantity > 0


class Order(models.Model):
    class Status(models.TextChoices):
        PENDING = 'pending', 'Pending'
        CONFIRMED = 'confirmed', 'Confirmed'
        SHIPPED = 'shipped', 'Shipped'
        DELIVERED = 'delivered', 'Delivered'
        CANCELLED = 'cancelled', 'Cancelled'

    customer = models.ForeignKey(
        Customer,
        on_delete=models.PROTECT,
        related_name='orders'
    )
    status = models.CharField(
        max_length=20,
        choices=Status.choices,
        default=Status.PENDING
    )
    total_price = models.DecimalField(
        max_digits=12,
        decimal_places=2,
        default=Decimal('0.00')
    )
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ['-created_at']

    def __str__(self):
        return f"Order #{self.pk} — {self.customer.name} ({self.status})"

    def calculate_total(self):
        self.total_price = sum(
            item.unit_price * item.quantity
            for item in self.items.all()
        )
        self.save(update_fields=['total_price'])


class OrderItem(models.Model):
    order = models.ForeignKey(
        Order,
        on_delete=models.CASCADE,
        related_name='items'
    )
    product = models.ForeignKey(
        Product,
        on_delete=models.PROTECT,
        related_name='order_items'
    )
    quantity = models.PositiveIntegerField(validators=[MinValueValidator(1)])
    unit_price = models.DecimalField(max_digits=10, decimal_places=2)

    class Meta:
        unique_together = ['order', 'product']

    def __str__(self):
        return f"{self.quantity}x {self.product.name} in Order #{self.order.pk}"
```

### Step 5 — Serializers

**`api/serializers.py`**:

```python
from rest_framework import serializers
from django.db import transaction
from .models import Customer, Product, Order, OrderItem


class CustomerSerializer(serializers.ModelSerializer):
    order_count = serializers.SerializerMethodField()

    class Meta:
        model = Customer
        fields = ['id', 'name', 'email', 'order_count', 'created_at']
        read_only_fields = ['id', 'created_at']

    def get_order_count(self, obj):
        return obj.orders.count()


class ProductSerializer(serializers.ModelSerializer):
    in_stock = serializers.BooleanField(read_only=True)

    class Meta:
        model = Product
        fields = ['id', 'name', 'description', 'price', 'stock_quantity', 'in_stock', 'created_at']
        read_only_fields = ['id', 'created_at']


class OrderItemSerializer(serializers.ModelSerializer):
    product_name = serializers.CharField(source='product.name', read_only=True)

    class Meta:
        model = OrderItem
        fields = ['id', 'product', 'product_name', 'quantity', 'unit_price']
        read_only_fields = ['id', 'unit_price', 'product_name']


class OrderCreateItemSerializer(serializers.Serializer):
    """Used only during order creation to validate input."""
    product_id = serializers.IntegerField()
    quantity = serializers.IntegerField(min_value=1)


class OrderSerializer(serializers.ModelSerializer):
    items = OrderItemSerializer(many=True, read_only=True)
    customer_name = serializers.CharField(source='customer.name', read_only=True)

    class Meta:
        model = Order
        fields = ['id', 'customer', 'customer_name', 'status', 'total_price', 'items', 'created_at']
        read_only_fields = ['id', 'total_price', 'created_at', 'customer_name']


class OrderCreateSerializer(serializers.Serializer):
    """Handles the full order creation flow with stock validation."""
    customer_id = serializers.IntegerField()
    items = OrderCreateItemSerializer(many=True, min_length=1)

    def validate_customer_id(self, value):
        try:
            Customer.objects.get(pk=value)
        except Customer.DoesNotExist:
            raise serializers.ValidationError(f"Customer {value} does not exist.")
        return value

    def validate(self, data):
        # Validate all products exist and have sufficient stock
        errors = []
        for item_data in data['items']:
            try:
                product = Product.objects.get(pk=item_data['product_id'])
                if product.stock_quantity < item_data['quantity']:
                    errors.append(
                        f"Insufficient stock for '{product.name}': "
                        f"requested {item_data['quantity']}, available {product.stock_quantity}"
                    )
            except Product.DoesNotExist:
                errors.append(f"Product {item_data['product_id']} does not exist.")

        if errors:
            raise serializers.ValidationError(errors)

        return data

    @transaction.atomic
    def create(self, validated_data):
        customer = Customer.objects.get(pk=validated_data['customer_id'])
        order = Order.objects.create(customer=customer)

        for item_data in validated_data['items']:
            product = Product.objects.select_for_update().get(pk=item_data['product_id'])

            # Deduct stock (select_for_update locks the row — prevents race conditions)
            product.stock_quantity -= item_data['quantity']
            product.save(update_fields=['stock_quantity'])

            OrderItem.objects.create(
                order=order,
                product=product,
                quantity=item_data['quantity'],
                unit_price=product.price,
            )

        order.calculate_total()
        return order
```

### Step 6 — Caching helper

**`api/cache.py`**:

```python
"""
Redis caching helpers for the API.

Pattern used: Cache-aside (lazy loading)
- Check cache first
- On miss: fetch from DB, store in cache, return
- Invalidate on writes
"""
from django.core.cache import cache
from django.conf import settings
import json

PRODUCT_LIST_KEY = 'api:products:list'
PRODUCT_DETAIL_KEY = 'api:products:detail:{id}'
CACHE_TTL = 60 * 5  # 5 minutes


def get_cached_product_list():
    """Return cached product list or None if not cached."""
    return cache.get(PRODUCT_LIST_KEY)


def set_cached_product_list(data):
    """Cache serialized product list."""
    cache.set(PRODUCT_LIST_KEY, data, timeout=CACHE_TTL)


def get_cached_product(product_id):
    """Return cached product detail or None."""
    key = PRODUCT_DETAIL_KEY.format(id=product_id)
    return cache.get(key)


def set_cached_product(product_id, data):
    """Cache serialized product detail."""
    key = PRODUCT_DETAIL_KEY.format(id=product_id)
    cache.set(key, data, timeout=CACHE_TTL)


def invalidate_product_cache(product_id=None):
    """
    Invalidate product cache on writes.
    Call this whenever a product is created, updated, or deleted.
    """
    cache.delete(PRODUCT_LIST_KEY)
    if product_id:
        cache.delete(PRODUCT_DETAIL_KEY.format(id=product_id))
```

### Step 7 — Views

**`api/views.py`**:

```python
from rest_framework import status
from rest_framework.decorators import api_view
from rest_framework.response import Response
from django.shortcuts import get_object_or_404
from .models import Customer, Product, Order
from .serializers import (
    CustomerSerializer,
    ProductSerializer,
    OrderSerializer,
    OrderCreateSerializer,
)
from .cache import (
    get_cached_product_list,
    set_cached_product_list,
    get_cached_product,
    set_cached_product,
    invalidate_product_cache,
)


@api_view(['GET'])
def health_check(request):
    """Simple health check — useful for Docker and load balancer health probes."""
    return Response({'status': 'ok', 'service': 'shoplocal-api'})


# ── Customers ─────────────────────────────────────────────────────────────────

@api_view(['GET', 'POST'])
def customer_list(request):
    if request.method == 'GET':
        customers = Customer.objects.all()
        serializer = CustomerSerializer(customers, many=True)
        return Response(serializer.data)

    serializer = CustomerSerializer(data=request.data)
    if serializer.is_valid():
        serializer.save()
        return Response(serializer.data, status=status.HTTP_201_CREATED)
    return Response(serializer.errors, status=status.HTTP_400_BAD_REQUEST)


@api_view(['GET', 'PUT', 'DELETE'])
def customer_detail(request, pk):
    customer = get_object_or_404(Customer, pk=pk)

    if request.method == 'GET':
        return Response(CustomerSerializer(customer).data)

    if request.method == 'PUT':
        serializer = CustomerSerializer(customer, data=request.data, partial=True)
        if serializer.is_valid():
            serializer.save()
            return Response(serializer.data)
        return Response(serializer.errors, status=status.HTTP_400_BAD_REQUEST)

    customer.delete()
    return Response(status=status.HTTP_204_NO_CONTENT)


# ── Products ──────────────────────────────────────────────────────────────────

@api_view(['GET', 'POST'])
def product_list(request):
    if request.method == 'GET':
        # Try cache first
        cached = get_cached_product_list()
        if cached is not None:
            return Response(cached)

        products = Product.objects.all()
        serializer = ProductSerializer(products, many=True)

        # Store in cache before returning
        set_cached_product_list(serializer.data)
        return Response(serializer.data)

    serializer = ProductSerializer(data=request.data)
    if serializer.is_valid():
        product = serializer.save()
        invalidate_product_cache()  # Invalidate list cache on create
        return Response(serializer.data, status=status.HTTP_201_CREATED)
    return Response(serializer.errors, status=status.HTTP_400_BAD_REQUEST)


@api_view(['GET', 'PUT', 'DELETE'])
def product_detail(request, pk):
    product = get_object_or_404(Product, pk=pk)

    if request.method == 'GET':
        cached = get_cached_product(pk)
        if cached is not None:
            return Response(cached)

        serializer = ProductSerializer(product)
        set_cached_product(pk, serializer.data)
        return Response(serializer.data)

    if request.method == 'PUT':
        serializer = ProductSerializer(product, data=request.data, partial=True)
        if serializer.is_valid():
            serializer.save()
            invalidate_product_cache(product_id=pk)
            return Response(serializer.data)
        return Response(serializer.errors, status=status.HTTP_400_BAD_REQUEST)

    product.delete()
    invalidate_product_cache(product_id=pk)
    return Response(status=status.HTTP_204_NO_CONTENT)


# ── Orders ────────────────────────────────────────────────────────────────────

@api_view(['GET', 'POST'])
def order_list(request):
    if request.method == 'GET':
        orders = Order.objects.select_related('customer').prefetch_related('items__product')
        serializer = OrderSerializer(orders, many=True)
        return Response(serializer.data)

    serializer = OrderCreateSerializer(data=request.data)
    if serializer.is_valid():
        order = serializer.save()
        return Response(OrderSerializer(order).data, status=status.HTTP_201_CREATED)
    return Response(serializer.errors, status=status.HTTP_400_BAD_REQUEST)


@api_view(['GET'])
def order_detail(request, pk):
    order = get_object_or_404(
        Order.objects.select_related('customer').prefetch_related('items__product'),
        pk=pk
    )
    return Response(OrderSerializer(order).data)
```

### Step 8 — URLs

**`api/urls.py`**:

```python
from django.urls import path
from . import views

urlpatterns = [
    path('health/', views.health_check),
    path('customers/', views.customer_list),
    path('customers/<int:pk>/', views.customer_detail),
    path('products/', views.product_list),
    path('products/<int:pk>/', views.product_detail),
    path('orders/', views.order_list),
    path('orders/<int:pk>/', views.order_detail),
]
```

**`shoplocal/urls.py`**:

```python
from django.contrib import admin
from django.urls import path, include

urlpatterns = [
    path('admin/', admin.site.urls),
    path('api/', include('api.urls')),
]
```

### Step 9 — Admin and apps config

**`api/admin.py`**:

```python
from django.contrib import admin
from .models import Customer, Product, Order, OrderItem


class OrderItemInline(admin.TabularInline):
    model = OrderItem
    extra = 0
    readonly_fields = ['unit_price']


@admin.register(Order)
class OrderAdmin(admin.ModelAdmin):
    list_display = ['id', 'customer', 'status', 'total_price', 'created_at']
    list_filter = ['status']
    inlines = [OrderItemInline]


@admin.register(Product)
class ProductAdmin(admin.ModelAdmin):
    list_display = ['name', 'price', 'stock_quantity', 'in_stock']


@admin.register(Customer)
class CustomerAdmin(admin.ModelAdmin):
    list_display = ['name', 'email', 'created_at']


admin.site.register(OrderItem)
```

**`api/apps.py`**:

```python
from django.apps import AppConfig


class ApiConfig(AppConfig):
    default_auto_field = 'django.db.models.BigAutoField'
    name = 'shop'
```

### Step 10 — Seed data management command

```bash
mkdir -p shop/management/commands
touch shop/management/__init__.py
touch shop/management/commands/__init__.py
```

**`api/management/commands/seed_data.py`**:

```python
from django.core.management.base import BaseCommand
from api.models import Customer, Product, Order, OrderItem
from decimal import Decimal
import random


class Command(BaseCommand):
    help = 'Seed the database with sample e-commerce data'

    def add_arguments(self, parser):
        parser.add_argument('--clear', action='store_true', help='Clear existing data first')

    def handle(self, *args, **options):
        if options['clear']:
            self.stdout.write('Clearing existing data...')
            OrderItem.objects.all().delete()
            Order.objects.all().delete()
            Product.objects.all().delete()
            Customer.objects.all().delete()

        self.stdout.write('Creating customers...')
        customers = [
            Customer.objects.get_or_create(
                email=email,
                defaults={'name': name}
            )[0]
            for name, email in [
                ('Alice Johnson', 'alice@example.com'),
                ('Bob Smith', 'bob@example.com'),
                ('Carol White', 'carol@example.com'),
                ('David Lee', 'david@example.com'),
                ('Eve Davis', 'eve@example.com'),
            ]
        ]

        self.stdout.write('Creating products...')
        products_data = [
            ('Wireless Headphones', 'Premium noise-cancelling headphones', '149.99', 50),
            ('Mechanical Keyboard', 'Tactile switches, RGB backlight', '89.99', 30),
            ('USB-C Hub', '7-in-1 hub with HDMI and SD card', '49.99', 100),
            ('Laptop Stand', 'Aluminium adjustable stand', '39.99', 75),
            ('Webcam 4K', 'Ultra HD webcam with autofocus', '129.99', 25),
            ('Mouse Pad XL', 'Extended desk mat, 900x400mm', '29.99', 200),
            ('Blue Light Glasses', 'Anti-fatigue computer glasses', '24.99', 150),
            ('Desk Lamp LED', 'Adjustable color temperature', '59.99', 40),
        ]

        products = []
        for name, desc, price, stock in products_data:
            product, _ = Product.objects.get_or_create(
                name=name,
                defaults={
                    'description': desc,
                    'price': Decimal(price),
                    'stock_quantity': stock,
                }
            )
            products.append(product)

        self.stdout.write('Creating orders...')
        for i in range(10):
            customer = random.choice(customers)
            order = Order.objects.create(customer=customer, status=random.choice(
                ['pending', 'confirmed', 'shipped', 'delivered']
            ))

            # Add 1–3 random items
            chosen_products = random.sample(products, random.randint(1, 3))
            for product in chosen_products:
                qty = random.randint(1, 3)
                if product.stock_quantity >= qty:
                    OrderItem.objects.create(
                        order=order,
                        product=product,
                        quantity=qty,
                        unit_price=product.price,
                    )
                    product.stock_quantity -= qty
                    product.save(update_fields=['stock_quantity'])

            order.calculate_total()

        self.stdout.write(self.style.SUCCESS(
            f'Done! Created {len(customers)} customers, {len(products)} products, 10 orders.'
        ))
```

### Step 11 — Dockerfile

**`shoplocal/Dockerfile`**:

```dockerfile
FROM python:3.12-slim

# Install system dependencies
# libpq-dev: required to compile psycopg2
# gcc: C compiler for some pip packages
RUN apt-get update && apt-get install -y \
    libpq-dev \
    gcc \
    curl \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app

# Copy requirements first — cached layer unless requirements.txt changes
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# Copy application code
COPY . .

# Collect static files (needed for admin)
RUN python manage.py collectstatic --noinput \
    --settings=shoplocal.settings.base

EXPOSE 8000

# Entrypoint: migrate then start gunicorn
# Using exec form (JSON array) so gunicorn is PID 1 and receives signals correctly
CMD ["sh", "-c", "python manage.py migrate && gunicorn shoplocal.wsgi:application --bind 0.0.0.0:8000 --workers 2 --timeout 60"]
```

**`shoplocal/.dockerignore`**:

```
__pycache__/
*.pyc
*.pyo
.env
.git
.gitignore
*.md
.venv/
venv/
*.log
db.sqlite3
staticfiles/
```

### Step 12 — Nginx configuration

```bash
mkdir -p ../nginx
```

**`nginx/nginx.conf`**:

```nginx
upstream django {
    server web:8000;
}

server {
    listen 80;
    server_name localhost;

    # Proxy API requests to Django/Gunicorn
    location /api/ {
        proxy_pass http://django;
        proxy_set_header Host $host;
        proxy_set_header X-Real-IP $remote_addr;
        proxy_set_header X-Forwarded-For $proxy_add_x_forwarded_for;
        proxy_set_header X-Forwarded-Proto $scheme;
        proxy_connect_timeout 60s;
        proxy_read_timeout 60s;
    }

    # Proxy admin
    location /admin/ {
        proxy_pass http://django;
        proxy_set_header Host $host;
        proxy_set_header X-Real-IP $remote_addr;
    }

    # Serve static files directly from Nginx (bypasses Django entirely)
    location /static/ {
        alias /app/staticfiles/;
    }

    # Health check
    location /health/ {
        proxy_pass http://django/api/health/;
    }
}
```

### Step 13 — Docker Compose files

Back in `module-01-docker/`, create:

**`docker-compose.yml`** (production-like):

```yaml
version: "3.9"

services:
  db:
    image: postgres:16
    restart: unless-stopped
    environment:
      POSTGRES_DB: ${POSTGRES_DB:-shopdb}
      POSTGRES_USER: ${POSTGRES_USER:-shopuser}
      POSTGRES_PASSWORD: ${POSTGRES_PASSWORD:-shoppass}
    volumes:
      - postgres_data:/var/lib/postgresql/data
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
      context: ./shoplocal
      dockerfile: Dockerfile
    restart: unless-stopped
    environment:
      DJANGO_SETTINGS_MODULE: shoplocal.settings.base
      SECRET_KEY: ${SECRET_KEY:-dev-secret-key}
      DEBUG: "False"
      POSTGRES_HOST: db
      POSTGRES_DB: ${POSTGRES_DB:-shopdb}
      POSTGRES_USER: ${POSTGRES_USER:-shopuser}
      POSTGRES_PASSWORD: ${POSTGRES_PASSWORD:-shoppass}
      REDIS_URL: redis://redis:6379/0
      ALLOWED_HOSTS: "localhost,127.0.0.1"
    depends_on:
      db:
        condition: service_healthy
      redis:
        condition: service_healthy
    volumes:
      - static_files:/app/staticfiles

  nginx:
    image: nginx:alpine
    restart: unless-stopped
    ports:
      - "80:80"
    volumes:
      - ./nginx/nginx.conf:/etc/nginx/conf.d/default.conf:ro
      - static_files:/app/staticfiles:ro
    depends_on:
      - web

volumes:
  postgres_data:
  static_files:
```

**`docker-compose.dev.yml`** (development overrides):

```yaml
version: "3.9"

# Usage: docker compose -f docker-compose.yml -f docker-compose.dev.yml up
# This file overrides the base compose file for local development

services:
  web:
    environment:
      DJANGO_SETTINGS_MODULE: shoplocal.settings.development
      DEBUG: "True"
    volumes:
      - ./shoplocal:/app # Mount source code — changes reflected without rebuild
    ports:
      - "8000:8000" # Direct access to Django (bypass Nginx)
    command: >
      sh -c "python manage.py migrate &&
             python manage.py runserver 0.0.0.0:8000"

  db:
    ports:
      - "5432:5432" # Expose Postgres for local DB clients (TablePlus, etc.)

  redis:
    ports:
      - "6379:6379" # Expose Redis for local inspection
```

**`.env.example`**:

```bash
# Copy to .env and fill in values
SECRET_KEY=your-secret-key-here
POSTGRES_DB=shopdb
POSTGRES_USER=shopuser
POSTGRES_PASSWORD=shoppass
```

### Step 14 — Run it

```bash
# You should be in module-01-docker/
cd ~/projects/systems-at-scale-local/module-01-docker

# Copy env file
cp .env.example .env

# Build and start all services
docker compose up --build

# In a new terminal, seed the database
docker compose exec web python manage.py seed_data

# Create a Django superuser for the admin
docker compose exec web python manage.py createsuperuser
```

**Verify everything works:**

```bash
# Health check
curl http://localhost/health/
# {"status": "ok", "service": "shoplocal-api"}

# List products
curl http://localhost/api/products/
# Returns JSON list of 8 products

# List customers
curl http://localhost/api/customers/

# Create a new product
curl -X POST http://localhost/shop/products/ \
  -H "Content-Type: application/json" \
  -d '{"name": "Standing Desk", "description": "Electric height-adjustable desk", "price": "499.99", "stock_quantity": 10}'

# Place an order (adjust IDs based on your seeded data)
curl -X POST http://localhost/shop/orders/ \
  -H "Content-Type: application/json" \
  -d '{"customer_id": 1, "items": [{"product_id": 1, "quantity": 2}, {"product_id": 3, "quantity": 1}]}'

# Get all orders
curl http://localhost/api/orders/
```

**Access Django Admin:** Open http://localhost/admin/ and log in with the
superuser you created.

**For development (with hot reload):**

```bash
docker compose -f docker-compose.yml -f docker-compose.dev.yml up --build
```

Now edit any Python file in `shoplocal/` and Django will auto-reload without
rebuilding the image.

---

## 10. Common Errors & Fixes

### "port is already allocated"

```
Error: Bind for 0.0.0.0:80 failed: port is already allocated
```

Something else is using port 80. Find and stop it:

```bash
lsof -i :80
# Kill the process, or change the port in docker-compose.yml to "8080:80"
```

### "django.db.utils.OperationalError: could not connect to server"

Django started before Postgres was ready. The healthcheck in the compose file
should prevent this, but if it happens:

```bash
docker compose restart web
```

### "exec format error" on Apple Silicon

You're pulling an AMD64-only image. The fix is explicit platform specification:

```yaml
image: some-image:tag
platform: linux/amd64 # Forces Rosetta emulation — slower but works
```

For all images in this module (Python, Postgres, Redis, Nginx), ARM64 images
exist natively so you won't hit this.

### Changes to Python files not reflected

If using the production compose (no source mount):

```bash
docker compose up --build
```

If using dev compose (source is mounted): Django's runserver auto-reloads — just
save the file.

### "No module named X" inside container

You added a package to `requirements.txt` but the image wasn't rebuilt:

```bash
docker compose build web
docker compose up
```

### Database is in a broken state

Nuclear option — wipe everything and start fresh:

```bash
docker compose down -v   # Removes containers AND volumes (deletes the database!)
docker compose up --build
docker compose exec web python manage.py seed_data
```

### Checking Redis cache is working

```bash
docker compose exec redis redis-cli

# In redis-cli:
keys *                    # List all keys
get api:products:list     # Get the cached product list
ttl api:products:list     # Time remaining before expiry
```

---

## 11. What You've Learned

By completing this module you now understand:

**Concepts:**

- Why containers exist and how they differ from VMs
- How Docker uses Linux namespaces and cgroups under the hood
- The image layer system and why layer order matters for build speed
- The difference between images, containers, and volumes
- How Docker networks work and why services use hostnames not localhost

**Practical skills:**

- Writing a production-quality `Dockerfile` with proper caching
- Using `.dockerignore` to keep images lean
- Running a multi-service app with Docker Compose
- Separating development and production Compose configurations
- Healthchecks and service dependencies
- Persisting database data with named volumes
- Running one-off commands (`exec`, migrations, management commands)
- Exposing Postgres and Redis for local tooling in dev, not in prod

**The Django app:**

- A real REST API with Postgres, Redis caching, and Nginx
- Cache-aside pattern with cache invalidation on writes
- `select_for_update()` to prevent race conditions on stock deduction
- Separated settings for base/development
- A seed command you can re-run at any time

---

## 12. Git Repo Structure

This is how your repo should look after Module 1:

```
systems-at-scale-local/
├── README.md                        ← repo overview and module index
└── module-01-docker/
    ├── module-01-docker.md          ← this document
    ├── docker-compose.yml
    ├── docker-compose.dev.yml
    ├── .env.example
    ├── nginx/
    │   └── nginx.conf
    └── shoplocal/
        ├── Dockerfile
        ├── .dockerignore
        ├── requirements.txt
        ├── manage.py
        ├── shoplocal/
        │   ├── __init__.py
        │   ├── settings/
        │   │   ├── __init__.py
        │   │   ├── base.py
        │   │   └── development.py
        │   ├── urls.py
        │   └── wsgi.py
        └── api/
            ├── __init__.py
            ├── admin.py
            ├── apps.py
            ├── models.py
            ├── serializers.py
            ├── views.py
            ├── urls.py
            ├── cache.py
            └── management/
                └── commands/
                    ├── __init__.py
                    └── seed_data.py
```

**Suggested repo README.md top section:**

```markdown
# Systems at Scale (Local)

Learning distributed systems fundamentals — one Docker Compose file at a time.
All projects run entirely on a local machine. No cloud account required.

## Modules

| #   | Topic                           | Status      |
| --- | ------------------------------- | ----------- |
| 01  | Docker & Docker Compose         | ✅ Complete |
| 02  | Apache Kafka                    | 🔜 Next     |
| 03  | Kubernetes                      | ⏳ Planned  |
| 04  | Apache Spark                    | ⏳ Planned  |
| 05  | Redis Advanced                  | ⏳ Planned  |
| 06  | Prometheus + Grafana            | ⏳ Planned  |
| 07  | Elasticsearch                   | ⏳ Planned  |
| 08  | gRPC                            | ⏳ Planned  |
| 09  | Terraform                       | ⏳ Planned  |
| 10  | CI/CD — GitHub Actions + ArgoCD | ⏳ Planned  |

## Domain

All modules use the same e-commerce domain (ShopLocal) so the capstone project
is a natural extension of everything built here.
```

---

_Next module: **Module 02 — Apache Kafka** — Add an event-driven order pipeline.
When an order is placed via the Django API, it publishes an event to Kafka. Two
independent consumer services process it: one updates order status, one sends
notifications._
