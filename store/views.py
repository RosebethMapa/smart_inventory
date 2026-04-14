from decimal import Decimal, InvalidOperation
import json
from datetime import timedelta

import requests
from django.conf import settings
from django.contrib import messages
from django.core.mail import send_mail
from django.shortcuts import render, redirect, get_object_or_404
from django.db import models, transaction, IntegrityError
from django.db.models import Sum, Q, Count
from django.http import JsonResponse
from django.views.decorators.csrf import csrf_exempt
from django.utils import timezone
from django.db.models.functions import TruncDate, TruncWeek

from .models import Product, Transaction, Alert, RFIDUser, Order, OrderItem, Payment


STORE_ACCESS_PASSWORD = "1234"


def is_store_admin(request):
    return request.session.get('store_admin_unlocked', False)


def require_store_admin(request):
    if not is_store_admin(request):
        messages.error(request, 'Admin access is required for that action.')
        return False
    return True


def send_telegram_message(message):
    token = getattr(settings, 'TELEGRAM_BOT_TOKEN', '').strip()
    chat_id = str(getattr(settings, 'TELEGRAM_CHAT_ID', '')).strip()

    if not token or not chat_id:
        return False

    try:
        url = f"https://api.telegram.org/bot{token}/sendMessage"
        payload = {
            "chat_id": chat_id,
            "text": message,
        }
        response = requests.post(url, data=payload, timeout=10)
        return response.status_code == 200
    except Exception as e:
        print("Telegram error:", e)
        return False


def send_admin_email(subject, message):
    admin_email = getattr(settings, 'ADMIN_EMAIL', '').strip()
    from_email = getattr(settings, 'DEFAULT_FROM_EMAIL', '').strip()

    if not admin_email or not from_email:
        return False

    try:
        send_mail(
            subject,
            message,
            from_email,
            [admin_email],
            fail_silently=False,
        )
        return True
    except Exception as e:
        print("Admin email error:", e)
        return False


def send_customer_email(subject, message, recipient_email):
    recipient_email = (recipient_email or '').strip()
    from_email = getattr(settings, 'DEFAULT_FROM_EMAIL', '').strip()

    if not recipient_email or not from_email:
        return False

    try:
        send_mail(
            subject,
            message,
            from_email,
            [recipient_email],
            fail_silently=False,
        )
        return True
    except Exception as e:
        print("Customer email error:", e)
        return False


@csrf_exempt
def unlock_store_admin(request):
    if request.method != 'POST':
        return JsonResponse({'success': False, 'message': 'Invalid request method.'}, status=405)

    try:
        data = json.loads(request.body)
        password = str(data.get('password', '')).strip()
    except Exception:
        return JsonResponse({'success': False, 'message': 'Invalid request data.'}, status=400)

    if password == STORE_ACCESS_PASSWORD:
        request.session['store_admin_unlocked'] = True
        request.session.modified = True
        return JsonResponse({'success': True, 'message': 'Admin mode unlocked.'})

    return JsonResponse({'success': False, 'message': 'Wrong password.'}, status=403)


def lock_store_admin(request):
    request.session.pop('store_admin_unlocked', None)
    request.session.modified = True
    messages.success(request, 'Admin mode locked.')
    return redirect('food_menu')


def get_cart(request):
    return request.session.setdefault('cart', {})


def save_cart(request, cart):
    request.session['cart'] = cart
    request.session.modified = True


def build_cart_items(cart):
    product_ids = [int(pid) for pid in cart.keys()]
    products = Product.objects.filter(id__in=product_ids, is_active=True)

    items = []
    grand_total = Decimal('0.00')
    total_quantity = 0

    product_map = {product.id: product for product in products}

    for product_id_str, qty in cart.items():
        product_id = int(product_id_str)
        product = product_map.get(product_id)
        if not product:
            continue

        quantity = int(qty)
        if quantity < 1:
            continue

        subtotal = product.price * quantity
        grand_total += subtotal
        total_quantity += quantity

        items.append({
            'product': product,
            'quantity': quantity,
            'unit_price': product.price,
            'subtotal': subtotal,
        })

    return items, grand_total, total_quantity


def create_alert_once(product, alert_type, message):
    exists = Alert.objects.filter(
        product=product,
        alert_type=alert_type,
        message=message,
        is_read=False
    ).exists()

    if not exists:
        Alert.objects.create(
            product=product,
            alert_type=alert_type,
            message=message
        )


def generate_low_stock_alerts():
    low_stock_products = Product.objects.filter(stock__lte=models.F('reorder_level'))

    for product in low_stock_products:
        message = f'Low stock: {product.name} has {product.stock} left.'
        create_alert_once(product, 'LOW_STOCK', message)


def generate_best_selling_alert():
    best_selling = (
        Transaction.objects.values('product__name')
        .annotate(total_sold=Sum('quantity'))
        .order_by('-total_sold')
        .first()
    )

    if best_selling and best_selling['product__name'] and best_selling['total_sold']:
        product_name = best_selling['product__name']
        total_sold = best_selling['total_sold']
        message = f'Most selling product: {product_name} with {total_sold} items sold.'

        if not Alert.objects.filter(message=message, is_read=False).exists():
            Alert.objects.create(
                product=None,
                alert_type='RESTOCK',
                message=message
            )


def customer_home(request):
    cart = get_cart(request)
    _, _, total_quantity = build_cart_items(cart)

    featured_products = Product.objects.filter(is_active=True).order_by('name')[:6]

    category_data = (
        Product.objects.filter(is_active=True)
        .values('category')
        .annotate(total=Count('id'))
        .order_by('category')
    )

    context = {
        'cart_count': total_quantity,
        'featured_products': featured_products,
        'category_data': category_data,
        'is_admin_mode': is_store_admin(request),
    }
    return render(request, 'store/customer_home.html', context)


def dashboard(request):
    if not require_store_admin(request):
        return redirect('customer_home')

    generate_low_stock_alerts()
    generate_best_selling_alert()

    products = Product.objects.all()
    transactions = Transaction.objects.select_related('product').order_by('-transaction_time')[:10]
    alerts = Alert.objects.filter(is_read=False).order_by('-created_at')
    orders = Order.objects.prefetch_related('items__product').order_by('-created_at')[:10]

    total_products = products.count()
    low_stock_products = products.filter(stock__lte=models.F('reorder_level')).count()
    total_sales = Payment.objects.aggregate(total=Sum('total_amount'))['total'] or Decimal('0.00')
    pending_orders = Order.objects.filter(status='PENDING').count()

    today = timezone.localdate()
    start_daily = today - timedelta(days=6)

    daily_sales_qs = (
        Payment.objects
        .filter(paid_at__date__gte=start_daily, paid_at__date__lte=today)
        .annotate(day=TruncDate('paid_at'))
        .values('day')
        .annotate(total=Sum('total_amount'))
        .order_by('day')
    )
    daily_sales_labels = [item['day'].strftime('%b %d') for item in daily_sales_qs]
    daily_sales_data = [float(item['total']) for item in daily_sales_qs]

    start_week = today - timedelta(weeks=7)
    weekly_orders_qs = (
        Order.objects
        .filter(created_at__date__gte=start_week)
        .annotate(week=TruncWeek('created_at'))
        .values('week')
        .annotate(total=Count('id'))
        .order_by('week')
    )
    weekly_order_labels = [item['week'].strftime('%b %d') for item in weekly_orders_qs]
    weekly_order_data = [item['total'] for item in weekly_orders_qs]

    low_stock_qs = (
        Product.objects
        .filter(stock__lte=models.F('reorder_level'))
        .order_by('stock', 'name')[:5]
    )
    low_stock_labels = [item.name for item in low_stock_qs]
    low_stock_data = [item.stock for item in low_stock_qs]

    payment_method_qs = (
        Payment.objects.values('payment_method')
        .annotate(total=Count('id'))
        .order_by('-total')
    )
    payment_method_labels = [item['payment_method'] for item in payment_method_qs]
    payment_method_data = [item['total'] for item in payment_method_qs]

    context = {
        'products': products,
        'transactions': transactions,
        'alerts': alerts,
        'orders': orders,
        'total_products': total_products,
        'low_stock_products': low_stock_products,
        'total_sales': total_sales,
        'pending_orders': pending_orders,
        'daily_sales_labels': json.dumps(daily_sales_labels),
        'daily_sales_data': json.dumps(daily_sales_data),
        'weekly_order_labels': json.dumps(weekly_order_labels),
        'weekly_order_data': json.dumps(weekly_order_data),
        'low_stock_labels': json.dumps(low_stock_labels),
        'low_stock_data': json.dumps(low_stock_data),
        'payment_method_labels': json.dumps(payment_method_labels),
        'payment_method_data': json.dumps(payment_method_data),
        'is_admin_mode': is_store_admin(request),
    }
    return render(request, 'store/dashboard.html', context)


def sales_report(request):
    if not require_store_admin(request):
        return redirect('food_menu')

    local_today = timezone.localdate()
    period = request.GET.get('period', 'daily').strip()
    start_date_str = request.GET.get('start_date', '').strip()
    end_date_str = request.GET.get('end_date', '').strip()

    if period == 'weekly':
        start_date = local_today - timedelta(days=6)
        end_date = local_today
    elif period == 'custom':
        try:
            start_date = timezone.datetime.strptime(start_date_str, '%Y-%m-%d').date() if start_date_str else local_today
            end_date = timezone.datetime.strptime(end_date_str, '%Y-%m-%d').date() if end_date_str else local_today
            if end_date < start_date:
                messages.error(request, 'End date cannot be earlier than start date.')
                return redirect('sales_report')
        except ValueError:
            messages.error(request, 'Invalid custom date range.')
            return redirect('sales_report')
    else:
        period = 'daily'
        start_date = local_today
        end_date = local_today

    tz = timezone.get_current_timezone()
    start_dt = timezone.make_aware(
        timezone.datetime.combine(start_date, timezone.datetime.min.time()),
        tz
    )
    end_dt = timezone.make_aware(
        timezone.datetime.combine(end_date + timedelta(days=1), timezone.datetime.min.time()),
        tz
    )

    payments = (
        Payment.objects
        .select_related('order')
        .prefetch_related('order__items__product')
        .filter(paid_at__gte=start_dt, paid_at__lt=end_dt)
        .order_by('-paid_at')
    )

    total_sales = payments.aggregate(total=Sum('total_amount'))['total'] or Decimal('0.00')
    cash_sales = payments.filter(payment_method='CASH').aggregate(total=Sum('total_amount'))['total'] or Decimal('0.00')
    gcash_sales = payments.filter(payment_method='GCASH').aggregate(total=Sum('total_amount'))['total'] or Decimal('0.00')
    maya_sales = payments.filter(payment_method='MAYA').aggregate(total=Sum('total_amount'))['total'] or Decimal('0.00')

    orders_count = payments.count()
    paid_order_ids = payments.values_list('order_id', flat=True)

    items_sold = (
        OrderItem.objects
        .filter(order_id__in=paid_order_ids)
        .aggregate(total_qty=Sum('quantity'))
    )['total_qty'] or 0

    top_products = (
        OrderItem.objects
        .filter(order_id__in=paid_order_ids)
        .values('product__name')
        .annotate(
            total_qty=Sum('quantity'),
            total_sales=Sum('subtotal')
        )
        .order_by('-total_qty', '-total_sales')
    )

    daily_breakdown = (
        payments
        .annotate(day=TruncDate('paid_at'))
        .values('day')
        .annotate(
            total_sales=Sum('total_amount'),
            total_orders=Count('id')
        )
        .order_by('day')
    )

    payment_records = []
    for payment in payments:
        item_names = ", ".join(
            [f"{item.product.name} x{item.quantity}" for item in payment.order.items.all()]
        )
        payment_records.append({
            'order_number': payment.order.order_number,
            'customer_name': payment.customer_name,
            'items': item_names,
            'payment_method': payment.payment_method,
            'amount_received': payment.amount_received,
            'change': payment.change,
            'total_amount': payment.total_amount,
            'paid_at': timezone.localtime(payment.paid_at),
        })

    context = {
        'period': period,
        'start_date': start_date,
        'end_date': end_date,
        'total_sales': total_sales,
        'cash_sales': cash_sales,
        'gcash_sales': gcash_sales,
        'maya_sales': maya_sales,
        'orders_count': orders_count,
        'items_sold': items_sold,
        'top_products': top_products,
        'daily_breakdown': daily_breakdown,
        'payment_records': payment_records,
        'is_admin_mode': is_store_admin(request),
    }
    return render(request, 'store/sales_report.html', context)


def product_list(request):
    if not require_store_admin(request):
        return redirect('food_menu')

    products = Product.objects.all().order_by('name')
    return render(request, 'store/products.html', {'products': products})


def transaction_list(request):
    if not require_store_admin(request):
        return redirect('food_menu')

    query = request.GET.get('q', '').strip()
    selected_date = request.GET.get('date', '').strip()

    transactions = Transaction.objects.select_related('product', 'rfid_user').order_by('-transaction_time')

    if query:
        transactions = transactions.filter(
            Q(product__name__icontains=query) |
            Q(source__icontains=query) |
            Q(rfid_user__full_name__icontains(query)) |
            Q(rfid_user__rfid_code__icontains(query))
        )

    if selected_date:
        transactions = transactions.filter(transaction_time__date=selected_date)

    context = {
        'transactions': transactions,
        'query': query,
        'selected_date': selected_date,
    }
    return render(request, 'store/transactions.html', context)


def alert_list(request):
    if not require_store_admin(request):
        return redirect('food_menu')

    query = request.GET.get('q', '').strip()
    selected_date = request.GET.get('date', '').strip()

    alerts = Alert.objects.order_by('-created_at')

    if query:
        alerts = alerts.filter(
            Q(message__icontains=query) |
            Q(alert_type__icontains=query)
        )

    if selected_date:
        alerts = alerts.filter(created_at__date=selected_date)

    context = {
        'alerts': alerts,
        'query': query,
        'selected_date': selected_date,
    }
    return render(request, 'store/alerts.html', context)


def food_menu(request):
    query = request.GET.get('q', '').strip()
    selected_category = request.GET.get('category', '').strip()

    all_products = Product.objects.filter(is_active=True).order_by('category', 'name')
    total_products = all_products.count()

    products = all_products

    if selected_category:
        products = products.filter(category__iexact=selected_category)

    if query:
        products = products.filter(
            Q(name__icontains=query) | Q(category__icontains=query)
        )

    category_data = (
        all_products.values('category')
        .annotate(total=Count('id'))
        .order_by('category')
    )

    cart = get_cart(request)
    _, _, total_quantity = build_cart_items(cart)

    return render(request, 'store/food_menu.html', {
        'products': products,
        'query': query,
        'selected_category': selected_category,
        'cart_count': total_quantity,
        'category_data': category_data,
        'total_products': total_products,
    })


def add_to_cart(request, product_id):
    product = get_object_or_404(Product, id=product_id, is_active=True)

    if request.method != 'POST':
        return redirect('food_menu')

    try:
        quantity = int(request.POST.get('quantity', 1))
    except ValueError:
        quantity = 1

    if quantity < 1:
        quantity = 1

    cart = get_cart(request)
    current_qty = int(cart.get(str(product.id), 0))
    new_qty = current_qty + quantity

    if new_qty > product.stock:
        messages.error(request, f'Only {product.stock} stock available for {product.name}.')
        return redirect('food_menu')

    cart[str(product.id)] = new_qty
    save_cart(request, cart)

    messages.success(request, f'{product.name} added to cart.')
    return redirect('food_menu')


def cart_view(request):
    cart = get_cart(request)
    items, grand_total, total_quantity = build_cart_items(cart)

    if request.method == 'POST':
        customer_name = request.POST.get('customer_name', '').strip()
        customer_name = customer_name if customer_name else 'Walk-in Customer'
        customer_email = request.POST.get('customer_email', '').strip()
        wants_email_updates = request.POST.get('wants_email_updates') == 'on'

        if not items:
            messages.error(request, 'Your cart is empty.')
            return redirect('food_menu')

        for item in items:
            if item['quantity'] > item['product'].stock:
                messages.error(
                    request,
                    f"Not enough stock for {item['product'].name}. Available: {item['product'].stock}."
                )
                return redirect('cart_view')

        with transaction.atomic():
            order = Order.objects.create(
                customer_name=customer_name,
                customer_email=customer_email,
                wants_email_updates=wants_email_updates,
                total_price=Decimal('0.00'),
                status='PENDING',
                stock_deducted=False
            )

            for item in items:
                OrderItem.objects.create(
                    order=order,
                    product=item['product'],
                    quantity=item['quantity'],
                    unit_price=item['unit_price']
                )

            order.update_total()

            Alert.objects.create(
                product=None,
                alert_type='NEW_ORDER',
                message=f'New multi-item order from {customer_name}. Order #{order.order_number} is waiting for preparation.'
            )

            send_telegram_message(
                f"New order received\n"
                f"Order: {order.order_number}\n"
                f"Customer: {order.customer_name}\n"
                f"Total: PHP {order.total_price}\n"
                f"Status: {order.status}"
            )

            send_admin_email(
                f"New Order Received - {order.order_number}",
                f"New order received.\n\n"
                f"Order Number: {order.order_number}\n"
                f"Customer: {order.customer_name}\n"
                f"Total: PHP {order.total_price}\n"
                f"Status: {order.status}"
            )

            if order.wants_email_updates and order.customer_email:
                send_customer_email(
                    f"Order Received - {order.order_number}",
                    f"Hello {order.customer_name},\n\n"
                    f"Your order has been received successfully.\n\n"
                    f"Order Number: {order.order_number}\n"
                    f"Total: PHP {order.total_price}\n"
                    f"Status: {order.status}\n\n"
                    f"We will send you another update once payment is recorded or your order is completed.\n\n"
                    f"Thank you for ordering.",
                    order.customer_email
                )

        request.session['cart'] = {}
        request.session['last_order_id'] = order.id
        request.session.modified = True

        messages.success(request, f'Order placed successfully for {customer_name}.')
        return redirect('order_success', order_id=order.id)

    return render(request, 'store/cart.html', {
        'cart_items': items,
        'grand_total': grand_total,
        'total_quantity': total_quantity,
    })


def track_order(request):
    searched = False
    order = None
    query = ''

    if request.method == 'POST':
        searched = True
        query = request.POST.get('order_number', '').strip().upper()

        if not query:
            messages.error(request, 'Please enter your order number.')
        else:
            order = Order.objects.prefetch_related('items__product').filter(order_number__iexact=query).first()
            if not order:
                messages.error(request, 'Order not found. Please check the order number and try again.')

    return render(request, 'store/track_order.html', {
        'order': order,
        'searched': searched,
        'query': query,
        'is_admin_mode': is_store_admin(request),
    })


def order_success(request, order_id):
    order = get_object_or_404(
        Order.objects.prefetch_related('items__product'),
        id=order_id
    )

    return render(request, 'store/order_success.html', {
        'order': order,
        'is_admin_mode': is_store_admin(request),
    })


def update_cart_item(request, product_id):
    if request.method != 'POST':
        return redirect('cart_view')

    cart = get_cart(request)
    product = get_object_or_404(Product, id=product_id, is_active=True)

    try:
        quantity = int(request.POST.get('quantity', 1))
    except ValueError:
        quantity = 1

    if quantity <= 0:
        quantity = 1

    if quantity > product.stock:
        messages.error(request, f'Only {product.stock} stock available for {product.name}.')
        return redirect('cart_view')

    cart[str(product.id)] = quantity
    save_cart(request, cart)
    messages.success(request, f'{product.name} quantity updated.')
    return redirect('cart_view')


def remove_cart_item(request, product_id):
    cart = get_cart(request)
    product = get_object_or_404(Product, id=product_id, is_active=True)

    if str(product.id) in cart:
        del cart[str(product.id)]

    save_cart(request, cart)
    messages.success(request, f'{product.name} removed from cart.')
    return redirect('cart_view')


def clear_cart(request):
    request.session['cart'] = {}
    request.session.modified = True
    messages.success(request, 'Cart cleared.')
    return redirect('cart_view')


def order_list(request):
    orders = Order.objects.prefetch_related('items__product').order_by('-created_at')
    return render(request, 'store/orders.html', {
        'orders': orders,
        'is_admin_mode': is_store_admin(request),
    })


def update_order_status(request, order_id, new_status):
    if not require_store_admin(request):
        return redirect('food_menu')

    order = get_object_or_404(Order.objects.prefetch_related('items__product'), id=order_id)
    valid_statuses = ['PENDING', 'PREPARING', 'COMPLETED', 'CANCELLED']

    if new_status not in valid_statuses:
        messages.error(request, 'Invalid order status.')
        return redirect('order_list')

    if new_status == 'PREPARING':
        if order.status == 'CANCELLED':
            Alert.objects.create(
                product=None,
                alert_type='NEW_ORDER',
                message=f'Cannot prepare cancelled order {order.order_number} for {order.customer_name}.'
            )
            messages.error(request, f'Cannot prepare cancelled order for {order.customer_name}.')
            return redirect('order_list')

        if not order.stock_deducted:
            for item in order.items.all():
                if item.product.stock < item.quantity:
                    Alert.objects.create(
                        product=item.product,
                        alert_type='LOW_STOCK',
                        message=f'Cannot prepare order {order.order_number}. Not enough stock for {item.product.name}.'
                    )
                    messages.error(request, f'Not enough stock for {item.product.name}.')
                    return redirect('order_list')

            for item in order.items.all():
                product = item.product
                product.stock -= item.quantity
                product.save()

                Transaction.objects.create(
                    product=product,
                    quantity=item.quantity,
                    total_price=item.subtotal,
                    source='ORDER'
                )

                if product.stock <= product.reorder_level:
                    create_alert_once(
                        product,
                        'LOW_STOCK',
                        f'Low stock: {product.name} has {product.stock} left.'
                    )

            order.stock_deducted = True

        order.status = 'PREPARING'
        order.save()

        Alert.objects.create(
            product=None,
            alert_type='NEW_ORDER',
            message=f'Order {order.order_number} for {order.customer_name} is now PREPARING.'
        )
        messages.success(request, f'Order for {order.customer_name} is now PREPARING.')
        return redirect('order_list')

    if new_status == 'COMPLETED':
        if order.status == 'CANCELLED':
            Alert.objects.create(
                product=None,
                alert_type='NEW_ORDER',
                message=f'Cannot complete cancelled order {order.order_number} for {order.customer_name}.'
            )
            messages.error(request, f'Cannot complete cancelled order for {order.customer_name}.')
            return redirect('order_list')

        if not order.stock_deducted:
            Alert.objects.create(
                product=None,
                alert_type='NEW_ORDER',
                message=f'Order {order.order_number} for {order.customer_name} must be prepared before completing.'
            )
            messages.error(request, f'Order for {order.customer_name} must be prepared first.')
            return redirect('order_list')

        order.status = 'COMPLETED'
        order.save()

        Alert.objects.create(
            product=None,
            alert_type='NEW_ORDER',
            message=f'Order {order.order_number} for {order.customer_name} is now COMPLETED.'
        )

        if order.wants_email_updates and order.customer_email:
            send_customer_email(
                f"Order Completed - {order.order_number}",
                f"Hello {order.customer_name},\n\n"
                f"Your order is now completed.\n\n"
                f"Order Number: {order.order_number}\n"
                f"Status: {order.status}\n\n"
                f"Thank you.",
                order.customer_email
            )

        messages.success(request, f'Order for {order.customer_name} is now COMPLETED.')
        return redirect('order_list')

    if new_status == 'CANCELLED':
        if order.status == 'COMPLETED':
            Alert.objects.create(
                product=None,
                alert_type='NEW_ORDER',
                message=f'Completed order {order.order_number} for {order.customer_name} cannot be cancelled.'
            )
            messages.error(request, f'Completed order for {order.customer_name} cannot be cancelled.')
            return redirect('order_list')

        if order.status == 'PREPARING' and order.stock_deducted:
            for item in order.items.all():
                product = item.product
                product.stock += item.quantity
                product.save()

            order.stock_deducted = False

        order.status = 'CANCELLED'
        order.save()

        Alert.objects.create(
            product=None,
            alert_type='NEW_ORDER',
            message=f'Order {order.order_number} for {order.customer_name} was CANCELLED.'
        )
        messages.success(request, f'Order for {order.customer_name} was CANCELLED.')
        return redirect('order_list')

    order.status = new_status
    order.save()
    messages.success(request, 'Order status updated.')
    return redirect('order_list')


def clear_all_alerts(request):
    Alert.objects.filter(is_read=False).update(is_read=True)
    messages.success(request, 'All unread alerts were cleared.')
    return redirect('dashboard')


def delete_all_alerts(request):
    deleted_count = Alert.objects.count()
    Alert.objects.all().delete()
    messages.success(request, f'All alerts deleted successfully. ({deleted_count} removed)')
    return redirect('dashboard')


def mark_alert_read(request, alert_id):
    alert = get_object_or_404(Alert, id=alert_id)
    alert.is_read = True
    alert.save()
    messages.success(request, 'Alert marked as read.')
    return redirect('dashboard')


def payment_list(request):
    if not require_store_admin(request):
        return redirect('food_menu')

    query = request.GET.get('q', '').strip()
    selected_method = request.GET.get('method', '').strip()
    selected_date = request.GET.get('date', '').strip()

    payments = Payment.objects.select_related('order').prefetch_related('order__items__product').order_by('-paid_at')

    if query:
        payments = payments.filter(
            Q(customer_name__icontains=query) |
            Q(order__order_number__icontains=query) |
            Q(order__items__product__name__icontains=query) |
            Q(payment_method__icontains=query) |
            Q(note__icontains=query)
        ).distinct()

    if selected_method:
        payments = payments.filter(payment_method=selected_method)

    if selected_date:
        payments = payments.filter(paid_at__date=selected_date)

    context = {
        'payments': payments,
        'query': query,
        'selected_method': selected_method,
        'selected_date': selected_date,
    }
    return render(request, 'store/payments.html', context)


def record_payment(request, order_id):
    if not require_store_admin(request):
        return redirect('food_menu')

    order = get_object_or_404(Order.objects.prefetch_related('items__product'), id=order_id)

    if order.status != 'COMPLETED':
        Alert.objects.create(
            product=None,
            alert_type='PAYMENT',
            message=f'Payment cannot be recorded for {order.customer_name}. Order must be COMPLETED first.'
        )
        messages.error(request, f'Payment cannot be recorded for {order.customer_name}. Order must be COMPLETED first.')
        return redirect('order_list')

    existing_payment = Payment.objects.filter(order=order).first()
    if existing_payment:
        Alert.objects.create(
            product=None,
            alert_type='PAYMENT',
            message=f'Payment already recorded for {order.customer_name}.'
        )
        messages.error(request, f'Payment already recorded for {order.customer_name}.')
        return redirect('payment_list')

    if request.method == 'POST':
        payment_method = request.POST.get('payment_method', 'CASH').strip().upper()
        amount_received_raw = request.POST.get('amount_received', '0').strip()
        customer_note = request.POST.get('customer_note', '').strip()

        valid_methods = {'CASH', 'GCASH', 'MAYA'}
        if payment_method not in valid_methods:
            return render(request, 'store/payment_form.html', {
                'order': order,
                'error': 'Invalid payment method selected.',
                'receipt_now': timezone.localtime(),
            })

        try:
            amount_received = Decimal(amount_received_raw)
        except (InvalidOperation, ValueError):
            return render(request, 'store/payment_form.html', {
                'order': order,
                'error': 'Invalid amount received.',
                'receipt_now': timezone.localtime(),
            })

        if amount_received < 0:
            return render(request, 'store/payment_form.html', {
                'order': order,
                'error': 'Amount received cannot be negative.',
                'receipt_now': timezone.localtime(),
            })

        total_amount = order.total_price

        if payment_method == 'CASH':
            if amount_received < total_amount:
                return render(request, 'store/payment_form.html', {
                    'order': order,
                    'error': 'Cash received is less than the total amount.',
                    'receipt_now': timezone.localtime(),
                })
            change = amount_received - total_amount
        else:
            if amount_received < total_amount:
                return render(request, 'store/payment_form.html', {
                    'order': order,
                    'error': 'Digital payment must cover the full amount.',
                    'receipt_now': timezone.localtime(),
                })
            change = Decimal('0.00')

        with transaction.atomic():
            try:
                Payment.objects.create(
                    order=order,
                    customer_name=order.customer_name,
                    total_amount=total_amount,
                    amount_received=amount_received,
                    change=change,
                    payment_method=payment_method,
                    note=customer_note
                )
            except IntegrityError:
                messages.error(request, f'Payment already exists for {order.customer_name}.')
                return redirect('payment_list')

            Alert.objects.create(
                product=None,
                alert_type='PAYMENT',
                message=f'Payment recorded for {order.customer_name} via {payment_method}.'
            )

            send_telegram_message(
                f"Payment recorded\n"
                f"Order: {order.order_number}\n"
                f"Customer: {order.customer_name}\n"
                f"Method: {payment_method}\n"
                f"Amount: PHP {total_amount}"
            )

            send_admin_email(
                f"Payment Recorded - {order.order_number}",
                f"Payment has been recorded.\n\n"
                f"Order Number: {order.order_number}\n"
                f"Customer: {order.customer_name}\n"
                f"Payment Method: {payment_method}\n"
                f"Amount: PHP {total_amount}\n"
                f"Change: PHP {change}"
            )

            if order.wants_email_updates and order.customer_email:
                send_customer_email(
                    f"Payment Successful - {order.order_number}",
                    f"Hello {order.customer_name},\n\n"
                    f"Your payment was successful.\n\n"
                    f"Order Number: {order.order_number}\n"
                    f"Payment Method: {payment_method}\n"
                    f"Amount: PHP {total_amount}\n"
                    f"Change: PHP {change}\n\n"
                    f"Thank you.",
                    order.customer_email
                )

        messages.success(request, f'Payment recorded successfully for {order.customer_name}.')
        return redirect('payment_list')

    return render(request, 'store/payment_form.html', {
        'order': order,
        'receipt_now': timezone.localtime(),
    })


@csrf_exempt
def calculate_change(request):
    if request.method == 'POST':
        try:
            data = json.loads(request.body)
            total_amount = Decimal(str(data.get('total_amount', 0)))
            amount_received = Decimal(str(data.get('amount_received', 0)))
            payment_method = data.get('payment_method', 'CASH')

            if payment_method == 'CASH':
                change = amount_received - total_amount
                if change < 0:
                    change = Decimal('0.00')
            else:
                change = Decimal('0.00')

            return JsonResponse({'change': float(change)})
        except Exception as e:
            return JsonResponse({'error': str(e)}, status=400)

    return JsonResponse({'error': 'Invalid request method'}, status=400)


@csrf_exempt
def record_rfid_transaction(request):
    if request.method == 'POST':
        try:
            data = json.loads(request.body)

            rfid_code = data.get('rfid_code')
            product_id = data.get('product_id')
            quantity = int(data.get('quantity', 1))

            if not product_id:
                return JsonResponse({'error': 'Product ID is required'}, status=400)

            if quantity <= 0:
                return JsonResponse({'error': 'Quantity must be greater than 0'}, status=400)

            user = RFIDUser.objects.filter(rfid_code=rfid_code).first()

            try:
                product = Product.objects.get(id=product_id)
            except Product.DoesNotExist:
                return JsonResponse({'error': 'Product not found'}, status=404)

            if product.stock < quantity:
                return JsonResponse({'error': 'Not enough stock'}, status=400)

            product.stock -= quantity
            product.save()

            total_price = product.price * quantity

            Transaction.objects.create(
                rfid_user=user,
                product=product,
                quantity=quantity,
                total_price=total_price,
                source='RFID'
            )

            if product.stock <= product.reorder_level:
                create_alert_once(
                    product,
                    'LOW_STOCK',
                    f'Low stock: {product.name} has {product.stock} left.'
                )

            return JsonResponse({
                'message': 'Transaction recorded successfully',
                'product': product.name,
                'remaining_stock': product.stock,
                'total_price': float(total_price)
            })

        except json.JSONDecodeError:
            return JsonResponse({'error': 'Invalid JSON format'}, status=400)
        except Exception as e:
            return JsonResponse({'error': str(e)}, status=500)

    return JsonResponse({'error': 'Invalid request method'}, status=400)