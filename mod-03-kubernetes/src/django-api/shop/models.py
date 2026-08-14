from django.db import models


class Customer(models.Model):
    """
    Represents a customer in the shop.
    """

    name = models.CharField(max_length=255)
    """Customer's full name."""

    email = models.EmailField(unique=True)
    """Customer's email address."""

    created_at = models.DateTimeField(auto_now_add=True)
    """Timestamp when the customer was created."""

    def __str__(self):
        return f"{self.name} <{self.email}>"


class Product(models.Model):
    """
    Represents a product available for purchase in the shop.
    """

    name = models.CharField(max_length=255)
    """Product name."""

    price = models.DecimalField(max_digits=10, decimal_places=2)
    """Product price."""

    stock = models.PositiveIntegerField(default=0)
    """Product stock quantity."""

    def __str__(self):
        return self.name


class Order(models.Model):
    """
    Represents an order placed by a customer.
    """

    class Status(models.TextChoices):
        PENDING = "pending", "Pending"
        CONFIRMED = "confirmed", "Confirmed"
        CANCELLED = "cancelled", "Cancelled"

    customer = models.ForeignKey(
        Customer, on_delete=models.PROTECT, related_name="orders"
    )
    """Customer who placed the order."""

    total_price = models.DecimalField(max_digits=10, decimal_places=2)
    """Total price of the order."""

    status = models.CharField(
        max_length=20, choices=Status.choices, default=Status.PENDING
    )
    """Status of the order."""

    created_at = models.DateTimeField(auto_now_add=True)
    """Timestamp when the order was created."""

    updated_at = models.DateTimeField(auto_now=True)
    """Timestamp when the order was last updated."""

    def __str__(self):
        return f"Order #{self.id} ({self.status})"


class OrderItem(models.Model):
    """
    Represents an item in an order.
    """

    order = models.ForeignKey(
        Order, on_delete=models.CASCADE, related_name="items"
    )
    """Order to which this item belongs."""

    product = models.ForeignKey(Product, on_delete=models.PROTECT)
    """Product associated with this order item."""

    quantity = models.PositiveIntegerField()
    """Quantity of the product in this order item."""

    unit_price = models.DecimalField(max_digits=10, decimal_places=2)
    """Unit price of the product at the time of order."""

    def __str__(self):
        return f"{self.quantity}x {self.product.name}"
