from django.contrib import admin

from .models import Customer, Order, OrderItem, Product


@admin.register(Customer)
class CustomerAdmin(admin.ModelAdmin):
    list_display = ("id", "name", "email", "created_at")
    search_fields = ("name", "email")
    ordering = ("-created_at",)


@admin.register(Product)
class ProductAdmin(admin.ModelAdmin):
    list_display = (
        "id",
        "name",
        "price",
        "stock_quantity",
    )
    search_fields = ("name",)
    ordering = ("price",)


class OrderItemInline(admin.TabularInline):
    model = OrderItem
    extra = 0
    readonly_fields = ("product", "quantity", "unit_price")
    can_delete = False


@admin.register(Order)
class OrderAdmin(admin.ModelAdmin):
    list_display = ("id", "customer", "status", "total_price", "created_at")
    search_fields = ("customer__name", "customer__email")
    ordering = ("-created_at",)
    inlines = [OrderItemInline]
