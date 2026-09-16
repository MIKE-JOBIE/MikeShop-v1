# routes/api.py - Mobile App API (aligned with variant-based inventory)
from flask import request, jsonify
from app import app, csrf
from database import db
from models.core import (
    Shoe, ShoeSize, Sale, User,
    Product, ProductVariant
)
from models.customer import Customer
from decorators import api_key_required
from werkzeug.security import check_password_hash
from datetime import datetime, timedelta
from sqlalchemy import func
import hashlib
import hmac


# ==================== AUTH ====================
@app.route('/api/v1/auth/login', methods=['POST'])
@csrf.exempt
def api_login():
    """Mobile app login - returns token."""
    data = request.json or {}
    username = data.get('username', '')
    password = data.get('password', '')

    user = User.query.filter_by(username=username).first()
    if not user or not check_password_hash(user.password_hash, password):
        return jsonify({'error': 'Invalid credentials'}), 401

    token_data = f"{user.id}:{username}:{datetime.now().timestamp()}"
    token = hmac.new(
        app.config['SECRET_KEY'].encode(),
        token_data.encode(),
        hashlib.sha256
    ).hexdigest()

    return jsonify({
        'status': 'success',
        'token': token,
        'user': {
            'id': user.id,
            'username': user.username,
            'role': user.role.name
        }
    })


# ==================== HELPERS ====================
def _shoe_to_dict(s):
    return {
        'id': s.id,
        'type': 'shoe',
        'brand': s.brand,
        'model': s.model,
        'display_name': s.display_name(),
        'sizes': [
            {
                'id': size.id,
                'size': size.size,
                'quantity': size.quantity,
                'cost_usd': size.cost_usd,
                'sell_usd': size.sell_usd
            } for size in s.sizes
        ],
        'stock': s.get_total_quantity(),
        'sku': s.sku
    }


def _product_to_dict(p):
    return {
        'id': p.id,
        'type': 'product',
        'brand': p.brand,
        'model': p.model,
        'display_name': p.display_name(),
        'category': p.category,
        'variants': [
            {
                'id': v.id,
                'label': v.variant_label,
                'value': v.variant_value,
                'quantity': v.quantity,
                'cost_usd': v.cost_usd,
                'sell_usd': v.sell_usd
            } for v in p.variants
        ],
        'stock': p.get_total_quantity(),
        'sku': p.sku
    }


# ==================== INVENTORY ====================
@app.route('/api/v1/inventory')
@api_key_required
def api_inventory():
    """Return shoes + products (unified)."""
    shoes = Shoe.query.filter(Shoe.is_active == True).all()
    products = Product.query.filter(Product.is_active == True).all()

    return jsonify(
        [_shoe_to_dict(s) for s in shoes] +
        [_product_to_dict(p) for p in products]
    )


@app.route('/api/v1/inventory/<int:shoe_id>')
@api_key_required
def api_inventory_item(shoe_id):
    """Return a single shoe."""
    shoe = Shoe.query.get_or_404(shoe_id)
    return jsonify(_shoe_to_dict(shoe))


def resolve_sold_by(raw_username):
    """Only trust a sold_by value if it matches a real, active user —
    otherwise the mobile API (protected by one shared static key) lets
    anyone forge sales under any name, including the owner's, breaking
    the audit trail. Falls back to 'mobile_app' when it doesn't match."""
    if not raw_username:
        return 'mobile_app'
    user = User.query.filter_by(username=raw_username).first()
    if user and user.is_active:
        return user.username
    return 'mobile_app'


# ==================== SALES ====================
@app.route('/api/v1/sale', methods=['POST'])
@csrf.exempt
@api_key_required
def api_create_sale():
    """Record a shoe-size sale from mobile."""
    data = request.json or {}

    size_id = data.get('size_id')            # preferred
    customer_id = data.get('customer_id')
    username = resolve_sold_by(data.get('sold_by'))

    try:
        quantity = int(data.get('quantity', 0))
    except (TypeError, ValueError):
        return jsonify({'error': 'Invalid quantity'}), 400

    if not size_id:
        return jsonify({'error': 'size_id is required'}), 400

    shoe_size = ShoeSize.query.with_for_update().get_or_404(size_id)
    shoe = shoe_size.shoe

    if quantity <= 0:
        return jsonify({'error': 'Invalid quantity'}), 400
    if quantity > shoe_size.quantity:
        return jsonify({'error': f'Only {shoe_size.quantity} units available'}), 400

    total_usd = shoe_size.sell_usd * quantity
    profit_usd = (shoe_size.sell_usd - shoe_size.cost_usd) * quantity

    try:
        shoe_size.quantity -= quantity

        sale = Sale(
            shoe_id=shoe.id,
            shoe_size_id=shoe_size.id,
            size=shoe_size.size,
            quantity=quantity,
            total_usd=total_usd,
            profit_usd=profit_usd,
            sold_by=username,
            sale_type='mobile',
            customer_id=customer_id
        )
        db.session.add(sale)

        if customer_id:
            customer = Customer.query.get(customer_id)
            if customer:
                customer.total_spent = (customer.total_spent or 0) + total_usd
                customer.total_orders = (customer.total_orders or 0) + 1
                customer.last_purchase = datetime.utcnow()
                customer.loyalty_points = (customer.loyalty_points or 0) + int(total_usd * 0.1)

        db.session.commit()
    except Exception as e:
        db.session.rollback()
        return jsonify({'error': f'Sale could not be completed: {str(e)}'}), 500

    return jsonify({
        'status': 'success',
        'sale_id': sale.id,
        'total': total_usd,
        'profit': profit_usd,
        'remaining_stock': shoe_size.quantity
    })



@app.route('/api/v1/sale_product', methods=['POST'])
@csrf.exempt
@api_key_required
def api_create_product_sale():
    """Record a product-variant sale from mobile."""
    data = request.json or {}

    variant_id = data.get('variant_id')
    customer_id = data.get('customer_id')
    username = resolve_sold_by(data.get('sold_by'))

    try:
        quantity = int(data.get('quantity', 0))
    except (TypeError, ValueError):
        return jsonify({'error': 'Invalid quantity'}), 400

    if not variant_id:
        return jsonify({'error': 'variant_id is required'}), 400

    variant = ProductVariant.query.with_for_update().get_or_404(variant_id)
    product = variant.product

    if quantity <= 0:
        return jsonify({'error': 'Invalid quantity'}), 400
    if quantity > variant.quantity:
        return jsonify({'error': f'Only {variant.quantity} units available'}), 400

    total_usd = variant.sell_usd * quantity
    profit_usd = (variant.sell_usd - variant.cost_usd) * quantity

    try:
        variant.quantity -= quantity

        sale = Sale(
            product_id=product.id,
            product_variant_id=variant.id,
            size=variant.variant_value,
            quantity=quantity,
            total_usd=total_usd,
            profit_usd=profit_usd,
            sold_by=username,
            sale_type='mobile',
            customer_id=customer_id
        )
        db.session.add(sale)

        if customer_id:
            customer = Customer.query.get(customer_id)
            if customer:
                customer.total_spent = (customer.total_spent or 0) + total_usd
                customer.total_orders = (customer.total_orders or 0) + 1
                customer.last_purchase = datetime.utcnow()
                customer.loyalty_points = (customer.loyalty_points or 0) + int(total_usd * 0.1)

        db.session.commit()
    except Exception as e:
        db.session.rollback()
        return jsonify({'error': f'Sale could not be completed: {str(e)}'}), 500

    return jsonify({
        'status': 'success',
        'sale_id': sale.id,
        'total': total_usd,
        'profit': profit_usd,
        'remaining_stock': variant.quantity
    })


# ==================== DASHBOARD ====================
@app.route('/api/v1/dashboard')
@api_key_required
def api_dashboard():
    """Dashboard summary for mobile app."""
    today = datetime.utcnow().date()
    start = datetime(today.year, today.month, today.day)
    end = start + timedelta(days=1)

    today_sales = Sale.query.filter(
        Sale.date >= start,
        Sale.date < end
    ).all()

    total_sales = sum(s.total_usd for s in today_sales)
    total_items = sum(s.quantity for s in today_sales)
    profit = sum(s.profit_usd for s in today_sales)

    # Low stock: shoes + product variants
    low_stock_count = 0
    for shoe in Shoe.query.filter(Shoe.is_active == True).all():
        for size in shoe.sizes:
            if 0 < size.quantity <= 10:
                low_stock_count += 1
    for product in Product.query.filter(Product.is_active == True).all():
        for v in product.variants:
            if 0 < v.quantity <= 10:
                low_stock_count += 1

    # Inventory value
    shoe_value = sum(
        (size.cost_usd or 0) * (size.quantity or 0)
        for shoe in Shoe.query.all() for size in shoe.sizes
    )
    product_value = sum(
        (v.cost_usd or 0) * (v.quantity or 0)
        for product in Product.query.all() for v in product.variants
    )

    return jsonify({
        'total_sales': float(total_sales),
        'total_items': total_items,
        'transactions': len(today_sales),
        'profit': float(profit),
        'low_stock_items': low_stock_count,
        'inventory_value': float(shoe_value + product_value)
    })


# ==================== REPORTING ====================
@app.route('/api/v1/report/sales')
@api_key_required
def api_sales_report():
    days = int(request.args.get('days', 7))
    start_date = datetime.utcnow() - timedelta(days=days)

    sales = db.session.query(
        func.date(Sale.date).label('date'),
        func.sum(Sale.total_usd).label('total'),
        func.count(Sale.id).label('count')
    ).filter(Sale.date >= start_date).group_by(
        func.date(Sale.date)
    ).order_by(func.date(Sale.date)).all()

    return jsonify([{
        'date': str(s.date),
        'total': float(s.total or 0),
        'transactions': s.count
    } for s in sales])


@app.route('/api/v1/report/top-products')
@api_key_required
def api_top_products():
    days = int(request.args.get('days', 30))
    start_date = datetime.utcnow() - timedelta(days=days)

    # Shoes
    top_shoes = db.session.query(
        Shoe.brand, Shoe.model,
        func.sum(Sale.quantity).label('qty'),
        func.sum(Sale.total_usd).label('revenue')
    ).join(Sale, Sale.shoe_id == Shoe.id).filter(
        Sale.date >= start_date
    ).group_by(Shoe.id).all()

    # Products
    top_products = db.session.query(
        Product.brand, Product.model,
        func.sum(Sale.quantity).label('qty'),
        func.sum(Sale.total_usd).label('revenue')
    ).join(Sale, Sale.product_id == Product.id).filter(
        Sale.date >= start_date
    ).group_by(Product.id).all()

    combined = []
    for r in top_shoes:
        name = f"{r.brand} {r.model}".strip()
        combined.append({'name': name, 'quantity': int(r.qty or 0),
                        'revenue': float(r.revenue or 0)})
    for r in top_products:
        name = f"{r.brand} {r.model}".strip()
        combined.append({'name': name, 'quantity': int(r.qty or 0),
                        'revenue': float(r.revenue or 0)})

    combined.sort(key=lambda x: x['revenue'], reverse=True)
    return jsonify(combined[:10])


# ==================== CUSTOMERS ====================
@app.route('/api/v1/customers')
@api_key_required
def api_customers():
    customers = Customer.query.order_by(Customer.created_at.desc()).limit(100).all()
    return jsonify([{
        'id': c.id,
        'name': c.name,
        'email': c.email,
        'phone': c.phone,
        'total_spent': float(c.total_spent or 0),
        'loyalty_points': c.loyalty_points or 0,
        'tier': c.get_loyalty_tier()
    } for c in customers])