from django.urls import path
from . import views

urlpatterns = [
    path('', views.customer_home, name='customer_home'),
    path('dashboard/', views.dashboard, name='dashboard'),
    path('sales-report/', views.sales_report, name='sales_report'),

    path('products/', views.product_list, name='product_list'),
    path('transactions/', views.transaction_list, name='transaction_list'),

    path('alerts/', views.alert_list, name='alert_list'),
    path('alerts/clear/', views.clear_all_alerts, name='clear_all_alerts'),
    path('alerts/delete-all/', views.delete_all_alerts, name='delete_all_alerts'),
    path('alerts/read/<int:alert_id>/', views.mark_alert_read, name='mark_alert_read'),

    path('menu/', views.food_menu, name='food_menu'),
    path('menu/add/<int:product_id>/', views.add_to_cart, name='add_to_cart'),

    path('cart/', views.cart_view, name='cart_view'),
    path('cart/update/<int:product_id>/', views.update_cart_item, name='update_cart_item'),
    path('cart/remove/<int:product_id>/', views.remove_cart_item, name='remove_cart_item'),
    path('cart/clear/', views.clear_cart, name='clear_cart'),

    path('track-order/', views.track_order, name='track_order'),

    path('orders/', views.order_list, name='order_list'),
    path('orders/<int:order_id>/<str:new_status>/', views.update_order_status, name='update_order_status'),

    path('payments/', views.payment_list, name='payment_list'),
    path('payments/record/<int:order_id>/', views.record_payment, name='record_payment'),

    path('order-success/<int:order_id>/', views.order_success, name='order_success'),

    path('admin-unlock/', views.unlock_store_admin, name='unlock_store_admin'),
    path('admin-lock/', views.lock_store_admin, name='lock_store_admin'),

    path('api/calculate-change/', views.calculate_change, name='calculate_change'),
    path('api/rfid/', views.record_rfid_transaction, name='record_rfid_transaction'),
]