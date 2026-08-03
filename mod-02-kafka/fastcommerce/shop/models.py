from decimal import Decimal

from django.core.validators import MinValueValidator
from django.db import models


class TimestampedModel(models.Model):
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        abstract = True


class Customer(TimestampedModel):
    name = models.CharField(max_length=255)
    email = models.EmailField(unique=True)

    class Meta:
        ordering = ["-created_at"]

    def __str__(self):
        return f"{self.name} <{self.email}>"


class Product(TimestampedModel):
    name = models.CharField(max_length=255)
    description = models.TextField()
    price = models.DecimalField(
        max_digits=10, decimal_places=2, validators=[MinValueValidator(Decimal("0.01"))]
    )
    stock_quantity = models.PositiveIntegerField(default=0)

    class Meta:
        ordering = ["name"]

    def __str__(self):
        return f"{self.name} (${self.price})"

    @property
    def is_in_stock(self):
        return self.stock_quantity > 0


class Order(TimestampedModel):
    class Status(models.TextChoices):
        PENDING = "pending", "Pending"
        CONFIRMED = "confirmed", "Confirmed"
        SHIPPED = "shipped", "Shipped"
        DELIVERED = "delivered", "Delivered"
        CANCELLED = "cancelled", "Cancelled"

    customer = models.ForeignKey(
        Customer, on_delete=models.PROTECT, related_name="orders"
    )
    status = models.CharField(
        max_length=20, choices=Status.choices, default=Status.PENDING
    )
    total_price = models.DecimalField(
        max_digits=12, decimal_places=2, default=Decimal("0.00")
    )

    class Meta:
        ordering = ["-created_at"]

    def __str__(self):
        return f"Order #{self.pk} — {self.customer.name} ({self.status})"

    def calculate_total(self):
        self.total_price = sum(
            item.unit_price * item.quantity for item in self.items.all()
        )
        self.save(update_fields=["total_price"])


class OrderItem(models.Model):
    order = models.ForeignKey(Order, on_delete=models.CASCADE, related_name="items")
    product = models.ForeignKey(
        Product, on_delete=models.PROTECT, related_name="order_items"
    )
    quantity = models.PositiveIntegerField(validators=[MinValueValidator(1)])
    unit_price = models.DecimalField(max_digits=10, decimal_places=2)

    class Meta:
        unique_together = ["order", "product"]

    def __str__(self):
        return f"{self.quantity}x {self.product.name} in Order #{self.order.pk}"
