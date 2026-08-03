from decimal import Decimal

from rest_framework import serializers

from .models import Customer, Order, OrderItem, Product


class ProductSerializer(serializers.ModelSerializer):
    class Meta:
        model = Product
        fields = ["id", "name", "price", "stock"]


class OrderItemOutputSerializer(serializers.ModelSerializer):
    product_name = serializers.CharField(source="product.name", read_only=True)

    class Meta:
        model = OrderItem
        fields = ["product_id", "product_name", "quantity", "unit_price"]


class OrderSerializer(serializers.ModelSerializer):
    items = OrderItemOutputSerializer(many=True, read_only=True)
    customer_email = serializers.EmailField(
        source="customer.email", read_only=True
    )

    class Meta:
        model = Order
        fields = [
            "id",
            "customer_id",
            "customer_email",
            "total_price",
            "status",
            "items",
            "created_at",
            "updated_at",
        ]


class _OrderItemInputSerializer(serializers.Serializer):
    product_id = serializers.IntegerField(min_value=1)
    quantity = serializers.IntegerField(min_value=1)


class OrderCreateSerializer(serializers.Serializer):
    customer_id = serializers.IntegerField(min_value=1)
    items = _OrderItemInputSerializer(many=True, allow_empty=False)

    def validate_customer_id(self, value):
        if not Customer.objects.filter(pk=value).exists():
            raise serializers.ValidationError(
                f"Customer {value} does not exist."
            )
        return value

    def validate(self, data):
        product_ids = [item["product_id"] for item in data["items"]]
        found_ids = set(
            Product.objects.filter(pk__in=product_ids).values_list(
                "id", flat=True
            )
        )
        missing = set(product_ids) - found_ids
        if missing:
            raise serializers.ValidationError(
                f"Products not found: {sorted(missing)}"
            )
        return data

    def save(self):
        customer_id = self.validated_data["customer_id"]
        items_data = self.validated_data["items"]
        product_ids = [item["product_id"] for item in items_data]

        # Lock rows to prevent concurrent overselling
        products = {
            p.id: p
            for p in Product.objects.select_for_update().filter(
                pk__in=product_ids
            )
        }

        order_items = []
        total = Decimal("0")
        for item_data in items_data:
            product = products[item_data["product_id"]]
            qty = item_data["quantity"]
            if product.stock < qty:
                raise serializers.ValidationError(
                    f'Insufficient stock for "{product.name}": requested {qty}, available {product.stock}.'
                )
            product.stock -= qty
            product.save(update_fields=["stock"])
            total += product.price * qty
            order_items.append(
                OrderItem(
                    product=product, quantity=qty, unit_price=product.price
                )
            )

        order = Order.objects.create(
            customer_id=customer_id,
            total_price=total,
        )
        for item in order_items:
            item.order = order
        OrderItem.objects.bulk_create(order_items)
        return order
