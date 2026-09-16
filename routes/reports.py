# routes/reports.py
from flask import Flask, jsonify, render_template, request, redirect, url_for, session, flash, send_file
from app import app, csrf
from database import db
from models.core import (
    Sale, Expense, Shoe, ShoeSize, User, Notification,
    Product, ProductVariant,
)
from decorators import login_required, role_required
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
    days = request.args.get('days', 30, type=int)
    start_date = datetime.utcnow() - timedelta(days=days)
    
    # Total sales
    total_sales = db.session.query(
        func.sum(Sale.total_usd).label('revenue'),
        func.sum(Sale.profit_usd).label('profit'),
        func.count(Sale.id).label('count'),
        func.sum(Sale.quantity).label('items')
    ).filter(Sale.date >= start_date).first()
    
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
    
    # Expenses
    total_expenses = db.session.query(
        func.sum(Expense.amount_usd)
    ).filter(Expense.date >= start_date).scalar() or 0
    
    # Inventory value — shoes (sum over sizes) + products (sum over variants)
    shoe_value = db.session.query(
        func.coalesce(func.sum(ShoeSize.cost_usd * ShoeSize.quantity), 0)
    ).scalar() or 0

    product_value = db.session.query(
        func.coalesce(func.sum(ProductVariant.cost_usd * ProductVariant.quantity), 0)
    ).scalar() or 0

    total_inventory_value = float(shoe_value) + float(product_value)

    # Total distinct products (shoes + non-shoe products)
    total_products = Shoe.query.count() + Product.query.count()
    
    return jsonify({
        'total_revenue': float(total_sales.revenue or 0),
        'total_profit': float(total_sales.profit or 0),
        'total_transactions': total_sales.count or 0,
        'total_items_sold': total_sales.items or 0,
        'total_expenses': float(total_expenses),
        'net_profit': float((total_sales.profit or 0) - total_expenses),
        'inventory_value': float(total_inventory_value),
        'total_products': total_products,
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
    })

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