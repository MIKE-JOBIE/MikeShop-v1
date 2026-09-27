# routes/reports.py
from flask import Flask, jsonify, render_template, request, redirect, url_for, session, flash, send_file
from app import app, csrf, depreciated_value
from database import db
from models.core import (
    Sale, Expense, Shoe, ShoeSize, User, Role, Notification,
    Product, ProductVariant,
)
from decorators import login_required, role_required
from routes.admin import LOW_STOCK_THRESHOLD
from datetime import datetime, timedelta
from sqlalchemy import func
import io
import csv
from reportlab.pdfgen import canvas
from reportlab.lib.pagesizes import letter
from reportlab.lib import colors
from reportlab.platypus import SimpleDocTemplate, Table, TableStyle, Paragraph, Spacer
from reportlab.lib.styles import getSampleStyleSheet, ParagraphStyle
from reportlab.lib.units import inch 

@app.route('/reports')
@login_required
@role_required('owner', 'admin')
def reports():
    # Get unread alerts for the header
    unread_alerts = Notification.query.filter(
        ((Notification.user_id == session['user_id']) | (Notification.user_id == None)) &
        (Notification.is_read == False)
    ).count()
    
    notifications = Notification.query.filter(
        (Notification.user_id == session['user_id']) | (Notification.user_id == None)
    ).order_by(Notification.created_at.desc()).limit(10).all()
    
    return render_template(
        'admin/reports.html',
        role=session.get('role'),
        username=session.get('username'),
        title='Reports & Analytics',
        active_page='reports',
        unread_alerts=unread_alerts,
        notifications=notifications
    )

@app.route('/api/report/sales_summary')
@login_required
@role_required('owner', 'admin')
def sales_summary_report():
    from models.customer import Customer
    days = request.args.get('days', 30, type=int)
    now = datetime.utcnow()
    start_date = now - timedelta(days=days)
    # Previous period of equal length, immediately before this one — this
    # is what "is my business growing" actually compares against.
    prev_start = start_date - timedelta(days=days)
    prev_end = start_date

    is_owner = session.get('role') == 'owner'

    def period_totals(period_start, period_end=None):
        q = db.session.query(
            func.coalesce(func.sum(Sale.total_usd), 0).label('revenue'),
            func.coalesce(func.sum(Sale.profit_usd), 0).label('profit'),
            func.count(Sale.id).label('count'),
            func.coalesce(func.sum(Sale.quantity), 0).label('items')
        ).filter(Sale.date >= period_start)
        if period_end is not None:
            q = q.filter(Sale.date < period_end)
        return q.first()

    total_sales = period_totals(start_date)
    prev_sales = period_totals(prev_start, prev_end)

    def pct_change(current, previous):
        """% change vs the previous period. None (shown as 'N/A', not 0%
        or infinity) when there's no previous-period data to compare
        against — a real "no baseline" case, not a 0% or -100% change."""
        if not previous:
            return None
        return round(((current - previous) / previous) * 100, 1)

    # Daily breakdown
    daily = db.session.query(
        func.date(Sale.date).label('day'),
        func.sum(Sale.total_usd).label('revenue'),
        func.count(Sale.id).label('count'),
        func.sum(Sale.quantity).label('items')
    ).filter(Sale.date >= start_date).group_by(
        func.date(Sale.date)
    ).order_by(func.date(Sale.date)).all()
    
    # Top shoes
    top_shoes = db.session.query(
        Shoe.brand, Shoe.model,
        func.sum(Sale.quantity).label('qty'),
        func.sum(Sale.total_usd).label('revenue')
    ).join(Sale, Sale.shoe_id == Shoe.id).filter(
        Sale.date >= start_date
    ).group_by(Shoe.id).order_by(
        func.sum(Sale.total_usd).desc()
    ).limit(10).all()

    # Top products
    top_products_db = db.session.query(
        Product.brand, Product.model, Product.category,
        func.sum(Sale.quantity).label('qty'),
        func.sum(Sale.total_usd).label('revenue')
    ).join(Sale, Sale.product_id == Product.id).filter(
        Sale.date >= start_date
    ).group_by(Product.id).order_by(
        func.sum(Sale.total_usd).desc()
    ).limit(10).all()

    # Merge and take top 10 by revenue
    _combined = []
    for r in top_shoes:
        _combined.append({
            'name': f"{r.brand} {r.model}".strip(),
            'quantity': int(r.qty or 0),
            'revenue': float(r.revenue or 0),
        })
    for r in top_products_db:
        _combined.append({
            'name': f"{r.brand} {r.model}".strip() + f" ({r.category})",
            'quantity': int(r.qty or 0),
            'revenue': float(r.revenue or 0),
        })
    _combined.sort(key=lambda x: x['revenue'], reverse=True)
    top_products = _combined[:10]
    
    # Top staff
    top_staff = db.session.query(
        Sale.sold_by,
        func.sum(Sale.total_usd).label('revenue'),
        func.count(Sale.id).label('count')
    ).filter(Sale.date >= start_date).group_by(
        Sale.sold_by
    ).order_by(func.sum(Sale.total_usd).desc()).limit(5).all()
    
    # Expenses (owner-only, see below)
    total_expenses = db.session.query(
        func.sum(Expense.amount_usd)
    ).filter(Expense.date >= start_date).scalar() or 0

    # Inventory value — split into two figures rather than one that either
    # hides discontinued stock entirely or counts it at full value forever:
    #   - Active: current sellable stock, at full cost value
    #   - Depreciating: discontinued stock, written down over time
    #     (see depreciated_value() in app.py) toward $0
    active_shoe_value = db.session.query(
        func.coalesce(func.sum(ShoeSize.cost_usd * ShoeSize.quantity), 0)
    ).join(Shoe, ShoeSize.shoe_id == Shoe.id).filter(Shoe.is_active == True).scalar() or 0

    active_product_value = db.session.query(
        func.coalesce(func.sum(ProductVariant.cost_usd * ProductVariant.quantity), 0)
    ).join(Product, ProductVariant.product_id == Product.id).filter(Product.is_active == True).scalar() or 0

    active_inventory_value = float(active_shoe_value) + float(active_product_value)

    # Depreciating value needs a per-item calculation (each item has its
    # own deactivated_at), so this loads the (typically small) set of
    # discontinued items rather than using a single SQL aggregate.
    depreciating_value = 0.0
    for shoe in Shoe.query.filter(Shoe.is_active == False).all():
        shoe_cost_total = sum((s.cost_usd or 0) * (s.quantity or 0) for s in shoe.sizes)
        depreciating_value += depreciated_value(shoe_cost_total, shoe.deactivated_at)
    for product in Product.query.filter(Product.is_active == False).all():
        product_cost_total = sum((v.cost_usd or 0) * (v.quantity or 0) for v in product.variants)
        depreciating_value += depreciated_value(product_cost_total, product.deactivated_at)

    total_inventory_value = active_inventory_value + depreciating_value

    # Total distinct products (shoes + non-shoe products)
    total_products = Shoe.query.count() + Product.query.count()

    # Whole-app summary — customers, staff, low stock. This is what makes
    # Reports a summary of the whole app rather than just a sales report.
    total_customers = Customer.query.count()
    new_customers_this_period = Customer.query.filter(Customer.created_at >= start_date).count()
    total_staff = User.query.join(Role).filter(Role.name != 'owner').count()
    low_stock_shoe_sizes = db.session.query(ShoeSize).join(Shoe).filter(
        Shoe.is_active == True, ShoeSize.quantity <= LOW_STOCK_THRESHOLD, ShoeSize.quantity > 0
    ).count()
    low_stock_variants = db.session.query(ProductVariant).join(Product).filter(
        Product.is_active == True, ProductVariant.quantity <= LOW_STOCK_THRESHOLD, ProductVariant.quantity > 0
    ).count()

    response = {
        'period_days': days,
        'total_revenue': float(total_sales.revenue or 0),
        'total_transactions': total_sales.count or 0,
        'total_items_sold': total_sales.items or 0,
        'inventory_value': float(total_inventory_value),
        'active_inventory_value': float(active_inventory_value),
        'depreciating_inventory_value': round(float(depreciating_value), 2),
        'total_products': total_products,
        'total_customers': total_customers,
        'new_customers_this_period': new_customers_this_period,
        'total_staff': total_staff,
        'low_stock_count': low_stock_shoe_sizes + low_stock_variants,
        'growth': {
            'revenue_pct': pct_change(total_sales.revenue or 0, prev_sales.revenue or 0),
            'transactions_pct': pct_change(total_sales.count or 0, prev_sales.count or 0),
        },
        'daily': [
            {
                'date': d.day,
                'revenue': float(d.revenue or 0),
                'count': d.count or 0,
                'items': d.items or 0
            } for d in daily
        ],
        'top_products': top_products,
        'top_staff': [
            {
                'name': s.sold_by or 'Unknown',
                'revenue': float(s.revenue or 0),
                'sales': s.count or 0
            } for s in top_staff
        ]
    }

    # Profit, expenses, and net profit are owner-only — same policy as the
    # Dashboard. An admin calling this endpoint directly should not get
    # these fields either, not just have them hidden in the UI.
    if is_owner:
        response['total_profit'] = float(total_sales.profit or 0)
        response['total_expenses'] = float(total_expenses)
        response['net_profit'] = float((total_sales.profit or 0) - total_expenses)
        response['growth']['profit_pct'] = pct_change(total_sales.profit or 0, prev_sales.profit or 0)
    else:
        response['total_profit'] = None
        response['total_expenses'] = None
        response['net_profit'] = None
        response['growth']['profit_pct'] = None

    return jsonify(response)

@app.route('/export/report/pdf')
@login_required
@role_required('owner', 'admin')
def export_report_pdf():
    days = request.args.get('days', 30, type=int)
    start_date = datetime.utcnow() - timedelta(days=days)
    
    buffer = io.BytesIO()
    doc = SimpleDocTemplate(buffer, pagesize=letter)
    styles = getSampleStyleSheet()
    story = []
    
    title_style = ParagraphStyle(
        'CustomTitle',
        parent=styles['Heading1'],
        fontSize=24,
        textColor=colors.HexColor('#059669')
    )
    report_days = f"Last {days} days"
    story.append(Paragraph(
        f"{app.config.get('STORE_NAME', 'MikeShop')} — Sales Report",
        title_style
    ))
    story.append(Paragraph(report_days, styles['Normal']))
    story.append(Paragraph(f"Period: {start_date.strftime('%Y-%m-%d')} to {datetime.now().strftime('%Y-%m-%d')}", styles['Normal']))
    story.append(Spacer(1, 0.25*inch))
    
    sales_data = db.session.query(
        Sale.date,
        Shoe.brand.label('shoe_brand'),
        Shoe.model.label('shoe_model'),
        Product.brand.label('prod_brand'),
        Product.model.label('prod_model'),
        Product.category.label('prod_category'),
        Sale.size,
        Sale.quantity,
        Sale.total_usd,
        Sale.profit_usd,
        Sale.sold_by
    ).outerjoin(Shoe, Sale.shoe_id == Shoe.id) \
     .outerjoin(Product, Sale.product_id == Product.id) \
     .filter(Sale.date >= start_date) \
     .order_by(Sale.date.desc()).limit(100).all()

    data = [['Date', 'Product', 'Variant', 'Qty', 'Total ($)', 'Profit ($)', 'Sold By']]
    for s in sales_data:
        if s.shoe_brand:
            name = f"{s.shoe_brand} {s.shoe_model}".strip()
        elif s.prod_brand:
            name = f"{s.prod_brand} {s.prod_model}".strip()
            if s.prod_category:
                name += f" ({s.prod_category})"
        else:
            name = "Deleted item"

        data.append([
            s.date.strftime('%Y-%m-%d'),
            name,
            s.size or 'N/A',
            str(s.quantity),
            f"${s.total_usd:.2f}",
            f"${s.profit_usd:.2f}",
            s.sold_by or 'Unknown'
        ])
    
    table = Table(data)
    table.setStyle(TableStyle([
        ('BACKGROUND', (0, 0), (-1, 0), colors.HexColor('#059669')),
        ('TEXTCOLOR', (0, 0), (-1, 0), colors.whitesmoke),
        ('ALIGN', (0, 0), (-1, -1), 'CENTER'),
        ('FONTNAME', (0, 0), (-1, 0), 'Helvetica-Bold'),
        ('FONTSIZE', (0, 0), (-1, 0), 9),
        ('BOTTOMPADDING', (0, 0), (-1, 0), 12),
        ('BACKGROUND', (0, 1), (-1, -1), colors.HexColor('#F9FAFB')),
        ('GRID', (0, 0), (-1, -1), 1, colors.HexColor('#E5E7EB'))
    ]))
    
    story.append(table)
    doc.build(story)
    
    buffer.seek(0)
    return send_file(
        buffer,
        as_attachment=True,
        download_name=f'sales_report_{datetime.now().strftime("%Y%m%d")}.pdf',
        mimetype='application/pdf'
    )

@app.route('/export/report/csv')
@login_required
@role_required('owner', 'admin')
def export_report_csv():
    days = request.args.get('days', 30, type=int)
    start_date = datetime.utcnow() - timedelta(days=days)
    
    out = io.StringIO()
    writer = csv.writer(out)
    writer.writerow([
        'Date', 'Product', 'Type', 'Brand', 'Model',
        'Variant', 'Quantity', 'Total USD', 'Profit USD', 'Sold By'
    ])

    sales = Sale.query.filter(Sale.date >= start_date).order_by(Sale.date.desc()).all()
    for s in sales:
        if s.shoe:
            name = f"{s.shoe.brand} {s.shoe.model}".strip()
            kind = 'shoe'
            brand = s.shoe.brand
            model = s.shoe.model
        elif s.product:
            name = s.product.display_name()
            kind = s.product.category
            brand = s.product.brand
            model = s.product.model
        else:
            name, kind, brand, model = 'Deleted item', 'unknown', '', ''

        writer.writerow([
            s.date.strftime('%Y-%m-%d %H:%M'),
            name,
            kind,
            brand,
            model,
            s.size or 'N/A',
            s.quantity,
            f"{s.total_usd:.2f}",
            f"{s.profit_usd:.2f}",
            s.sold_by or 'Unknown'
        ])
    
    return send_file(
        io.BytesIO(out.getvalue().encode()),
        mimetype='text/csv',
        as_attachment=True,
        download_name=f'sales_report_{datetime.now().strftime("%Y%m%d")}.csv'
    )