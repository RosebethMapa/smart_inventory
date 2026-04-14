from django.contrib import admin
from .models import Product, RFIDUser, Transaction, Alert, Order, OrderItem, Payment


@admin.register(Product)
class ProductAdmin(admin.ModelAdmin):
    list_display = ('name', 'category', 'stock', 'reorder_level', 'price', 'is_active')
    search_fields = ('name', 'category', 'description')
    list_filter = ('category', 'is_active')
    ordering = ('name',)


@admin.register(RFIDUser)
class RFIDUserAdmin(admin.ModelAdmin):
    list_display = ('full_name', 'rfid_code', 'user_type')
    search_fields = ('full_name', 'rfid_code', 'user_type')
    ordering = ('full_name',)


@admin.register(Transaction)
class TransactionAdmin(admin.ModelAdmin):
    list_display = ('product', 'rfid_user', 'quantity', 'total_price', 'source', 'transaction_time')
    search_fields = ('product__name', 'rfid_user__full_name', 'rfid_user__rfid_code', 'source')
    list_filter = ('source', 'transaction_time')
    ordering = ('-transaction_time',)
    autocomplete_fields = ('product', 'rfid_user')


@admin.register(Alert)
class AlertAdmin(admin.ModelAdmin):
    list_display = ('alert_type', 'short_message', 'product', 'is_read', 'created_at')
    search_fields = ('message', 'alert_type', 'product__name')
    list_filter = ('alert_type', 'is_read', 'created_at')
    ordering = ('-created_at',)
    autocomplete_fields = ('product',)

    def short_message(self, obj):
        return obj.message[:70] + '...' if len(obj.message) > 70 else obj.message
    short_message.short_description = 'Message'


class OrderItemInline(admin.TabularInline):
    model = OrderItem
    extra = 0
    autocomplete_fields = ('product',)


@admin.register(Order)
class OrderAdmin(admin.ModelAdmin):
    list_display = ('order_number', 'customer_name', 'total_price', 'status', 'stock_deducted', 'created_at')
    search_fields = ('order_number', 'customer_name', 'status')
    list_filter = ('status', 'stock_deducted', 'created_at')
    ordering = ('-created_at',)
    inlines = [OrderItemInline]


@admin.register(Payment)
class PaymentAdmin(admin.ModelAdmin):
    list_display = (
        'customer_name',
        'order',
        'total_amount',
        'amount_received',
        'change',
        'payment_method',
        'short_note',
        'paid_at',
    )
    search_fields = ('customer_name', 'order__order_number', 'payment_method', 'note')
    list_filter = ('payment_method', 'paid_at')
    ordering = ('-paid_at',)
    autocomplete_fields = ('order',)

    def short_note(self, obj):
        if not obj.note:
            return '-'
        return obj.note[:50] + '...' if len(obj.note) > 50 else obj.note
    short_note.short_description = 'Note'