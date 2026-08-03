from django.db import transaction
from rest_framework import serializers

from .models import Customer, Order, OrderItem, Product


class CustomerSerializer(serializers.ModelSerializer):
    order_count = serializers.SerializerMethodField()

    class Meta:
        model = Customer
        fields = ["id", "name", "email", "order_count", "created_at"]
        read_only_fields = ["id", "created_at"]

    def get_order_count(self, obj):
        return obj.orders.count()


class ProductSerializer(serializers.ModelSerializer):
    in_stock = serializers.BooleanField(read_only=True)

    class Meta:
        model = Product
        fields = [
            "id",
            "name",
            "description",
            "price",
            "stock_quantity",
            "in_stock",
            "created_at",
        ]
        read_only_fields = ["id", "created_at"]


class OrderItemSerializer(serializers.ModelSerializer):
    product_name = serializers.CharField(source="product.name", read_only=True)

    class Meta:
        model = OrderItem
        fields = ["id", "product", "product_name", "quantity", "unit_price"]
        read_only_fields = ["id", "unit_price", "product_name"]


class OrderCreateItemSerializer(serializers.Serializer):
    """Used only during order creation to validate input."""

    product_id = serializers.IntegerField()
    quantity = serializers.IntegerField(min_value=1)


class OrderSerializer(serializers.ModelSerializer):
    items = OrderItemSerializer(many=True, read_only=True)
    customer_name = serializers.CharField(source="customer.name", read_only=True)

    class Meta:
        model = Order
        fields = [
            "id",
            "customer",
            "customer_name",
            "status",
            "total_price",
            "items",
            "created_at",
        ]
        read_only_fields = ["id", "total_price", "created_at", "customer_name"]


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
        for item_data in data["items"]:
            try:
                product = Product.objects.get(pk=item_data["product_id"])
                if product.stock_quantity < item_data["quantity"]:
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
        customer = Customer.objects.get(pk=validated_data["customer_id"])
        order = Order.objects.create(customer=customer)

        for item_data in validated_data["items"]:
            product = Product.objects.select_for_update().get(
                pk=item_data["product_id"]
            )

            # Deduct stock (select_for_update locks the row — prevents race conditions)
            product.stock_quantity -= item_data["quantity"]
            product.save(update_fields=["stock_quantity"])

            OrderItem.objects.create(
                order=order,
                product=product,
                quantity=item_data["quantity"],
                unit_price=product.price,
            )

        order.calculate_total()
        return order
