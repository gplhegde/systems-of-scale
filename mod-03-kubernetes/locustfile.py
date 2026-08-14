import random

from locust import HttpUser, between, task

PRODUCT_IDS = list(range(1, 9))
CUSTOMER_IDS = list(range(1, 6))


class FastCommerceUser(HttpUser):
    wait_time = between(0.5, 2)

    @task(3)
    def place_order(self):
        with self.client.post(
            "/api/orders/",
            json={
                "customer_id": random.choice(CUSTOMER_IDS),
                "items": [
                    {
                        "product_id": random.choice(PRODUCT_IDS),
                        "quantity": random.randint(1, 3),
                    }
                ],
            },
            catch_response=True,
            name="POST /api/orders/",
        ) as response:
            if response.status_code in (201, 400):
                # 400 = stock exhausted — expected, not a failure
                response.success()
            else:
                response.failure(f"Unexpected {response.status_code}")

    @task(2)
    def list_products(self):
        self.client.get("/api/products/", name="GET /api/products/")

    @task(1)
    def list_orders(self):
        self.client.get("/api/orders/", name="GET /api/orders/")
