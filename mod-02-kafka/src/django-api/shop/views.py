"""
Views for the shop app.

NOTE: This is not production-ready code by any means. It is meant to illustrate
the concepts in the course.
"""

from django.db import transaction
from rest_framework import status
from rest_framework.decorators import api_view
from rest_framework.response import Response

from .events import publish_order_event
from .models import Order, Product
from .serializers import (
    OrderCreateSerializer,
    OrderSerializer,
    ProductSerializer,
)


@api_view(["GET", "POST"])
def orders(request):
    if request.method == "POST":
        return _create_order(request)
    qs = (
        Order.objects.select_related("customer")
        .prefetch_related("items__product")
        .order_by("-created_at")
    )
    return Response(OrderSerializer(qs, many=True).data)


def _create_order(request):
    serializer = OrderCreateSerializer(data=request.data)
    if not serializer.is_valid():
        return Response(serializer.errors, status=status.HTTP_400_BAD_REQUEST)

    # The DB transaction commits here; the event is published after.
    # Publishing inside the transaction couples Kafka failure to a DB rollback.
    # Publishing after means a crash between the two leaves an order PENDING
    # with no event — the transactional outbox pattern (Section 8) closes this.
    with transaction.atomic():
        order = serializer.save()

    publish_order_event("order_placed", order)
    return Response(
        OrderSerializer(order).data, status=status.HTTP_201_CREATED
    )


@api_view(["GET"])
def get_order(request, pk):
    try:
        order = (
            Order.objects.select_related("customer")
            .prefetch_related("items__product")
            .get(pk=pk)
        )
    except Order.DoesNotExist:
        return Response(
            {"detail": "Not found."}, status=status.HTTP_404_NOT_FOUND
        )
    return Response(OrderSerializer(order).data)


@api_view(["GET"])
def list_products(request):
    products = Product.objects.all().order_by("id")
    return Response(ProductSerializer(products, many=True).data)
