# routes/admin.py - COMPLETE VERSION WITH ALL FUNCTIONALITY
from flask import current_app, render_template, request, redirect, url_for, session, flash, send_file, jsonify, abort, make_response
from app import app, csrf
from database import db, socketio
from models.core import Role, User, Shoe, ShoeSize, Sale, Expense, AuditLog, Notification, Restock,Product, ProductVariant
from models.customer import Customer, CustomerPurchase
from models.store import StoreOrder
from decorators import login_required, role_required
from werkzeug.security import generate_password_hash, check_password_hash
from datetime import datetime, timedelta
from sqlalchemy import func
from sqlalchemy.exc import IntegrityError
import csv, io
import json
import math

PER_PAGE = 15
LOW_STOCK_THRESHOLD = 10
ALLOWED_CATEGORIES = {'shoe', 'clothing', 'grocery', 'electronics', 'other'}
MAX_TEXT_LENGTH = 150
MAX_VARIANT_LENGTH = 50
MAX_QUANTITY = 1_000_000
MAX_PRICE_USD = 10_000_000
OWNER_USERNAME = "MichaelJobieMusa"

def _norm(value):
    """Canonical display-safe whitespace normalization."""
    return " ".join(str(value or "").strip().casefold().split())

def _identity_token(value):
    """Canonical comparison token: ignores case, spaces and punctuation."""
    import re
    return re.sub(r'[^a-z0-9]+', '', _norm(value))

def _shoe_identity_key(brand, model):
    return f"{_identity_token(brand)}|{_identity_token(model)}"

def _product_identity_key(category, brand, model):
    return f"{_identity_token(category)}|{_identity_token(brand)}|{_identity_token(model)}"

def _variant_key(label, value):
    return f"{_identity_token(label)}|{_identity_token(value)}"

def _clean_text(value, max_length=MAX_TEXT_LENGTH):
    value = " ".join(str(value or "").strip().split())
    return value[:max_length]

def _valid_number(value, minimum=0.0, maximum=MAX_PRICE_USD):
    return math.isfinite(value) and minimum <= value <= maximum

def _variant_status(variants):
    quantities = [max(0, int(v.quantity or 0)) for v in variants]
    if not quantities or all(q == 0 for q in quantities):
        return 'out'
    if any(0 < q <= LOW_STOCK_THRESHOLD for q in quantities):
        return 'low'
    return 'good'

def _emit_notification(notification):
    try:
        if notification and notification.user_id:
            socketio.emit('new_notification', {
                'id': notification.id, 'message': notification.message,
                'category': notification.category,
                'unread_count': Notification.query.filter_by(user_id=notification.user_id, is_read=False).count()
            }, room=f'user_{notification.user_id}')
    except Exception:
        app.logger.exception('Notification socket delivery failed')

def _clear_low_stock_notifications(name, variant_value):
    needle = f"{name} {variant_value}".strip()
    Notification.query.filter(
        Notification.category == 'low_stock',
        Notification.message.ilike(f'%{name}%'),
        Notification.message.ilike(f'%{variant_value}%'),
        Notification.is_read == False
    ).update({'is_read': True}, synchronize_session=False)

def usd_to_sll(value):
    """Convert USD to SLL. Rate is read from app.config with 23000 fallback."""
    rate = app.config.get('USD_TO_SLL', 23000)
    return round((value or 0) * rate, 2)

def _apply_restock(variant, quantity, cost_usd, sell_usd):
    """
    Apply a restock to an existing ShoeSize OR ProductVariant.

    Rules:
      - cost_usd  -> weighted average: (old_qty*old_cost + new_qty*new_cost) / total_qty
      - sell_usd  -> newest value wins (overwrite)
      - quantity  -> summed

    Returns the new total quantity (useful for logging).
    Works on any object exposing: .quantity, .cost_usd, .sell_usd
    """
    old_qty = variant.quantity or 0
    old_cost = variant.cost_usd or 0.0
    total_qty = old_qty + quantity

    if total_qty > 0:
        variant.cost_usd = (old_qty * old_cost + quantity * cost_usd) / total_qty
    else:
        variant.cost_usd = cost_usd

    variant.quantity = total_qty
    variant.sell_usd = sell_usd
    return total_qty

def log(action, commit=False):
    from app import db
    from models.core import AuditLog
    try:
        db.session.add(AuditLog(user=session.get("username", "system"), action=action))
        if commit:
            db.session.commit()
    except Exception:
        db.session.rollback()
        raise

# ==================== DASHBOARD ====================

@app.route('/dashboard')
@login_required
def dashboard():
    inv_page = request.args.get('inv_page', 1, type=int)
    sales_page = request.args.get('sales_page', 1, type=int)

       # --- BUILD UNIFIED INVENTORY PAGE ---
    # Use a SQL UNION to page product identities in the database instead of
    # loading the entire inventory into Python. Variants for only the current
    # page are then loaded for rendering.
    from sqlalchemy import literal
    shoe_identity_q = db.session.query(
        Shoe.id.label('id'), literal('shoe').label('kind'),
        Shoe.category.label('category'), Shoe.brand.label('brand'), Shoe.model.label('model')
    ).filter(Shoe.is_active == True)
    product_identity_q = db.session.query(
        Product.id.label('id'), literal('product').label('kind'),
        Product.category.label('category'), Product.brand.label('brand'), Product.model.label('model')
    ).filter(Product.is_active == True)
    inventory_union = shoe_identity_q.union_all(product_identity_q).subquery()
    total_inventory = db.session.query(func.count()).select_from(inventory_union).scalar() or 0
    inventory_pages = max(1, math.ceil(total_inventory / PER_PAGE))
    inv_page = max(1, min(inv_page, inventory_pages))
    inv_rows = db.session.query(inventory_union).order_by(
        inventory_union.c.category.asc(), inventory_union.c.brand.asc(), inventory_union.c.model.asc()
    ).offset((inv_page - 1) * PER_PAGE).limit(PER_PAGE).all()

    shoe_ids = [r.id for r in inv_rows if r.kind == 'shoe']
    product_ids = [r.id for r in inv_rows if r.kind == 'product']
    shoes = Shoe.query.filter(Shoe.id.in_(shoe_ids)).order_by(Shoe.brand.asc(), Shoe.model.asc()).all() if shoe_ids else []
    products = Product.query.filter(Product.id.in_(product_ids)).order_by(Product.brand.asc(), Product.model.asc()).all() if product_ids else []
    shoe_map = {x.id: x for x in shoes}
    product_map = {x.id: x for x in products}

    all_inventory = []
    for row in inv_rows:
        if row.kind == 'shoe':
            shoe = shoe_map[row.id]
            sizes_list = [{'id': size.id, 'size': size.size, 'quantity': size.quantity, 'cost_usd': size.cost_usd, 'sell_usd': size.sell_usd} for size in shoe.sizes]
            shoe.sizes_json = json.dumps(sizes_list)
            all_inventory.append({
                'type': 'shoe', 'id': shoe.id, 'name': shoe.display_name(), 'brand': shoe.brand, 'model': shoe.model,
                'category': 'shoe', 'sizes': shoe.sizes, 'sizes_json': shoe.sizes_json,
                'total_quantity': shoe.get_total_quantity(), 'stock_status': _variant_status(shoe.sizes),
                'has_sizes': True, 'object': shoe
            })
        else:
            product = product_map[row.id]
            variants_list = [{'id': v.id, 'label': v.variant_label, 'value': v.variant_value, 'quantity': v.quantity, 'cost_usd': v.cost_usd, 'sell_usd': v.sell_usd} for v in product.variants]
            all_inventory.append({
                'type': 'product', 'id': product.id, 'name': product.display_name(), 'category': product.category,
                'sizes': product.variants, 'sizes_json': json.dumps(variants_list),
                'total_quantity': product.get_total_quantity(), 'stock_status': _variant_status(product.variants),
                'has_sizes': True, 'object': product
            })

    # Preserve the union's deterministic order.
    order = {(r.kind, r.id): i for i, r in enumerate(inv_rows)}
    all_inventory.sort(key=lambda x: order[(x['type'], x['id'])])

    # --- SALES PAGINATION ---
    sales = Sale.query.order_by(Sale.date.desc()).paginate(
        page=sales_page,
        per_page=PER_PAGE,
        error_out=False
    )

    total_sales, total_profit = db.session.query(
        func.coalesce(func.sum(Sale.total_usd), 0),
        func.coalesce(func.sum(Sale.profit_usd), 0)
    ).first()

    total_expense = db.session.query(func.coalesce(func.sum(Expense.amount_usd), 0)).scalar()
    net_profit = total_profit - total_expense

    chart_start = datetime.utcnow() - timedelta(days=365)
    daily_sales_rows = db.session.query(
        func.date(Sale.date), func.sum(Sale.total_usd)
    ).filter(Sale.date >= chart_start).group_by(func.date(Sale.date)).all()

    # str() the key — Postgres returns datetime.date, SQLite returns str.
    # JSON requires str/int/float/bool/None keys.
    daily_sales = {str(day): total for day, total in daily_sales_rows}

    from collections import defaultdict
    best_sellers_by_date = defaultdict(dict)

    # --- Shoes ---
    shoe_sales = (
        db.session.query(
            func.date(Sale.date).label("sale_date"),
            (Shoe.brand + " " + Shoe.model).label("name"),
            func.coalesce(func.sum(Sale.quantity), 0).label("total_qty")
        )
        .join(Shoe, Sale.shoe_id == Shoe.id)
        .filter(Sale.date >= chart_start)
        .group_by(func.date(Sale.date), Shoe.id)
        .all()
    )
    for row in shoe_sales:
        best_sellers_by_date[str(row.sale_date)][row.name] = int(row.total_qty)

    # --- Products ---
    product_sales = (
        db.session.query(
            func.date(Sale.date).label("sale_date"),
            (Product.brand + " " + Product.model).label("name"),
            Product.category.label("category"),
            func.coalesce(func.sum(Sale.quantity), 0).label("total_qty")
        )
        .join(Product, Sale.product_id == Product.id)
        .filter(Sale.date >= chart_start)
        .group_by(func.date(Sale.date), Product.id)
        .all()
    )
    for row in product_sales:
        day = str(row.sale_date)
        name = row.name
        # Disambiguate from a same-named shoe sold that day
        if name in best_sellers_by_date[day]:
            name = f"{name} · {row.category}"
        best_sellers_by_date[day][name] = (
            best_sellers_by_date[day].get(name, 0) + int(row.total_qty)
        )

    best_sellers = dict(best_sellers_by_date)

        # ---------- LOW STOCK: shoes + all products ----------
    low_stock_items = []

    # 1) Shoes — per size
    all_shoes = Shoe.query.filter(Shoe.is_active == True).limit(5000).all()
    for shoe in all_shoes:
        for size in shoe.sizes:
            if 0 < (size.quantity or 0) <= LOW_STOCK_THRESHOLD:
                low_stock_items.append({
                    'id': shoe.id,
                    'size_id': size.id,
                    'name': f"{shoe.brand} {shoe.model}",
                    'size': size.size,
                    'quantity': size.quantity,
                    'is_product': False,
                })

    # 2) Products — per variant
    all_products_for_alert = Product.query.filter(Product.is_active == True).limit(5000).all()
    for product in all_products_for_alert:
        for v in product.variants:
            if 0 < (v.quantity or 0) <= LOW_STOCK_THRESHOLD:
                low_stock_items.append({
                    'id': product.id,
                    'size_id': v.id,
                    'name': product.display_name(),
                    'size': v.variant_value,
                    'quantity': v.quantity,
                    'is_product': True,
                })

    low_stock_items.sort(key=lambda x: (x['quantity'], x['name']))
    low_stock_items = low_stock_items[:50]

    # ---------- HIGH DEMAND: shoes + all products ----------
    week_ago = datetime.utcnow() - timedelta(days=7)
    high_demand = {}

    # 1) Shoes
    shoe_demand = (
        db.session.query(
            (Shoe.brand + " " + Shoe.model).label("name"),
            func.sum(Sale.quantity).label("qty")
        )
        .join(Sale, Sale.shoe_id == Shoe.id)
        .filter(Sale.date >= week_ago, Shoe.is_active == True)
        .group_by(Shoe.id)
        .having(func.sum(Sale.quantity) >= 5)
        .all()
    )
    for row in shoe_demand:
        high_demand[row.name] = int(row.qty)

    # 2) Products
    product_demand = (
        db.session.query(
            (Product.brand + " " + Product.model).label("name"),
            Product.category.label("category"),
            func.sum(Sale.quantity).label("qty")
        )
        .join(Sale, Sale.product_id == Product.id)
        .filter(Sale.date >= week_ago, Product.is_active == True)
        .group_by(Product.id)
        .having(func.sum(Sale.quantity) >= 5)
        .all()
    )
    for row in product_demand:
        # Disambiguate from a same-named shoe in the high-demand set
        name = row.name
        if name in high_demand:
            name = f"{name} · {row.category}"
        high_demand[name] = high_demand.get(name, 0) + int(row.qty)

    # Same seven-day demand source used by the alert, exposed to the inventory
    # table so the two views cannot drift conceptually.
    shoe_demand_by_id = dict(db.session.query(
        Sale.shoe_id, func.sum(Sale.quantity)
    ).join(Shoe, Sale.shoe_id == Shoe.id).filter(
        Sale.date >= week_ago, Shoe.is_active == True, Sale.shoe_id.in_(shoe_ids or [-1])
    ).group_by(Sale.shoe_id).all())
    product_demand_by_id = dict(db.session.query(
        Sale.product_id, func.sum(Sale.quantity)
    ).join(Product, Sale.product_id == Product.id).filter(
        Sale.date >= week_ago, Product.is_active == True, Sale.product_id.in_(product_ids or [-1])
    ).group_by(Sale.product_id).all())
    for item in all_inventory:
        item['high_demand_qty'] = int(
            shoe_demand_by_id.get(item['id'], 0) if item['type'] == 'shoe'
            else product_demand_by_id.get(item['id'], 0)
        )
        if item['high_demand_qty'] < 5:
            item['high_demand_qty'] = 0

    high_demand = dict(sorted(high_demand.items(), key=lambda kv: kv[1], reverse=True)[:50])

    notifications = Notification.query.filter(
        (Notification.user_id == session['user_id']) | (Notification.user_id == None)
    ).order_by(Notification.created_at.desc()).limit(10).all()

    unread_alerts = Notification.query.filter(
        ((Notification.user_id == session['user_id']) | (Notification.user_id == None)) &
        (Notification.is_read == False)
    ).count()

    recent_sales = Sale.query.order_by(Sale.date.desc()).limit(5).all()
    recent_expenses = Expense.query.order_by(Expense.date.desc()).limit(5).all()
    recent_users = User.query.order_by(User.id.desc()).limit(5).all()
    recent_expense_logs = AuditLog.query.filter(
        AuditLog.action.like("Added expense:%")
    ).order_by(AuditLog.timestamp.desc()).limit(10).all()

    users = User.query.join(Role).filter(Role.name != "owner").order_by(User.username.asc()).limit(200).all()
    all_customers = Customer.query.order_by(Customer.name.asc()).limit(200).all()
    restock_shoes = Shoe.query.filter(Shoe.is_active == True).order_by(Shoe.brand.asc(), Shoe.model.asc()).limit(500).all()
    restock_products = Product.query.filter(Product.is_active == True).order_by(Product.brand.asc(), Product.model.asc()).limit(500).all()

    response = app.make_response(render_template(
        'admin/dashboard.html',
        active_page='dashboard',
        shoes=shoes,
        all_products=restock_products,
        restock_shoes=restock_shoes,
        sales=sales.items,
        inv_page=inv_page,
        sales_page=sales_page,
        total_sales_pages=sales.pages,
        total_sales_usd=total_sales,
        total_sales_sll=usd_to_sll(total_sales),
        total_profit_usd=total_profit,
        total_profit_sll=usd_to_sll(total_profit),
        total_expense_usd=total_expense,
        total_expense_sll=usd_to_sll(total_expense),
        net_profit_usd=net_profit,
        net_profit_sll=usd_to_sll(net_profit),
        daily_sales=daily_sales,
        best_sellers=best_sellers,
        low_stock=low_stock_items,
        high_demand=high_demand,
        alerts_count=unread_alerts,
        notifications=notifications,
        unread_alerts=unread_alerts,
        LOW_STOCK_THRESHOLD=LOW_STOCK_THRESHOLD,
        recent_sales=recent_sales,
        recent_expenses=recent_expenses,
        recent_expense_logs=recent_expense_logs,
        recent_users=recent_users,
        users=users,
        customers=all_customers,
        role=session['role'],
        username=session['username'],
        usd_to_sll_rate=app.config.get('USD_TO_SLL', 23000),
        all_inventory=all_inventory,
        total_inventory=total_inventory,
        total_inventory_pages=inventory_pages
    ))
    response.headers['Cache-Control'] = 'no-store, no-cache, must-revalidate, max-age=0'
    response.headers['Pragma'] = 'no-cache'
    return response

# ==================== PRODUCT DUPLICATE CHECK ====================

@app.route('/api/check_product_exists', methods=['GET'])
@login_required
@role_required('owner', 'admin')
def check_product_exists():
    category = _norm(request.args.get('category', 'shoe'))
    brand = _clean_text(request.args.get('brand', ''))
    model = _clean_text(request.args.get('model', ''))
    if category not in ALLOWED_CATEGORIES or not brand:
        return jsonify({'exists': False})
    if category == 'shoe':
        exists = db.session.query(Shoe.id).filter(
            Shoe.identity_key == _shoe_identity_key(brand, model)
        ).first()
        return jsonify({'exists': bool(exists), 'kind': 'shoe', 'id': exists[0] if exists else None})
    exists = db.session.query(Product.id).filter(
        Product.identity_key == _product_identity_key(category, brand, model)
    ).first()
    return jsonify({'exists': bool(exists), 'kind': 'product', 'id': exists[0] if exists else None})

# ==================== ADD NEW PRODUCT ====================

@app.route('/add_product', methods=['POST'])
@login_required
@role_required('owner', 'admin')
def add_product():
    from sqlalchemy import func as sa_func

    category = _norm(request.form.get('category', 'shoe'))
    brand = _clean_text(request.form.get('brand', ''))
    model = _clean_text(request.form.get('model', ''))
    variant_value = _clean_text(request.form.get('variant_value', ''), MAX_VARIANT_LENGTH)

    try:
        quantity = int(request.form.get('quantity', 0) or 0)
        cost_usd = float(request.form.get('cost_usd', 0) or 0)
        sell_usd = float(request.form.get('sell_usd', 0) or 0)
    except (TypeError, ValueError):
        flash("Please enter valid numeric values.")
        return redirect(url_for('dashboard'))

    image_url = request.form.get('image_url', '').strip()[:500]
    sku = _clean_text(request.form.get('sku', ''), 50) or None

    # ---------- Validation ----------
    if category not in ALLOWED_CATEGORIES:
        flash('Invalid product category.')
        return redirect(url_for('dashboard'))
    if not brand:
        flash("Product Name / Brand is required.")
        return redirect(url_for('dashboard'))

    if sku and (Shoe.query.filter_by(sku=sku).first()
                or Product.query.filter_by(sku=sku).first()):
        flash(f"⚠️ SKU '{sku}' is already in use by another product.")
        return redirect(url_for('dashboard'))

    if any(sep in variant_value for sep in (',', ';')):
        flash(
            "⚠️ Enter ONE variant at a time (e.g. size 42). "
            "To add multiple sizes, submit the form again or use Restock."
        )
        return redirect(url_for('dashboard'))

    if not variant_value:
        flash("Please provide a size / variant value.")
        return redirect(url_for('dashboard'))

    if quantity < 0 or quantity > MAX_QUANTITY:
        flash(f'Quantity must be between 0 and {MAX_QUANTITY:,}.')
        return redirect(url_for('dashboard'))
    if not _valid_number(cost_usd) or not _valid_number(sell_usd):
        flash('Price must be a finite value between 0 and 10,000,000 USD.')
        return redirect(url_for('dashboard'))

    # ---------- SHOE ----------
    if category == 'shoe':
        if not model:
            flash("Model is required for shoes.")
            return redirect(url_for('dashboard'))

        shoe_match = Shoe.query.filter(Shoe.identity_key == _shoe_identity_key(brand, model)).first()

        if shoe_match:
            flash(
                f"⚠️ '{brand} {model}' already exists. "
                f"Use the Restock section below to add a new size."
            )
            return redirect(url_for('dashboard', _anchor='restock-section',
                                    prefill_shoe=shoe_match.id))

        shoe = Shoe(brand=brand, model=model, identity_key=_shoe_identity_key(brand, model), category='shoe',
                    image_url=image_url or None, sku=sku)
        try:
            db.session.add(shoe)
            db.session.flush()
            db.session.add(ShoeSize(
                shoe_id=shoe.id, size=variant_value, variant_key=_variant_key('size', variant_value), quantity=quantity,
                cost_usd=cost_usd, sell_usd=sell_usd
            ))
            db.session.commit()
        except IntegrityError:
            db.session.rollback()
            flash("⚠️ Could not save — duplicate SKU or brand+model.")
            return redirect(url_for('dashboard'))

        log(f"Added shoe {brand} {model} size {variant_value}")
        flash(f"✅ {brand} {model} added with size {variant_value}.")
        return redirect(url_for('dashboard'))

    # ---------- OTHER CATEGORIES ----------
    product_match = Product.query.filter(Product.identity_key == _product_identity_key(category, brand, model)).first()

    if product_match:
        flash(
            f"⚠️ '{brand} {model}' already exists in {category}. "
            f"Use the Restock section below to add a variant."
        )
        return redirect(url_for('dashboard', _anchor='restock-section',
                                prefill_product=product_match.id))

    # Build attributes (only for non-identifying info)
    attributes = {}
    if category == 'grocery':
        attributes['expiry'] = request.form.get('expiry', '').strip()
    elif category == 'electronics':
        attributes['specs'] = request.form.get('specs', '').strip()
    elif category == 'other':
        attributes['description'] = request.form.get('description', '').strip()

    product = Product(
        brand=brand, model=model, category=category,
        identity_key=_product_identity_key(category, brand, model),
        image_url=image_url or None, sku=sku
    )
    product.set_attributes(attributes)

    variant_label = {
        'clothing': 'size',
        'grocery': 'weight',
        'electronics': 'color/storage',
        'other': 'variant',
    }.get(category, 'variant')

    try:
        db.session.add(product)
        db.session.flush()
        db.session.add(ProductVariant(
            product_id=product.id,
            variant_label=variant_label,
            variant_value=variant_value,
            variant_key=_variant_key(variant_label, variant_value),
            quantity=quantity,
            cost_usd=cost_usd,
            sell_usd=sell_usd
        ))
        db.session.commit()
    except IntegrityError:
        db.session.rollback()
        flash("⚠️ Could not save — duplicate SKU or brand+model for this category.")
        return redirect(url_for('dashboard'))

    log(f"Added {category} '{brand} {model}' variant {variant_value}")
    flash(f"✅ {brand} {model} ({category}) added with {variant_value}.")
    return redirect(url_for('dashboard'))

# ==================== RESTOCK (ADD/UPDATE SIZE) ====================

@app.route('/restock', methods=['POST'])
@login_required
@role_required('owner', 'admin')
def restock():
    """Simplified restock: works for both Shoe and Product via one combined dropdown."""
    target = request.form.get('restock_target', '').strip()
    variant_value = _clean_text(request.form.get('size', ''), MAX_VARIANT_LENGTH)

    try:
        quantity = int(request.form.get('quantity', 0))
        cost_usd = float(request.form.get('cost_usd', 0))
        sell_usd = float(request.form.get('sell_usd', 0))
    except (TypeError, ValueError):
        flash("Invalid restock values.")
        return redirect(url_for('dashboard'))

    if quantity <= 0 or quantity > MAX_QUANTITY:
        flash(f'Restock quantity must be between 1 and {MAX_QUANTITY:,}.')
        return redirect(url_for('dashboard', _anchor='restock-section'))

    if ':' not in target:
        flash("Please select a product to restock.")
        return redirect(url_for('dashboard'))

    if not variant_value or quantity <= 0 or not _valid_number(cost_usd) or not _valid_number(sell_usd):
        flash("Please fill all fields correctly.")
        return redirect(url_for('dashboard'))

    kind, id_str = target.split(':', 1)
    try:
        target_id = int(id_str)
    except ValueError:
        flash("Invalid product selection.")
        return redirect(url_for('dashboard'))

    supplier = _clean_text(request.form.get('supplier', ''), 100)

        # ----- SHOE -----
    if kind == 'shoe':
        shoe = Shoe.query.filter_by(id=target_id, is_active=True).with_for_update().first_or_404()
        existing = ShoeSize.query.filter_by(shoe_id=shoe.id).with_for_update().all()
        existing = next((v for v in existing if _norm(v.size) == _norm(variant_value)), None)
        if existing:
            _apply_restock(existing, quantity, cost_usd, sell_usd)
            size_obj = existing
            action_msg = f"updated size {variant_value} (+{quantity})"
        else:
            size_obj = ShoeSize(
                shoe_id=shoe.id, size=variant_value, variant_key=_variant_key('size', variant_value), quantity=quantity,
                cost_usd=cost_usd, sell_usd=sell_usd
            )
            db.session.add(size_obj)
            db.session.flush()                    # ← ensures size_obj.id is populated
            action_msg = f"added new size {variant_value} ({quantity} units)"

        db.session.add(Restock(
            shoe_id=shoe.id, shoe_size_id=size_obj.id,
            size=variant_value, quantity=quantity,
            cost_usd=cost_usd, sell_usd=sell_usd, supplier=supplier
        ))
        log(f"Restocked {shoe.brand} {shoe.model}: {action_msg}")
        try:
            db.session.commit()
        except IntegrityError:
            db.session.rollback()
            flash('That size already exists. Refresh the dashboard and restock the existing size.')
            return redirect(url_for('dashboard', _anchor='restock-section'))
        if size_obj.quantity > LOW_STOCK_THRESHOLD:
            _clear_low_stock_notifications(f'{shoe.brand} {shoe.model}', variant_value)
            db.session.commit()
        flash(f"✅ {shoe.brand} {shoe.model} — {action_msg}.")
        return redirect(url_for('dashboard'))

        # ----- PRODUCT -----
    if kind == 'product':
        product = Product.query.filter_by(id=target_id, is_active=True).with_for_update().first_or_404()
        existing = ProductVariant.query.filter_by(product_id=product.id).with_for_update().all()
        existing = next((v for v in existing if _norm(v.variant_value) == _norm(variant_value)), None)
        if existing:
            _apply_restock(existing, quantity, cost_usd, sell_usd)
            variant_obj = existing
            action_msg = f"updated {variant_value} (+{quantity})"
        else:
            # Match the label to the product's category — same map as add_product()
            variant_label = {
                'clothing': 'size',
                'grocery': 'weight',
                'electronics': 'color/storage',
                'other': 'variant',
            }.get(product.category, 'variant')

            variant_obj = ProductVariant(
                product_id=product.id,
                variant_label=variant_label,
                variant_value=variant_value,
                variant_key=_variant_key(variant_label, variant_value),
                quantity=quantity,
                cost_usd=cost_usd,
                sell_usd=sell_usd
            )
            db.session.add(variant_obj)
            db.session.flush()                    # ← ensures variant_obj.id is populated
            action_msg = f"added new variant {variant_value} ({quantity} units)"

        db.session.add(Restock(
            product_id=product.id, product_variant_id=variant_obj.id,
            size=variant_value, quantity=quantity,
            cost_usd=cost_usd, sell_usd=sell_usd, supplier=supplier
        ))
        log(f"Restocked {product.display_name()}: {action_msg}")
        try:
            db.session.commit()
        except IntegrityError:
            db.session.rollback()
            flash('That variant already exists. Refresh the dashboard and restock the existing variant.')
            return redirect(url_for('dashboard', _anchor='restock-section'))
        if variant_obj.quantity > LOW_STOCK_THRESHOLD:
            _clear_low_stock_notifications(product.display_name(), variant_value)
            db.session.commit()
        flash(f"✅ {product.display_name()} — {action_msg}.")
        return redirect(url_for('dashboard'))

    flash("Invalid product type.")
    return redirect(url_for('dashboard'))

# ==================== DISCONTINUE / REACTIVATE INVENTORY ITEMS ====================
# Marking an item inactive stops it counting toward active inventory and
# low-stock/high-demand alerts, and starts its remaining stock value
# depreciating (see depreciated_value() in app.py) instead of either
# fully counting it forever or dropping it silently.

@app.route('/toggle_shoe_active/<int:shoe_id>', methods=['POST'])
@login_required
@role_required('owner', 'admin')
def toggle_shoe_active(shoe_id):
    shoe = Shoe.query.get_or_404(shoe_id)
    try:
        shoe.is_active = not shoe.is_active
        shoe.deactivated_at = None if shoe.is_active else datetime.utcnow()
        state = "reactivated" if shoe.is_active else "discontinued"
        log(f"{state.capitalize()} shoe {shoe.brand} {shoe.model}")
        db.session.commit()
        flash(f"{shoe.brand} {shoe.model} {state}.")
    except Exception:
        db.session.rollback()
        flash("Unable to update this item's status.")
    return redirect(url_for('dashboard'))

@app.route('/toggle_product_active/<int:product_id>', methods=['POST'])
@login_required
@role_required('owner', 'admin')
def toggle_product_active(product_id):
    product = Product.query.get_or_404(product_id)
    try:
        product.is_active = not product.is_active
        product.deactivated_at = None if product.is_active else datetime.utcnow()
        state = "reactivated" if product.is_active else "discontinued"
        log(f"{state.capitalize()} product {product.display_name()}")
        db.session.commit()
        flash(f"{product.display_name()} {state}.")
    except Exception:
        db.session.rollback()
        flash("Unable to update this item's status.")
    return redirect(url_for('dashboard'))

# ==================== SELL SHOE ====================

@app.route('/sell/<int:size_id>', methods=['POST'])
@login_required
@role_required('owner', 'admin', 'staff')
def sell(size_id):
    shoe_size = ShoeSize.query.with_for_update().filter_by(id=size_id).first_or_404()
    shoe = shoe_size.shoe
    if not shoe.is_active:
        abort(409, description='This product is inactive and cannot be sold.')
    customer_id = request.form.get('customer_id')
    customer = None
    if customer_id and customer_id != '':
        try:
            customer_id = int(customer_id)
            customer = Customer.query.get(customer_id)
        except (TypeError, ValueError):
            customer_id = None
            customer = None
    else:
        customer_id = None

    try:
        qty = int(request.form.get('quantity', 0))
    except (TypeError, ValueError):
        flash("Invalid quantity.")
        return redirect(url_for('dashboard'))

    if qty <= 0:
        flash("Quantity must be greater than zero.")
        return redirect(url_for('dashboard'))

    if qty > shoe_size.quantity:
        flash(f"Only {shoe_size.quantity} units available in size {shoe_size.size}.")
        return redirect(url_for('dashboard'))

    try:
        shoe_size.quantity -= qty
        total_usd = shoe_size.sell_usd * qty
        profit_usd = (shoe_size.sell_usd - shoe_size.cost_usd) * qty

        sale = Sale(
            shoe_id=shoe.id,
            shoe_size_id=shoe_size.id,
            size=shoe_size.size,
            quantity=qty,
            total_usd=total_usd,
            profit_usd=profit_usd,
            sold_by=session['username'],
            customer_id=customer_id
        )
        db.session.add(sale)
        db.session.flush()  # populate sale.id before it's used below

        if customer:
            customer.total_spent = (customer.total_spent or 0) + total_usd
            customer.total_orders = (customer.total_orders or 0) + 1
            customer.last_purchase = datetime.utcnow()
            customer.loyalty_points = (customer.loyalty_points or 0) + int(total_usd * 0.1)
            db.session.add(CustomerPurchase(
                customer_id=customer.id, sale_id=sale.id, total_usd=total_usd
            ))

        sale_notification = Notification(
            message=f"Sold {qty} - {shoe.brand} {shoe.model} (Size {shoe_size.size})",
            category="sale", user_id=session['user_id']
        )
        db.session.add(sale_notification)
        low_notification = None
        if shoe_size.quantity <= LOW_STOCK_THRESHOLD and shoe_size.quantity > 0:
            low_notification = Notification(
                message=f"Low stock: {shoe.brand} {shoe.model} size {shoe_size.size} ({shoe_size.quantity} left)",
                category="low_stock", user_id=session['user_id']
            )
            db.session.add(low_notification)

        log(f"Sold {qty} of {shoe.brand} {shoe.model} size {shoe_size.size}")
        db.session.commit()
        _emit_notification(sale_notification)
        if low_notification:
            _emit_notification(low_notification)
        flash(f"✅ Sale: {qty} × {shoe.brand} {shoe.model} (Size {shoe_size.size})"
              + (f" for {customer.name}" if customer else ""))
    except Exception as e:
        db.session.rollback()
        app.logger.exception('Sale transaction failed')
        flash('The sale could not be completed. No inventory was changed.')

    return redirect(url_for('dashboard'))


# ==================== SELL PRODUCT (variant-aware) ====================

@app.route('/sell_product/<int:product_id>', methods=['POST'])
@login_required
@role_required('owner', 'admin', 'staff')
def sell_product(product_id):
    product = Product.query.filter_by(id=product_id, is_active=True).first_or_404()
    customer_id = request.form.get('customer_id')
    variant_id = request.form.get('variant_id', type=int)

    customer = None
    if customer_id and customer_id != '':
        try:
            customer_id = int(customer_id)
            customer = Customer.query.get(customer_id)
        except (TypeError, ValueError):
            customer_id = None
            customer = None
    else:
        customer_id = None

    # Resolve variant (row-locked to prevent overselling under concurrency)
    variant = None
    if variant_id:
        variant = ProductVariant.query.with_for_update().filter_by(
            id=variant_id, product_id=product.id
        ).first()

    if variant is None:
        variant = ProductVariant.query.with_for_update().filter(
            ProductVariant.product_id == product.id,
            ProductVariant.quantity > 0
        ).order_by(ProductVariant.id.asc()).first()
        if variant is None:
            flash(f"⚠️ {product.display_name()} is out of stock.")
            return redirect(url_for('dashboard'))

    try:
        qty = int(request.form.get('quantity', 0))
    except (TypeError, ValueError):
        flash("Invalid quantity.")
        return redirect(url_for('dashboard'))

    if qty <= 0:
        flash("Quantity must be greater than zero.")
        return redirect(url_for('dashboard'))

    if qty > variant.quantity:
        flash(f"Only {variant.quantity} units available in {variant.variant_value}.")
        return redirect(url_for('dashboard'))

    try:
        variant.quantity -= qty
        total_usd = variant.sell_usd * qty
        profit_usd = (variant.sell_usd - variant.cost_usd) * qty

        sale = Sale(
            product_id=product.id,
            product_variant_id=variant.id,
            size=variant.variant_value,
            quantity=qty,
            total_usd=total_usd,
            profit_usd=profit_usd,
            sold_by=session['username'],
            customer_id=customer_id,
            sale_type='product'
        )
        db.session.add(sale)
        db.session.flush()  # populate sale.id before it's used below

        if customer:
            customer.total_spent = (customer.total_spent or 0) + total_usd
            customer.total_orders = (customer.total_orders or 0) + 1
            customer.last_purchase = datetime.utcnow()
            customer.loyalty_points = (customer.loyalty_points or 0) + int(total_usd * 0.1)
            db.session.add(CustomerPurchase(
                customer_id=customer.id, sale_id=sale.id, total_usd=total_usd
            ))

        sale_notification = Notification(
            message=f"Sold {qty} - {product.display_name()} ({variant.variant_value})",
            category="sale", user_id=session['user_id']
        )
        db.session.add(sale_notification)
        low_notification = None
        if variant.quantity <= LOW_STOCK_THRESHOLD and variant.quantity > 0:
            low_notification = Notification(
                message=f"Low stock: {product.display_name()} {variant.variant_value} ({variant.quantity} left)",
                category="low_stock", user_id=session['user_id']
            )
            db.session.add(low_notification)

        log(f"Sold {qty} of {product.display_name()} ({variant.variant_value})")
        db.session.commit()
        _emit_notification(sale_notification)
        if low_notification:
            _emit_notification(low_notification)

        flash(f"✅ Sale: {qty} × {product.display_name()} ({variant.variant_value})"
              + (f" for {customer.name}" if customer else ""))
    except Exception as e:
        db.session.rollback()
        app.logger.exception('Sale transaction failed')
        flash('The sale could not be completed. No inventory was changed.')

    return redirect(url_for('dashboard'))

# ==================== EXPENSE ====================

@app.route('/add_expense', methods=['POST'])
@login_required
@role_required('owner', 'admin')
def add_expense():
    title = request.form.get('title', '').strip()
    try:
        amount_usd = float(request.form.get('amount_usd', 0))
    except (TypeError, ValueError):
        flash("Please enter a valid expense amount.")
        return redirect(url_for('dashboard'))

    if not title or amount_usd <= 0:
        flash("Expense title and amount are required.")
        return redirect(url_for('dashboard'))

    try:
        db.session.add(Expense(title=title, amount_usd=amount_usd))
        msg = f"Expense added: {title}"
        expense_notification = Notification(message=msg, category="expense", user_id=session['user_id'])
        db.session.add(expense_notification)
        log(f"Added expense: {title} (${amount_usd:.2f})")
        db.session.commit()
        _emit_notification(expense_notification)
        flash("Expense added successfully.")
    except Exception:
        db.session.rollback()
        flash("Unable to save the expense.")
    
    return redirect(url_for('dashboard'))

# ==================== STAFF MANAGEMENT ====================

@app.route('/add_staff', methods=['POST'])
@login_required
@role_required('owner')
def add_staff():
    username = request.form.get('username', '').strip()
    password = request.form.get('password', '').strip()
    role_name = request.form.get('role', '').strip().lower()

    if not username or not password:
        flash("Username and password are required.")
        return redirect(url_for('staff_list'))

    if username == OWNER_USERNAME:
        flash("Owner account cannot be modified.")
        return redirect(url_for('staff_list'))

    min_password = current_app.config.get('MIN_PASSWORD_LENGTH', 12)
    if len(password) < min_password:
        flash(f"Password must be at least {min_password} characters.")
        return redirect(url_for('staff_list'))

    if role_name not in ('admin', 'staff'):
        flash("Invalid staff role.")
        return redirect(url_for('staff_list'))

    if User.query.filter_by(username=username).first():
        flash("User already exists.")
        return redirect(url_for('staff_list'))

    role = Role.query.filter_by(name=role_name).first()
    if not role:
        flash("Selected role does not exist.")
        return redirect(url_for('staff_list'))

    try:
        user = User(username=username, password_hash=generate_password_hash(password), role=role)
        db.session.add(user)
        msg = f"New user created: {username}"
        user_notification = Notification(message=msg, category="user", user_id=session['user_id'])
        db.session.add(user_notification)
        log(f"Created user {username}")
        db.session.commit()
        _emit_notification(user_notification)
        flash(f"Staff account '{username}' created successfully.")
    except Exception:
        db.session.rollback()
        flash("Unable to create the staff account.")
    
    return redirect(url_for('staff_list'))

@app.route("/staff_list")
@login_required
@role_required("owner")
def staff_list():
    page = request.args.get('page', 1, type=int)
    users = User.query.join(Role).filter(Role.name != "owner").order_by(User.username.asc()).paginate(page=page, per_page=PER_PAGE, error_out=False)
    response = make_response(render_template(
        "admin/staff_list.html",
        users=users,
        role=session["role"],
        username=session["username"],
        active_page='staff'
    ))
    response.headers['Cache-Control'] = 'no-store, no-cache, must-revalidate, max-age=0'
    response.headers['Pragma'] = 'no-cache'
    response.headers['Expires'] = '0'
    return response

@app.route("/update_role/<int:user_id>", methods=["POST"])
@login_required
@role_required("owner")
def update_role(user_id):
    user = User.query.get_or_404(user_id)
    if user.username == OWNER_USERNAME or user.id == session["user_id"]:
        return jsonify({"status": "error", "message": "Cannot change this role."}), 403

    data = request.get_json(silent=True) or {}
    role_name = str(data.get("role", "")).strip().lower()
    if role_name not in ("admin", "staff"):
        return jsonify({"status": "error", "message": "Invalid role."}), 400

    new_role = Role.query.filter_by(name=role_name).first()
    if not new_role:
        return jsonify({"status": "error", "message": "Role not found."}), 404

    try:
        user.role = new_role
        log(f"Changed role for {user.username} -> {role_name}")
        db.session.commit()
        return jsonify({"status": "success", "message": f"{user.username} is now {role_name}."})
    except Exception:
        db.session.rollback()
        return jsonify({"status": "error", "message": "Unable to update role."}), 500

@app.route("/reset_password/<int:user_id>", methods=["POST"])
@login_required
@role_required("owner")
def reset_password(user_id):
    user = User.query.get_or_404(user_id)
    if user.username == OWNER_USERNAME:
        return jsonify({"status": "error", "message": "Owner password cannot be changed here."}), 403

    data = request.get_json(silent=True) or {}
    new_pass = str(data.get("password", "")).strip()
    min_password = current_app.config.get('MIN_PASSWORD_LENGTH', 12)
    if len(new_pass) < min_password:
        return jsonify({"status": "error", "message": f"Password must be at least {min_password} characters."}), 400

    try:
        user.password_hash = generate_password_hash(new_pass)
        log(f"Reset password for user {user.username}")
        db.session.commit()
        return jsonify({"status": "success", "message": "Password reset successfully."})
    except Exception:
        db.session.rollback()
        return jsonify({"status": "error", "message": "Unable to reset password."}), 500

@app.route("/delete_staff/<int:user_id>", methods=["POST"])
@login_required
@role_required("owner")
def delete_staff(user_id):
    user = User.query.get_or_404(user_id)
    if user.username == OWNER_USERNAME or user.id == session["user_id"]:
        return jsonify({"status": "error", "message": "Cannot delete this user."}), 403

    try:
        # Belt-and-suspenders alongside the ondelete=SET NULL migration on
        # Notification.user_id: works even in an environment where that
        # migration hasn't landed yet.
        Notification.query.filter_by(user_id=user.id).update({"user_id": None})
        db.session.delete(user)
        log(f"Deleted user {user.username}")
        db.session.commit()
        return jsonify({"status": "success", "message": f"{user.username} deleted."})
    except Exception:
        db.session.rollback()
        return jsonify({"status": "error", "message": "Unable to delete user."}), 500

@app.route("/toggle_staff_active/<int:user_id>", methods=["POST"])
@login_required
@role_required("owner")
def toggle_staff_active(user_id):
    """Deactivate or reactivate a staff account without deleting it —
    keeps sales history, audit trail, and notifications intact. Blocked
    at login by the is_active check in routes/auth.py."""
    user = User.query.get_or_404(user_id)
    if user.username == OWNER_USERNAME or user.id == session["user_id"]:
        return jsonify({"status": "error", "message": "Cannot change this account."}), 403

    try:
        user.is_active = not user.is_active
        state = "activated" if user.is_active else "deactivated"
        log(f"{state.capitalize()} user {user.username}")
        db.session.commit()
        return jsonify({
            "status": "success",
            "message": f"{user.username} {state}.",
            "is_active": user.is_active
        })
    except Exception:
        db.session.rollback()
        return jsonify({"status": "error", "message": "Unable to update account status."}), 500

# ==================== EXPORT ====================

def build_filtered_sale_query(args, needs_join=None):
    """Sale query with every filter from the given request.args applied
    (year, start/end date, staff, search). Shared by sales_history() and
    export_sales() so an export always matches what's currently on screen
    instead of silently exporting the entire table regardless of filters."""
    from sqlalchemy import or_

    user_filter = args.get("user")
    search = args.get("search")
    start = args.get("start")
    end = args.get("end")
    year = args.get("year", "all")
    if needs_join is None:
        needs_join = bool(search)

    q = Sale.query
    if needs_join:
        q = q.outerjoin(Shoe, Sale.shoe_id == Shoe.id) \
             .outerjoin(Product, Sale.product_id == Product.id)

    if year and year != "all":
        try:
            year_int = int(year)
            q = q.filter(Sale.date >= datetime(year_int, 1, 1),
                         Sale.date < datetime(year_int + 1, 1, 1))
        except ValueError:
            pass

    if start:
        try:
            q = q.filter(Sale.date >= datetime.strptime(start, "%Y-%m-%d"))
        except ValueError:
            pass
    if end:
        try:
            q = q.filter(Sale.date < datetime.strptime(end, "%Y-%m-%d") + timedelta(days=1))
        except ValueError:
            pass

    if user_filter:
        q = q.filter(Sale.sold_by == user_filter)
    if search:
        q = q.filter(or_(
            Shoe.brand.ilike(f"%{search}%"),
            Shoe.model.ilike(f"%{search}%"),
            Product.brand.ilike(f"%{search}%"),
            Product.model.ilike(f"%{search}%"),
            Sale.size.ilike(f"%{search}%")
        ))
    return q


@app.route('/export_sales')
@login_required
@role_required('owner', 'admin')
def export_sales():
    is_owner = session.get('role') == 'owner'
    out = io.StringIO()
    writer = csv.writer(out)
    header = ['Date', 'Product', 'Size', 'Qty', 'Total USD']
    if is_owner:
        header.append('Profit USD')
    header.append('Sold By')
    writer.writerow(header)

    sales = build_filtered_sale_query(request.args).order_by(Sale.date.desc()).all()
    for s in sales:
        if s.shoe:
            product_name = f"{s.shoe.brand} {s.shoe.model}"
        elif s.product:
            product_name = s.product.display_name()
        else:
            product_name = "Unknown"

        row = [
            s.date.strftime('%Y-%m-%d %H:%M'),
            product_name,
            s.size or 'N/A',
            s.quantity,
            f"{s.total_usd:.2f}",
        ]
        if is_owner:
            row.append(f"{s.profit_usd:.2f}")
        row.append(s.sold_by or 'System')
        writer.writerow(row)

    return send_file(
        io.BytesIO(out.getvalue().encode()),
        mimetype="text/csv",
        as_attachment=True,
        download_name=f"sales_export_{datetime.now().strftime('%Y%m%d_%H%M%S')}.csv"
    )

@app.route('/receipt/<int:sale_id>')
@login_required
def receipt(sale_id):
    sale = Sale.query.get_or_404(sale_id)
    return render_template(
        'receipt.html',
        sale=sale,
        role=session.get('role', 'staff'),
        usd_to_sll=usd_to_sll,
    )

# ==================== SALES HISTORY ====================

@app.route("/sales_history")
@login_required
def sales_history():
    from sqlalchemy import func, or_
    from collections import defaultdict

    page = request.args.get("page", 1, type=int)
    user_filter = request.args.get("user")
    search = request.args.get("search")
    start = request.args.get("start")
    end = request.args.get("end")
    year = request.args.get("year", "all")

    is_owner = session.get('role') == 'owner'

    # This page is the complete, all-time sales ledger — "All Years" is
    # the true default. Year is the primary way to narrow it; custom
    # start/end dates (below) are still available for finer control.
    needs_join = bool(search)

    def base_query():
        return build_filtered_sale_query(request.args, needs_join=needs_join)

    # ---- Totals, computed as SQL aggregates (not by loading every row
    # into Python) so this stays fast no matter how many years of sales
    # exist. Profit is owner-only, per the same policy as the dashboard.
    totals_row = base_query().with_entities(
        func.coalesce(func.sum(Sale.total_usd), 0),
        func.coalesce(func.sum(Sale.profit_usd), 0),
        func.coalesce(func.sum(Sale.quantity), 0),
        func.count(Sale.id)
    ).first()
    total_sales, total_profit_raw, total_items, total_transactions = totals_row
    total_profit = total_profit_raw if is_owner else None
    avg_sale = (total_sales / total_transactions) if total_transactions else 0

    unique_customers = base_query().filter(Sale.customer_id.isnot(None)) \
        .with_entities(func.count(func.distinct(Sale.customer_id))).scalar() or 0

    # ---- Top staff / top product, via GROUP BY instead of a Python loop
    top_staff_row = base_query().with_entities(
        Sale.sold_by, func.sum(Sale.total_usd).label('rev')
    ).group_by(Sale.sold_by).order_by(func.sum(Sale.total_usd).desc()).first()
    top_staff = top_staff_row[0] if top_staff_row else "-"

    top_product = "-"
    top_shoe_row = base_query().filter(Sale.shoe_id.isnot(None)).with_entities(
        Shoe.brand, Shoe.model, func.sum(Sale.quantity).label('qty')
    ).group_by(Shoe.id).order_by(func.sum(Sale.quantity).desc()).first()
    top_product_row = base_query().filter(Sale.product_id.isnot(None)).with_entities(
        Product.brand, Product.model, func.sum(Sale.quantity).label('qty')
    ).group_by(Product.id).order_by(func.sum(Sale.quantity).desc()).first()
    best_qty = 0
    if top_shoe_row and top_shoe_row.qty > best_qty:
        top_product = f"{top_shoe_row[0]} {top_shoe_row[1]}"
        best_qty = top_shoe_row.qty
    if top_product_row and top_product_row.qty > best_qty:
        top_product = f"{top_product_row[0]} {top_product_row[1]}".strip()

    # ---- Trend chart, via GROUP BY date instead of a Python loop
    trend_rows = base_query().with_entities(
        func.date(Sale.date), func.sum(Sale.total_usd)
    ).group_by(func.date(Sale.date)).order_by(func.date(Sale.date)).all()
    trend_labels = [str(d) for d, _ in trend_rows]
    trend_data = [float(v) for _, v in trend_rows]

    staff_rows = base_query().with_entities(
        Sale.sold_by, func.sum(Sale.total_usd)
    ).group_by(Sale.sold_by).all()
    staff_labels = [s for s, _ in staff_rows]
    staff_data = [float(v) for _, v in staff_rows]

    # ---- The visible, paginated transaction table
    sales_pagination = base_query().order_by(Sale.date.desc()) \
        .paginate(page=page, per_page=PER_PAGE, error_out=False)
    sales = sales_pagination.items

    # Union of the live staff roster (so new staff show up immediately,
    # even with zero sales yet) and historical sold_by values (so a
    # departed or deactivated staff member's old sales stay filterable).
    current_usernames = [u[0] for u in db.session.query(User.username).all()]
    historical_usernames = [s[0] for s in db.session.query(Sale.sold_by).distinct().all()]
    staff_list = sorted(set(current_usernames) | set(historical_usernames), key=str.lower)

    # Years that actually have sales data, for the year dropdown
    min_sale_date, max_sale_date = db.session.query(func.min(Sale.date), func.max(Sale.date)).first()
    if min_sale_date and max_sale_date:
        available_years = list(range(max_sale_date.year, min_sale_date.year - 1, -1))
    else:
        available_years = []

    notifications = Notification.query.filter(
        (Notification.user_id == session['user_id']) | (Notification.user_id == None)
    ).order_by(Notification.created_at.desc()).limit(10).all()

    unread_alerts = Notification.query.filter(
        ((Notification.user_id == session['user_id']) | (Notification.user_id == None)) &
        (Notification.is_read == False)
    ).count()

    return render_template(
        "admin/sales_history.html",
        sales=sales,
        total_sales=total_sales,
        total_profit=total_profit,
        total_items=total_items,
        avg_sale=avg_sale,
        unique_customers=unique_customers,
        top_staff=top_staff,
        top_product=top_product,
        staff_list=staff_list,
        user_filter=user_filter,
        search=search,
        start=start,
        end=end,
        year=year,
        available_years=available_years,
        current_page=page,
        total_pages=sales_pagination.pages,
        role=session["role"],
        username=session["username"],
        trend_labels=json.dumps(trend_labels),
        trend_data=json.dumps(trend_data),
        staff_labels=json.dumps(staff_labels),
        staff_data=json.dumps(staff_data),
        active_page='sales',
        notifications=notifications,
        unread_alerts=unread_alerts
    )

# ==================== KPI DATA ====================

@app.route("/kpi-data")
@login_required
def kpi_data():
    range_type = request.args.get("range", "daily")
    now = datetime.utcnow()

    if range_type == "daily":
        start = now.replace(hour=0, minute=0, second=0, microsecond=0)
    elif range_type == "monthly":
        start = now.replace(day=1, hour=0, minute=0, second=0, microsecond=0)
    elif range_type == "quarterly":
        quarter = (now.month - 1) // 3
        start_month = quarter * 3 + 1
        start = now.replace(month=start_month, day=1, hour=0, minute=0, second=0, microsecond=0)
    elif range_type == "yearly":
        start = now.replace(month=1, day=1, hour=0, minute=0, second=0, microsecond=0)
    else:
        start = now.replace(hour=0, minute=0, second=0, microsecond=0)

    kpi_row = db.session.query(
        func.coalesce(func.sum(Sale.total_usd), 0),
        func.coalesce(func.sum(Sale.profit_usd), 0),
        func.count(Sale.id)
    ).filter(Sale.date >= start).first()
    revenue = float(kpi_row[0] or 0)
    total_sales = int(kpi_row[2] or 0)

    # Profit and expenses are owner-only — an admin or staff account
    # calling this endpoint directly should not be able to see them
    # either, not just have them hidden in the dashboard UI.
    is_owner = session.get('role') == 'owner'
    profit = float(kpi_row[1] or 0) if is_owner else None
    expenses = (
        db.session.query(func.coalesce(func.sum(Expense.amount_usd), 0))
        .filter(Expense.date >= start).scalar()
        if is_owner else None
    )

    return jsonify({
        "revenue": revenue,
        "profit": profit,
        "sales": total_sales,
        "expenses": expenses
    })

# ==================== NOTIFICATIONS ====================

@app.route("/notifications-feed")
@login_required
def notifications_feed():
    """Return the current user's latest notifications without browser caching."""
    user_id = session["user_id"]
    notifications = Notification.query.filter(
        (Notification.user_id == user_id) | (Notification.user_id == None)
    ).order_by(Notification.created_at.desc()).limit(10).all()

    unread_count = Notification.query.filter(
        (Notification.user_id == user_id) | (Notification.user_id == None),
        Notification.is_read == False
    ).count()

    response = jsonify({
        "notifications": [
            {
                "id": n.id,
                "message": n.message,
                "category": n.category,
                "is_read": bool(n.is_read),
                "created_at": n.created_at.strftime("%d %b %H:%M")
            }
            for n in notifications
        ],
        "unread_count": unread_count
    })
    response.headers["Cache-Control"] = "no-store, no-cache, must-revalidate, max-age=0"
    response.headers["Pragma"] = "no-cache"
    response.headers["Expires"] = "0"
    return response

@app.route("/mark_all_read", methods=["POST"])
@login_required
def mark_all_read():
    Notification.query.filter(
        (Notification.user_id == session['user_id']) | (Notification.user_id == None),
        Notification.is_read == False
    ).update({"is_read": True}, synchronize_session=False)
    db.session.commit()
    return jsonify({"status": "success", "unread_count": 0})

@app.route("/mark_notification_read/<int:notif_id>", methods=["POST"])
@login_required
def mark_notification_read(notif_id):
    notification = Notification.query.filter(
        Notification.id == notif_id,
        (Notification.user_id == session['user_id']) | (Notification.user_id == None)
    ).first_or_404()
    notification.is_read = True
    db.session.commit()
    unread_count = Notification.query.filter_by(user_id=session['user_id'], is_read=False).count()
    return jsonify({"status": "success", "unread_count": unread_count})


# ==================== CLI ====================

@app.cli.command("reset-owner-password")
def reset_owner_password():
    owner = User.query.filter_by(username=OWNER_USERNAME).first()
    if not owner:
        print("Owner account not found")
        return
    new_password = input("Enter new owner password: ")
    owner.password_hash = generate_password_hash(new_password)
    db.session.commit()
    print("Owner password successfully reset")
