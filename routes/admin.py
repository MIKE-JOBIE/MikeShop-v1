# routes/admin.py - COMPLETE VERSION WITH ALL FUNCTIONALITY
from flask import render_template, request, redirect, url_for, session, flash, send_file, jsonify
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

PER_PAGE = 15
LOW_STOCK_THRESHOLD = 10
OWNER_USERNAME = "MichaelJobieMusa"

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

       # Get all shoes with their sizes (whole list — inventory is a single unified table)
    shoes = Shoe.query.filter(Shoe.is_active == True).order_by(
        Shoe.brand.asc(), Shoe.model.asc()
    ).all()

    # --- BUILD UNIFIED INVENTORY LIST ---
    all_inventory = []

    # 1. Add shoes with sizes
    for shoe in shoes:
        sizes_list = []
        for size in shoe.sizes:
            sizes_list.append({
                'id': size.id,
                'size': size.size,
                'quantity': size.quantity,
                'cost_usd': size.cost_usd,
                'sell_usd': size.sell_usd
            })
        shoe.sizes_json = json.dumps(sizes_list)
        
        all_inventory.append({
            'type': 'shoe',
            'id': shoe.id,
            'name': f"{shoe.brand} {shoe.model}",
            'brand': shoe.brand,
            'model': shoe.model,
            'category': 'shoe',
            'sizes': shoe.sizes,
            'sizes_json': shoe.sizes_json,
            'total_quantity': shoe.get_total_quantity(),
            'has_sizes': True,
            'object': shoe
        })

        # 2. Add products (clothing, grocery, electronics, other)
    products = Product.query.filter(Product.is_active == True).all()
    for product in products:
        variants_list = []
        for v in product.variants:
            variants_list.append({
                'id': v.id,
                'label': v.variant_label,
                'value': v.variant_value,
                'quantity': v.quantity,
                'cost_usd': v.cost_usd,
                'sell_usd': v.sell_usd
            })
        all_inventory.append({
            'type': 'product',
            'id': product.id,
            'name': product.display_name(),
            'category': product.category,
            'sizes': product.variants,           # variants act like sizes
            'sizes_json': json.dumps(variants_list),
            'total_quantity': product.get_total_quantity(),
            'has_sizes': True,
            'object': product
        })

    # Sort by category then name
    all_inventory.sort(key=lambda x: (x['category'], x['name']))

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

    daily_sales = dict(
        db.session.query(
            func.date(Sale.date),
            func.sum(Sale.total_usd)
        ).group_by(func.date(Sale.date))
    )

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
        .group_by(func.date(Sale.date), Shoe.id)
        .all()
    )
    for row in shoe_sales:
        best_sellers_by_date[row.sale_date][row.name] = int(row.total_qty)

    # --- Products ---
    product_sales = (
        db.session.query(
            func.date(Sale.date).label("sale_date"),
            (Product.brand + " " + Product.model).label("name"),
            Product.category.label("category"),
            func.coalesce(func.sum(Sale.quantity), 0).label("total_qty")
        )
        .join(Product, Sale.product_id == Product.id)
        .group_by(func.date(Sale.date), Product.id)
        .all()
    )
    for row in product_sales:
        day = row.sale_date
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
    all_shoes = Shoe.query.filter(Shoe.is_active == True).all()
    for shoe in all_shoes:
        for size in shoe.sizes:
            if 0 < size.quantity <= LOW_STOCK_THRESHOLD:
                low_stock_items.append({
                    'id': shoe.id,
                    'size_id': size.id,
                    'name': f"{shoe.brand} {shoe.model}",
                    'size': size.size,
                    'quantity': size.quantity,
                    'is_product': False,
                })

    # 2) Products — per variant
    all_products_for_alert = Product.query.filter(Product.is_active == True).all()
    for product in all_products_for_alert:
        for v in product.variants:
            if 0 < v.quantity <= LOW_STOCK_THRESHOLD:
                low_stock_items.append({
                    'id': product.id,
                    'size_id': v.id,
                    'name': product.display_name(),
                    'size': v.variant_value,
                    'quantity': v.quantity,
                    'is_product': True,
                })

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
        .filter(Sale.date >= week_ago)
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
        .filter(Sale.date >= week_ago)
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

    users = User.query.join(Role).filter(Role.name != "owner").order_by(User.username.asc()).all()
    all_customers = Customer.query.order_by(Customer.name.asc()).all()

    return render_template(
        'admin/dashboard.html',
        shoes=shoes,
        all_products=Product.query.filter_by(is_active=True).order_by(Product.brand.asc(), Product.model.asc()).all(),
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
        all_inventory=all_inventory
    )

# ==================== ADD NEW PRODUCT ====================

@app.route('/add_product', methods=['POST'])
@login_required
@role_required('owner', 'admin')
def add_product():
    from sqlalchemy import func as sa_func

    category = request.form.get('category', 'shoe').strip().lower()
    brand    = request.form.get('brand', '').strip()
    model    = request.form.get('model', '').strip()
    variant_value = request.form.get('variant_value', '').strip()

    try:
        quantity = int(request.form.get('quantity', 0) or 0)
        cost_usd = float(request.form.get('cost_usd', 0) or 0)
        sell_usd = float(request.form.get('sell_usd', 0) or 0)
    except (TypeError, ValueError):
        flash("Please enter valid numeric values.")
        return redirect(url_for('dashboard'))

    image_url = request.form.get('image_url', '').strip()
    sku       = request.form.get('sku', '').strip() or None

    # ---------- Validation ----------
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

    if cost_usd < 0 or sell_usd < 0 or quantity < 0:
        flash("Values cannot be negative.")
        return redirect(url_for('dashboard'))

    # ---------- SHOE ----------
    if category == 'shoe':
        if not model:
            flash("Model is required for shoes.")
            return redirect(url_for('dashboard'))

        shoe_match = Shoe.query.filter(
            sa_func.lower(Shoe.brand) == brand.lower(),
            sa_func.lower(Shoe.model) == model.lower()
        ).first()

        if shoe_match:
            flash(
                f"⚠️ '{brand} {model}' already exists. "
                f"Use the Restock section below to add a new size."
            )
            return redirect(url_for('dashboard', _anchor='restock-section',
                                    prefill_shoe=shoe_match.id))

        shoe = Shoe(brand=brand, model=model, category='shoe',
                    image_url=image_url or None, sku=sku)
        try:
            db.session.add(shoe)
            db.session.flush()
            db.session.add(ShoeSize(
                shoe_id=shoe.id, size=variant_value, quantity=quantity,
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
    product_match = Product.query.filter(
        Product.category == category,
        sa_func.lower(Product.brand) == brand.lower(),
        sa_func.lower(Product.model) == model.lower()
    ).first()

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
    variant_value = request.form.get('size', '').strip()

    try:
        quantity = int(request.form.get('quantity', 0))
        cost_usd = float(request.form.get('cost_usd', 0))
        sell_usd = float(request.form.get('sell_usd', 0))
    except (TypeError, ValueError):
        flash("Invalid restock values.")
        return redirect(url_for('dashboard'))

    if ':' not in target:
        flash("Please select a product to restock.")
        return redirect(url_for('dashboard'))

    if not variant_value or quantity <= 0 or cost_usd < 0 or sell_usd < 0:
        flash("Please fill all fields correctly.")
        return redirect(url_for('dashboard'))

    kind, id_str = target.split(':', 1)
    try:
        target_id = int(id_str)
    except ValueError:
        flash("Invalid product selection.")
        return redirect(url_for('dashboard'))

    supplier = request.form.get('supplier', '').strip()

        # ----- SHOE -----
    if kind == 'shoe':
        shoe = Shoe.query.get_or_404(target_id)
        existing = shoe.get_size_by_name(variant_value)
        if existing:
            _apply_restock(existing, quantity, cost_usd, sell_usd)
            size_obj = existing
            action_msg = f"updated size {variant_value} (+{quantity})"
        else:
            size_obj = ShoeSize(
                shoe_id=shoe.id, size=variant_value, quantity=quantity,
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
        db.session.commit()
        flash(f"✅ {shoe.brand} {shoe.model} — {action_msg}.")
        return redirect(url_for('dashboard'))

        # ----- PRODUCT -----
    if kind == 'product':
        product = Product.query.get_or_404(target_id)
        existing = product.get_variant_by_value(variant_value)
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
        db.session.commit()
        flash(f"✅ {product.display_name()} — {action_msg}.")
        return redirect(url_for('dashboard'))

    flash("Invalid product type.")
    return redirect(url_for('dashboard'))

# ==================== SELL SHOE ====================

@app.route('/sell/<int:size_id>', methods=['POST'])
@login_required
@role_required('owner', 'admin', 'staff')
def sell(size_id):
    shoe_size = ShoeSize.query.with_for_update().get_or_404(size_id)
    shoe = shoe_size.shoe
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

        if customer:
            customer.total_spent = (customer.total_spent or 0) + total_usd
            customer.total_orders = (customer.total_orders or 0) + 1
            customer.last_purchase = datetime.utcnow()
            customer.loyalty_points = (customer.loyalty_points or 0) + int(total_usd * 0.1)
            db.session.add(CustomerPurchase(
                customer_id=customer.id, sale_id=sale.id, total_usd=total_usd
            ))

        db.session.add(Notification(
            message=f"Sold {qty} - {shoe.brand} {shoe.model} (Size {shoe_size.size})",
            category="sale", user_id=session['user_id']
        ))
        if shoe_size.quantity <= LOW_STOCK_THRESHOLD and shoe_size.quantity > 0:
            db.session.add(Notification(
                message=f"Low stock: {shoe.brand} {shoe.model} size {shoe_size.size} "
                        f"({shoe_size.quantity} left)",
                category="low_stock", user_id=session['user_id']
            ))

        log(f"Sold {qty} of {shoe.brand} {shoe.model} size {shoe_size.size}")
        db.session.commit()
        flash(f"✅ Sale: {qty} × {shoe.brand} {shoe.model} (Size {shoe_size.size})"
              + (f" for {customer.name}" if customer else ""))
    except Exception as e:
        db.session.rollback()
        flash(f"The sale could not be completed: {str(e)}")

    return redirect(url_for('dashboard'))


# ==================== SELL PRODUCT (variant-aware) ====================

@app.route('/sell_product/<int:product_id>', methods=['POST'])
@login_required
@role_required('owner', 'admin', 'staff')
def sell_product(product_id):
    product = Product.query.get_or_404(product_id)
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
        available = product.get_available_variants()
        if not available:
            flash(f"⚠️ {product.display_name()} is out of stock.")
            return redirect(url_for('dashboard'))
        variant = available[0]

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

        if customer:
            customer.total_spent = (customer.total_spent or 0) + total_usd
            customer.total_orders = (customer.total_orders or 0) + 1
            customer.last_purchase = datetime.utcnow()
            customer.loyalty_points = (customer.loyalty_points or 0) + int(total_usd * 0.1)
            db.session.add(CustomerPurchase(
                customer_id=customer.id, sale_id=sale.id, total_usd=total_usd
            ))

        db.session.add(Notification(
            message=f"Sold {qty} - {product.display_name()} ({variant.variant_value})",
            category="sale", user_id=session['user_id']
        ))
        if variant.quantity <= LOW_STOCK_THRESHOLD and variant.quantity > 0:
            db.session.add(Notification(
                message=f"Low stock: {product.display_name()} {variant.variant_value} "
                        f"({variant.quantity} left)",
                category="low_stock", user_id=session['user_id']
            ))

        log(f"Sold {qty} of {product.display_name()} ({variant.variant_value})")
        db.session.commit()

        flash(f"✅ Sale: {qty} × {product.display_name()} ({variant.variant_value})"
              + (f" for {customer.name}" if customer else ""))
    except Exception as e:
        db.session.rollback()
        flash(f"The sale could not be completed: {str(e)}")

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
        db.session.add(Notification(message=msg, category="expense", user_id=session['user_id']))
        log(f"Added expense: {title} (${amount_usd:.2f})")
        db.session.commit()
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
        return redirect(url_for('dashboard'))

    if username == OWNER_USERNAME:
        flash("Owner account cannot be modified.")
        return redirect(url_for('dashboard'))

    if len(password) < 6:
        flash("Password must be at least 6 characters.")
        return redirect(url_for('dashboard'))

    if role_name not in ('admin', 'staff'):
        flash("Invalid staff role.")
        return redirect(url_for('dashboard'))

    if User.query.filter_by(username=username).first():
        flash("User already exists.")
        return redirect(url_for('dashboard'))

    role = Role.query.filter_by(name=role_name).first()
    if not role:
        flash("Selected role does not exist.")
        return redirect(url_for('dashboard'))

    try:
        user = User(username=username, password_hash=generate_password_hash(password), role=role)
        db.session.add(user)
        msg = f"New user created: {username}"
        db.session.add(Notification(message=msg, category="user", user_id=session['user_id']))
        log(f"Created user {username}")
        db.session.commit()
        flash(f"Staff account '{username}' created successfully.")
    except Exception:
        db.session.rollback()
        flash("Unable to create the staff account.")
    
    return redirect(url_for('dashboard'))

@app.route("/staff_list")
@login_required
@role_required("owner")
def staff_list():
    users = User.query.join(Role).filter(Role.name != "owner").all()
    return render_template(
        "admin/staff_list.html",
        users=users,
        role=session["role"],
        username=session["username"],
        active_page='staff'
    )

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
    if len(new_pass) < 6:
        return jsonify({"status": "error", "message": "Password must be at least 6 characters."}), 400

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

@app.route('/export_sales')
@login_required
@role_required('owner', 'admin')
def export_sales():
    out = io.StringIO()
    writer = csv.writer(out)
    writer.writerow(['Date', 'Product', 'Size', 'Qty', 'Total USD', 'Profit USD', 'Sold By'])

    for s in Sale.query.order_by(Sale.date.desc()).all():
        if s.shoe:
            product_name = f"{s.shoe.brand} {s.shoe.model}"
        elif s.product:
            product_name = s.product.display_name()
        else:
            product_name = "Unknown"

        writer.writerow([
            s.date.strftime('%Y-%m-%d %H:%M'),
            product_name,
            s.size or 'N/A',
            s.quantity,
            f"{s.total_usd:.2f}",
            f"{s.profit_usd:.2f}",
            s.sold_by or 'System'
        ])

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
    from sqlalchemy import func
    from collections import defaultdict
    import json
    from datetime import datetime, timedelta

    page = request.args.get("page", 1, type=int)
    user_filter = request.args.get("user")
    search = request.args.get("search")
    start = request.args.get("start")
    end = request.args.get("end")
    quick = request.args.get("quick")

    query = Sale.query
    today = datetime.now().replace(hour=0, minute=0, second=0, microsecond=0)
    now = datetime.now()

    # Quick date filters
    if quick == "today":
        start_date = today
        end_date = today + timedelta(days=1)
        query = query.filter(Sale.date >= start_date, Sale.date < end_date)
    elif quick == "week":
        start_date = today - timedelta(days=today.weekday())
        end_date = start_date + timedelta(days=7)
        query = query.filter(Sale.date >= start_date, Sale.date < end_date)
    elif quick == "month":
        start_date = today.replace(day=1)
        if today.month == 12:
            end_date = today.replace(year=today.year + 1, month=1, day=1)
        else:
            end_date = today.replace(month=today.month + 1, day=1)
        query = query.filter(Sale.date >= start_date, Sale.date < end_date)
    elif quick == "30days":
        start_date = now - timedelta(days=30)
        end_date = now
        query = query.filter(Sale.date >= start_date, Sale.date <= end_date)

    # Custom date filters
    if not quick:
        if start:
            start_date = datetime.strptime(start, "%Y-%m-%d")
            query = query.filter(Sale.date >= start_date)
        if end:
            end_date = datetime.strptime(end, "%Y-%m-%d") + timedelta(days=1)
            query = query.filter(Sale.date < end_date)

    if user_filter:
        query = query.filter(Sale.sold_by == user_filter)
    if search:
        from sqlalchemy import or_
        query = query.outerjoin(Shoe, Sale.shoe_id == Shoe.id) \
                     .outerjoin(Product, Sale.product_id == Product.id) \
                     .filter(or_(
                         Shoe.brand.ilike(f"%{search}%"),
                         Shoe.model.ilike(f"%{search}%"),
                         Product.brand.ilike(f"%{search}%"),
                         Product.model.ilike(f"%{search}%"),
                         Sale.size.ilike(f"%{search}%")
                     ))

    query = query.order_by(Sale.date.desc())
    filtered_sales = query.all()
    sales_pagination = query.paginate(page=page, per_page=PER_PAGE, error_out=False)
    sales = sales_pagination.items

    total_sales = sum(s.total_usd for s in filtered_sales)
    total_profit = sum(s.profit_usd for s in filtered_sales)
    total_items = sum(s.quantity for s in filtered_sales)
    total_transactions = len(filtered_sales)
    avg_sale = total_sales / total_transactions if total_transactions > 0 else 0

    staff_totals = defaultdict(float)
    for s in filtered_sales:
        staff_totals[s.sold_by] += s.total_usd
    top_staff = max(staff_totals, key=staff_totals.get) if staff_totals else "-"

    product_totals = defaultdict(int)
    for s in filtered_sales:
        if s.shoe:
            product_totals[f"{s.shoe.brand} {s.shoe.model}"] += s.quantity
        elif s.product:
            product_totals[s.product.display_name()] += s.quantity
    top_product = max(product_totals, key=product_totals.get) if product_totals else "-"

    staff_list = [s[0] for s in db.session.query(Sale.sold_by).distinct().all()]

    trend_dict = defaultdict(float)
    for s in filtered_sales:
        day = s.date.strftime("%Y-%m-%d")
        trend_dict[day] += s.total_usd

    trend_labels = sorted(trend_dict.keys())
    trend_data = [trend_dict[d] for d in trend_labels]
    staff_labels = list(staff_totals.keys())
    staff_data = list(staff_totals.values())

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
        top_staff=top_staff,
        top_product=top_product,
        staff_list=staff_list,
        user_filter=user_filter,
        search=search,
        start=start,
        end=end,
        quick=quick,
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

    sales = Sale.query.filter(Sale.date >= start).all()
    revenue = sum(s.total_usd for s in sales)
    profit = sum(s.profit_usd for s in sales)
    total_sales = len(sales)
    expenses = db.session.query(func.coalesce(func.sum(Expense.amount_usd), 0)).filter(Expense.date >= start).scalar()

    return jsonify({
        "revenue": revenue,
        "profit": profit,
        "sales": total_sales,
        "expenses": expenses
    })

# ==================== NOTIFICATIONS ====================

@app.route("/mark_all_read", methods=["POST"])
@login_required
def mark_all_read():
    Notification.query.filter_by(user_id=session['user_id'], is_read=False).update({"is_read": True})
    db.session.commit()
    return jsonify({"status": "success", "unread_count": 0})

@app.route("/mark_notification_read/<int:notif_id>", methods=["POST"])
@login_required
def mark_notification_read(notif_id):
    notification = Notification.query.filter_by(id=notif_id, user_id=session['user_id']).first_or_404()
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