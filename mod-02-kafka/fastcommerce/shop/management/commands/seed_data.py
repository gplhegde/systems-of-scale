import random
from decimal import Decimal
from typing import List

from django.core.management.base import BaseCommand
from shop.models import Customer, Order, OrderItem, Product


class Command(BaseCommand):
    help = "Seed the database with sample data"

    def add_arguments(self, parser):
        parser.add_argument(
            "--clear", action="store_true", help="Clear existing data first"
        )

    def handle(self, *args, **options):
        self.stdout.write("Seeding data...")
        if options["clear"]:
            self.stdout.write("Clearing existing data...")
            OrderItem.objects.all().delete()
            Order.objects.all().delete()
            Product.objects.all().delete()
            Customer.objects.all().delete()

        self.stdout.write("Creating customers...")
        customers = self._create_customers()

        self.stdout.write("Creating products...")
        products = self._create_products()

        self.stdout.write("Creating orders...")

        self.stdout.write(
            self.style.SUCCESS(
                f"Done! Created {len(customers)} customers, {len(products)} products, 10 orders."
            )
        )

    def _create_customers(self):
        customers = [
            Customer.objects.get_or_create(email=email, defaults={"name": name})[0]
            for name, email in [
                ("Alice Johnson", "alice@example.com"),
                ("Bob Smith", "bob@example.com"),
                ("Carol White", "carol@example.com"),
                ("David Lee", "david@example.com"),
                ("Eve Davis", "eve@example.com"),
            ]
        ]
        return customers

    def _create_products(self):
        products_data = [
            (
                "Wireless Headphones",
                "Premium noise-cancelling headphones",
                "149.99",
                50,
            ),
            ("Mechanical Keyboard", "Tactile switches, RGB backlight", "89.99", 30),
            ("USB-C Hub", "7-in-1 hub with HDMI and SD card", "49.99", 100),
            ("Laptop Stand", "Aluminium adjustable stand", "39.99", 75),
            ("Webcam 4K", "Ultra HD webcam with autofocus", "129.99", 25),
            ("Mouse Pad XL", "Extended desk mat, 900x400mm", "29.99", 200),
            ("Blue Light Glasses", "Anti-fatigue computer glasses", "24.99", 150),
            ("Desk Lamp LED", "Adjustable color temperature", "59.99", 40),
        ]

        products = []
        for name, desc, price, stock in products_data:
            product, _ = Product.objects.get_or_create(
                name=name,
                defaults={
                    "description": desc,
                    "price": Decimal(price),
                    "stock_quantity": stock,
                },
            )
            products.append(product)
        return products

    def _create_orders(self, customers: List[Customer], products: List[Product]):
        for _ in range(10):
            customer = random.choice(customers)
            order = Order.objects.create(
                customer=customer,
                status=random.choice(["pending", "confirmed", "shipped", "delivered"]),
            )

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
                    product.save(update_fields=["stock_quantity"])

            order.calculate_total()
