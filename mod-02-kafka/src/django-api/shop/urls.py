from django.urls import path
from . import views

urlpatterns = [
    path('products/', views.list_products),
    path('orders/', views.orders),
    path('orders/<int:pk>/', views.get_order),
]
