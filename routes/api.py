# routes/api.py - Mobile App API (aligned with variant-based inventory)
from flask import request, jsonify, g
from app import app, csrf, depreciated_value
from database import db
from models.core import (
    Shoe, ShoeSize, Sale, User,
    Product, ProductVariant
)
from models.customer import Customer, CustomerPurchase
from decorators import api_key_required
from database import limiter
from itsdangerous import URLSafeTimedSerializer
from werkzeug.security import check_password_hash
from datetime import datetime, timedelta
from sqlalchemy import func
import hashlib
import hmac


# ==================== AUTH ====================
@app.route('/api/v1/auth/login', methods=['POST'])
@csrf.exempt
@limiter.limit('5 per minute')
def api_login():
    """Mobile app login - returns token."""
    data = request.json or {}
    username = data.get('username', '')
    password = data.get('password', '')

    user = User.query.filter_by(username=username).first()
    if not user or not user.is_active or not check_password_hash(user.password_hash, password):
        return jsonify({'error': 'Invalid credentials'}), 401

    token = URLSafeTimedSerializer(app.config['SECRET_KEY']).dumps({'user_id': user.id})
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
    page = max(1, request.args.get('page', 1, type=int))
    per_page = min(100, max(1, request.args.get('per_page', 50, type=int)))
    shoes = Shoe.query.filter(Shoe.is_active == True).order_by(Shoe.id).paginate(page=page, per_page=per_page, error_out=False)
    products = Product.query.filter(Product.is_active == True).order_by(Product.id).paginate(page=page, per_page=per_page, error_out=False)
    items = [_shoe_to_dict(s) for s in shoes.items] + [_product_to_dict(p) for p in products.items]
    return jsonify({'items': items, 'page': page, 'per_page': per_page, 'shoe_pages': shoes.pages, 'product_pages': products.pages})


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

    shoe_size = ShoeSize.query.filter_by(id=size_id).with_for_update().first_or_404()
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
        db.session.flush()  # populate sale.id before it's used below

        if customer_id:
            customer = Customer.query.get(customer_id)
            if customer:
                customer.total_spent = (customer.total_spent or 0) + total_usd
                customer.total_orders = (customer.total_orders or 0) + 1
                customer.last_purchase = datetime.utcnow()
                customer.loyalty_points = (customer.loyalty_points or 0) + int(total_usd * 0.1)
                db.session.add(CustomerPurchase(
                    customer_id=customer.id, sale_id=sale.id, total_usd=total_usd
                ))

        db.session.commit()
    except Exception as e:
        db.session.rollback()
        return jsonify({'error': f'Sale could not be completed: {str(e)}'}), 500

    return jsonify({
        'status': 'success',
        'sale_id': sale.id,
        'total': total_usd,
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

    variant = ProductVariant.query.filter_by(id=variant_id).with_for_update().first_or_404()
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
        db.session.flush()  # populate sale.id before it's used below

        if customer_id:
            customer = Customer.query.get(customer_id)
            if customer:
                customer.total_spent = (customer.total_spent or 0) + total_usd
                customer.total_orders = (customer.total_orders or 0) + 1
                customer.last_purchase = datetime.utcnow()
                customer.loyalty_points = (customer.loyalty_points or 0) + int(total_usd * 0.1)
                db.session.add(CustomerPurchase(
                    customer_id=customer.id, sale_id=sale.id, total_usd=total_usd
                ))

        db.session.commit()
    except Exception as e:
        db.session.rollback()
        return jsonify({'error': f'Sale could not be completed: {str(e)}'}), 500

    return jsonify({
        'status': 'success',
        'sale_id': sale.id,
        'total': total_usd,
        'remaining_stock': variant.quantity
    })


# ==================== DASHBOARD ====================
@app.route('/api/v1/dashboard')
@api_key_required
def api_dashboard():
    """Return a bounded dashboard summary without loading full tables into Python.

    Financial/profit fields are owner-only. A shared API key is intentionally
    treated as non-owner because it identifies an integration, not a person.
    """
    today = datetime.utcnow().date()
    start = datetime(today.year, today.month, today.day)
    end = start + timedelta(days=1)

    sales_row = db.session.query(
        func.coalesce(func.sum(Sale.total_usd), 0),
        func.coalesce(func.sum(Sale.quantity), 0),
        func.coalesce(func.sum(Sale.profit_usd), 0),
        func.count(Sale.id)
    ).filter(Sale.date >= start, Sale.date < end).one()

    shoe_low = db.session.query(func.count(ShoeSize.id)).join(Shoe).filter(
        Shoe.is_active.is_(True), ShoeSize.quantity > 0, ShoeSize.quantity <= 10
    ).scalar() or 0
    product_low = db.session.query(func.count(ProductVariant.id)).join(Product).filter(
        Product.is_active.is_(True), ProductVariant.quantity > 0, ProductVariant.quantity <= 10
    ).scalar() or 0

    active_shoe_value = db.session.query(
        func.coalesce(func.sum(ShoeSize.cost_usd * ShoeSize.quantity), 0)
    ).join(Shoe).filter(Shoe.is_active.is_(True)).scalar() or 0
    active_product_value = db.session.query(
        func.coalesce(func.sum(ProductVariant.cost_usd * ProductVariant.quantity), 0)
    ).join(Product).filter(Product.is_active.is_(True)).scalar() or 0

    # Discontinued inventory is normally small; retain Python depreciation
    # here because the calculation is deliberately database-neutral across
    # SQLite and PostgreSQL. Active inventory—the hot path—is SQL aggregated.
    depreciating_value = 0.0
    for shoe in Shoe.query.filter(Shoe.is_active.is_(False)).yield_per(500):
        total = sum((s.cost_usd or 0) * (s.quantity or 0) for s in shoe.sizes)
        depreciating_value += depreciated_value(total, shoe.deactivated_at)
    for product in Product.query.filter(Product.is_active.is_(False)).yield_per(500):
        total = sum((v.cost_usd or 0) * (v.quantity or 0) for v in product.variants)
        depreciating_value += depreciated_value(total, product.deactivated_at)

    response = {
        'status': 'success',
        'today_sales': float(sales_row[0] or 0),
        'today_items': int(sales_row[1] or 0),
        'today_transactions': int(sales_row[3] or 0),
        'low_stock_count': int(shoe_low + product_low),
        'active_inventory_value': float(active_shoe_value + active_product_value),
        'depreciating_inventory_value': round(depreciating_value, 2),
    }

    api_user = getattr(g, 'api_user', None)
    if api_user and api_user.role and api_user.role.name == 'owner':
        response['profit'] = float(sales_row[2] or 0)
        response['inventory_value'] = float(active_shoe_value + active_product_value + depreciating_value)
    return jsonify(response)


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