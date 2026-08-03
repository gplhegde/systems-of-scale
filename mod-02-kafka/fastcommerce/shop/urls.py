from django.urls import include, path
from rest_framework.routers import DefaultRouter

from .views import CustomerViewSet, OrderViewSet, ProductViewSet, health_check

router = DefaultRouter()
router.register(r"customers", CustomerViewSet, basename="customer")
router.register(r"products", ProductViewSet, basename="product")
router.register(r"orders", OrderViewSet, basename="order")

urlpatterns = [
    path("health/", health_check, name="health-check"),
    path("", include(router.urls)),
]
