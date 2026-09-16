# services/email.py
from flask_mail import Mail, Message
from app import app, csrf
from database import db
import os

mail = Mail(app)

def usd_to_sll(value):
    """Convert USD to SLL using configured rate"""
    rate = app.config.get('USD_TO_SLL', 23000)
    return round((value or 0) * rate, 2)

def send_receipt(sale, customer_email=None):
    """Send receipt email for a sale"""
    if not customer_email:
        return
    
    subject = f"Receipt from {app.config['STORE_NAME']}"
    
    # Use the local function
    sll_amount = usd_to_sll(sale.total_usd)
    
    html = f"""
    <!DOCTYPE html>
    <html>
    <head>
        <style>
            body {{ font-family: Arial, sans-serif; max-width: 600px; margin: 0 auto; }}
            .header {{ background: #059669; color: white; padding: 20px; text-align: center; }}
            .content {{ padding: 20px; }}
            .total {{ font-size: 24px; font-weight: bold; color: #059669; }}
            table {{ width: 100%; border-collapse: collapse; }}
            th, td {{ padding: 10px; border-bottom: 1px solid #E5E7EB; text-align: left; }}
            .footer {{ background: #F9FAFB; padding: 15px; text-align: center; color: #6B7280; font-size: 12px; }}
        </style>
    </head>
    <body>
        <div class="header">
            <h1>{app.config['STORE_NAME']}</h1>
            <p>Thank you for your purchase!</p>
        </div>
        
        <div class="content">
            <h2>Receipt #{sale.id}</h2>
            <p><strong>Date:</strong> {sale.date.strftime('%Y-%m-%d %H:%M')}</p>
            <p><strong>Sold by:</strong> {sale.sold_by or 'Staff'}</p>
            
            <table>
                <tr>
                    <th>Product</th>
                    <th>Qty</th>
                    <th>Price</th>
                    <th>Total</th>
                </tr>
                <tr>
                    <td>{sale.shoe.brand} {sale.shoe.model}</td>
                    <td>{sale.quantity}</td>
                    <td>${sale.shoe.sell_usd:.2f}</td>
                    <td>${sale.total_usd:.2f}</td>
                </tr>
            </table>
            
            <p class="total">Total: ${sale.total_usd:.2f}</p>
            <p>💰 SLL {sll_amount:.2f}</p>
        </div>
        
        <div class="footer">
            <p>{app.config['STORE_NAME']} - Your trusted sneaker & clothing store</p>
            <p>Visit us at {app.config.get('STORE_URL', '')}</p>
        </div>
    </body>
    </html>
    """
    
    msg = Message(
        subject=subject,
        recipients=[customer_email],
        html=html
    )
    mail.send(msg)


def send_low_stock_alert(shoe):
    """Send low stock alert to owners"""
    from models.core import User
    
    owners = User.query.filter_by(role_id=1).all()  # role_id 1 = owner
    owner_emails = [o.email for o in owners if o.email]
    
    if not owner_emails:
        return
    
    subject = f"⚠️ Low Stock Alert: {shoe.brand} {shoe.model}"
    
    html = f"""
    <!DOCTYPE html>
    <html>
    <head>
        <style>
            body {{ font-family: Arial, sans-serif; }}
            .alert {{ background: #FEE2E2; border-left: 4px solid #DC2626; padding: 15px; }}
            .info {{ background: #F9FAFB; padding: 15px; }}
        </style>
    </head>
    <body>
        <div class="alert">
            <h2>⚠️ Low Stock Alert</h2>
            <p><strong>{shoe.brand} {shoe.model}</strong> is running low!</p>
        </div>
        
        <div class="info">
            <p><strong>Current Stock:</strong> {shoe.quantity} units</p>
            <p><strong>Threshold:</strong> 10 units</p>
            <p><strong>SKU:</strong> {shoe.sku or 'N/A'}</p>
            <p><strong>Size:</strong> {shoe.size or 'N/A'}</p>
        </div>
        
        <p>Please restock as soon as possible.</p>
    </body>
    </html>
    """
    
    msg = Message(
        subject=subject,
        recipients=owner_emails,
        html=html
    )
    mail.send(msg)

def send_daily_report(date, recipients):
    """Send daily sales report"""
    from models.core import Sale, Expense
    from sqlalchemy import func
    
    start = date.replace(hour=0, minute=0, second=0)
    end = date.replace(hour=23, minute=59, second=59)
    
    sales = Sale.query.filter(Sale.date >= start, Sale.date <= end).all()
    expenses = Expense.query.filter(Expense.date >= start, Expense.date <= end).all()
    
    total_sales = sum(s.total_usd for s in sales)
    total_profit = sum(s.profit_usd for s in sales)
    total_expenses = sum(e.amount_usd for e in expenses)
    net = total_profit - total_expenses
    
    html = f"""
    <!DOCTYPE html>
    <html>
    <head>
        <style>
            body {{ font-family: Arial, sans-serif; }}
            .header {{ background: #059669; color: white; padding: 20px; text-align: center; }}
            .content {{ padding: 20px; }}
            .kpi {{ display: flex; gap: 20px; flex-wrap: wrap; }}
            .kpi-item {{ background: #F9FAFB; padding: 15px; border-radius: 8px; flex: 1; min-width: 150px; }}
            .kpi-item .label {{ color: #6B7280; font-size: 12px; }}
            .kpi-item .value {{ font-size: 24px; font-weight: bold; color: #059669; }}
        </style>
    </head>
    <body>
        <div class="header">
            <h1>{app.config['STORE_NAME']}</h1>
            <p>Daily Report - {date.strftime('%Y-%m-%d')}</p>
        </div>
        
        <div class="content">
            <div class="kpi">
                <div class="kpi-item">
                    <div class="label">💰 Revenue</div>
                    <div class="value">${total_sales:.2f}</div>
                </div>
                <div class="kpi-item">
                    <div class="label">📈 Profit</div>
                    <div class="value">${total_profit:.2f}</div>
                </div>
                <div class="kpi-item">
                    <div class="label">💸 Expenses</div>
                    <div class="value">${total_expenses:.2f}</div>
                </div>
                <div class="kpi-item">
                    <div class="label">📊 Net Profit</div>
                    <div class="value">${net:.2f}</div>
                </div>
            </div>
            
            <h3>📋 Sales Summary</h3>
            <p>Total Sales: {len(sales)} transactions</p>
            <p>Items Sold: {sum(s.quantity for s in sales)} units</p>
            
            <h3>💵 Expenses</h3>
            <ul>
                {"".join(f"<li>{e.title}: ${e.amount_usd:.2f}</li>" for e in expenses)}
            </ul>
        </div>
    </body>
    </html>
    """
    
    msg = Message(
        subject=f"Daily Report - {date.strftime('%Y-%m-%d')}",
        recipients=recipients,
        html=html
    )
    mail.send(msg)