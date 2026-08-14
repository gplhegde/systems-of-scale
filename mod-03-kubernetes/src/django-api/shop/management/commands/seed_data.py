from django.core.management.base import BaseCommand
from shop.models import Customer, Product

CUSTOMERS = [
    {"name": "Alice Chen", "email": "alice@example.com"},
    {"name": "Bob Patel", "email": "bob@example.com"},
    {"name": "Carol Smith", "email": "carol@example.com"},
    {"name": "Dave Kim", "email": "dave@example.com"},
    {"name": "Eve Torres", "email": "eve@example.com"},
]

PRODUCTS = [
    {"name": "Wireless Headphones", "price": "49.99"},
    {"name": "Mechanical Keyboard", "price": "89.99"},
    {"name": "USB-C Hub", "price": "34.99"},
    {"name": "Webcam HD", "price": "69.99"},
    {"name": "Laptop Stand", "price": "29.99"},
    {"name": "Mouse Pad XL", "price": "19.99"},
    {"name": "LED Desk Lamp", "price": "44.99"},
    {"name": "Cable Organiser", "price": "14.99"},
]

INITIAL_STOCK = 1000


class Command(BaseCommand):
    help = "Seed the database with customers and products."

    def add_arguments(self, parser):
        parser.add_argument(
            "--clear",
            action="store_true",
            help="Reset product stock to initial values.",
        )

    def handle(self, *args, **options):
        for data in CUSTOMERS:
            customer, created = Customer.objects.get_or_create(
                email=data["email"], defaults={"name": data["name"]}
            )
            if created:
                self.stdout.write(f"  Created customer: {customer.name}")

        for data in PRODUCTS:
            product, created = Product.objects.get_or_create(
                name=data["name"],
                defaults={"price": data["price"], "stock": INITIAL_STOCK},
            )
            if created:
                self.stdout.write(f"  Created product: {product.name}")
            elif options["clear"]:
                product.stock = INITIAL_STOCK
                product.save(update_fields=["stock"])
                self.stdout.write(f"  Reset stock: {product.name}")

        self.stdout.write(
            self.style.SUCCESS(
                f"Seed complete: {Customer.objects.count()} customers, {Product.objects.count()} products."
            )
        )
