from rest_framework import mixins, status, viewsets
from rest_framework.decorators import api_view
from rest_framework.exceptions import ValidationError
from rest_framework.response import Response

from .caching import (
    get_cached_product,
    get_cached_product_list,
    invalidate_product_cache,
    set_cached_product,
    set_cached_product_list,
)
from .models import Customer, Order, OrderItem, Product
from .serializers import (
    CustomerSerializer,
    OrderCreateSerializer,
    OrderSerializer,
    ProductSerializer,
)


@api_view(["GET"])
def health_check(request):
    """Simple health check — useful for Docker and load balancer health probes."""
    return Response({"status": "ok", "service": "shoplocal-api"})


class CustomerViewSet(viewsets.ModelViewSet):
    queryset = Customer.objects.all()
    serializer_class = CustomerSerializer


class ProductViewSet(viewsets.ModelViewSet):
    queryset = Product.objects.all()
    serializer_class = ProductSerializer

    def list(self, request, *args, **kwargs):
        # Just for demo, ignoring pagination and filters for simplicity
        cached_data = get_cached_product_list()
        if cached_data is not None:
            return Response(cached_data)

        response = super().list(request, *args, **kwargs)
        set_cached_product_list(response.data)
        return response

    def retrieve(self, request, *args, **kwargs):
        product_id = kwargs.get("pk")
        cached_data = get_cached_product(product_id)
        if cached_data is not None:
            return Response(cached_data)

        response = super().retrieve(request, *args, **kwargs)
        set_cached_product(product_id, response.data)
        return response

    def perform_create(self, serializer):
        instance = serializer.save()
        invalidate_product_cache(instance.id)

    def perform_update(self, serializer):
        instance = serializer.save()
        invalidate_product_cache(instance.id)

    def perform_destroy(self, instance):
        product_id = instance.id
        instance.delete()
        invalidate_product_cache(product_id)


class OrderViewSet(
    mixins.CreateModelMixin, mixins.RetrieveModelMixin, viewsets.GenericViewSet
):
    queryset = (
        Order.objects.select_related("customer")
        .prefetch_related("items__product")
        .all()
    )
    serializer_class = OrderSerializer

    def create(self, request, *args, **kwargs):
        customer_id = request.data.get("customer")
        items_data = request.data.get("items", [])

        if not customer_id or not items_data:
            raise ValidationError("Customer and items are required.")

        serializer = OrderCreateSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        serializer.save()
        return Response(serializer.data, status=status.HTTP_201_CREATED)
