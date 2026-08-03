import os
import time

import psycopg2


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
                host=os.environ.get("POSTGRES_HOST", "db"),
                dbname=os.environ.get("POSTGRES_DB", "shopdb"),
                user=os.environ.get("POSTGRES_USER", "shopuser"),
                password=os.environ.get("POSTGRES_PASSWORD", "shoppass"),
            )
        except psycopg2.OperationalError as e:
            print(
                f"DB connection attempt {attempt + 1}/{max_retries} failed: {e}"
            )
            time.sleep(3)
    raise RuntimeError("Could not connect to database after retries")


def confirm_order(db_conn, order_id):
    """
    Mark the order as confirmed in the database.
    """
    with db_conn.cursor() as cursor:
        cursor.execute(
            "UPDATE shop_order SET status = %s WHERE id = %s AND status = 'pending'",
            (
                "confirmed",
                order_id,
            ),
        )
        rows_updated = cursor.rowcount
        db_conn.commit()
        return rows_updated > 0
