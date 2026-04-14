from django.db import migrations, models


def populate_order_numbers(apps, schema_editor):
    Order = apps.get_model('store', 'Order')
    for order in Order.objects.order_by('id'):
        order.order_number = f"ORD-{order.id:05d}"
        order.save(update_fields=['order_number'])


class Migration(migrations.Migration):

    dependencies = [
    ('store', '0006_payment_note_alter_payment_order'),
    ]

    operations = [
        migrations.RemoveField(
            model_name='order',
            name='product',
        ),
        migrations.RemoveField(
            model_name='order',
            name='quantity',
        ),
        migrations.AddField(
            model_name='order',
            name='order_number',
            field=models.CharField(blank=True, max_length=20, null=True),
        ),
        migrations.AlterField(
            model_name='order',
            name='total_price',
            field=models.DecimalField(decimal_places=2, default=0.00, max_digits=10),
        ),
        migrations.AlterField(
            model_name='payment',
            name='order',
            field=models.OneToOneField(
                on_delete=models.deletion.CASCADE,
                related_name='payment',
                to='store.order',
            ),
        ),
        migrations.CreateModel(
            name='OrderItem',
            fields=[
                ('id', models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name='ID')),
                ('quantity', models.IntegerField(default=1)),
                ('unit_price', models.DecimalField(decimal_places=2, max_digits=10)),
                ('subtotal', models.DecimalField(decimal_places=2, default=0.00, max_digits=10)),
                ('order', models.ForeignKey(on_delete=models.deletion.CASCADE, related_name='items', to='store.order')),
                ('product', models.ForeignKey(on_delete=models.deletion.CASCADE, to='store.product')),
            ],
        ),
        migrations.RunPython(populate_order_numbers, migrations.RunPython.noop),
        migrations.AlterField(
            model_name='order',
            name='order_number',
            field=models.CharField(blank=True, max_length=20, unique=True),
        ),
    ]