# routes/customers.py
from flask import render_template, request, flash, redirect, url_for, jsonify, session
from app import app, csrf
from database import db
from models.customer import Customer, CustomerPurchase
from models.core import Sale
from decorators import login_required, role_required
from datetime import datetime

@app.route('/customers')
@login_required
def customers():
    page = request.args.get('page', 1, type=int)
    search = request.args.get('search', '')
    
    query = Customer.query
    if search:
        query = query.filter(
            (Customer.name.ilike(f'%{search}%')) |
            (Customer.email.ilike(f'%{search}%')) |
            (Customer.phone.ilike(f'%{search}%'))
        )
    
    customers = query.order_by(Customer.created_at.desc()).paginate(
        page=page, per_page=20, error_out=False
    )
    
    # Get unread alerts for the header
    from models.core import Notification
    unread_alerts = Notification.query.filter(
        ((Notification.user_id == session['user_id']) | (Notification.user_id == None)) &
        (Notification.is_read == False)
    ).count()
    
    notifications = Notification.query.filter(
        (Notification.user_id == session['user_id']) | (Notification.user_id == None)
    ).order_by(Notification.created_at.desc()).limit(10).all()
    
    return render_template(
        'admin/customers.html',
        customers=customers,
        search=search,
        role=session.get('role'),
        username=session.get('username'),
        title='Customer Management',
        active_page='customers',
        unread_alerts=unread_alerts,
        notifications=notifications
    )


@app.route('/customer/<int:customer_id>')
@login_required
def customer_detail(customer_id):
    customer = Customer.query.get_or_404(customer_id)
    
    # Get all purchases with sale details
    purchases = CustomerPurchase.query.filter_by(
        customer_id=customer.id
    ).order_by(CustomerPurchase.purchased_at.desc()).all()
    
    # Calculate customer stats
    total_spent = customer.total_spent or 0
    total_orders = customer.total_orders or 0
    loyalty_points = customer.loyalty_points or 0
    tier = customer.get_loyalty_tier()
    
    # Get last purchase date
    last_purchase = customer.last_purchase
    
    # Get all sales for this customer
    sales = Sale.query.filter_by(customer_id=customer.id).order_by(Sale.date.desc()).all()
    
    return render_template(
        'admin/customer_detail.html',
        customer=customer,
        purchases=purchases,
        sales=sales,  # Pass all sales
        total_spent=total_spent,
        total_orders=total_orders,
        loyalty_points=loyalty_points,
        tier=tier,
        last_purchase=last_purchase,
        role=session.get('role'),
        username=session.get('username'),
        title='Customer Details',
        active_page='customers'
    )

@app.route('/add_customer', methods=['POST'])
@login_required
@role_required('owner', 'admin')
def add_customer():
    name = request.form.get('name', '').strip()
    email = request.form.get('email', '').strip()
    phone = request.form.get('phone', '').strip()
    address = request.form.get('address', '').strip()
    city = request.form.get('city', '').strip()
    country = request.form.get('country', 'Sierra Leone')
    
    if not name:
        flash('Customer name is required.')
        return redirect(url_for('customers'))
    
    if email:
        existing = Customer.query.filter_by(email=email).first()
        if existing:
            flash(f'A customer with email {email} already exists.')
            return redirect(url_for('customers'))
    
    customer = Customer(
        name=name,
        email=email or None,
        phone=phone or None,
        address=address or None,
        city=city or None,
        country=country
    )
    
    db.session.add(customer)
    db.session.commit()
    
    flash(f'Customer {name} added successfully!')
    return redirect(url_for('customers'))

@app.route('/edit_customer/<int:customer_id>', methods=['POST'])
@login_required
@role_required('owner', 'admin')
def edit_customer(customer_id):
    customer = Customer.query.get_or_404(customer_id)
    
    customer.name = request.form.get('name', customer.name).strip()
    customer.email = request.form.get('email', customer.email).strip() or None
    customer.phone = request.form.get('phone', customer.phone).strip() or None
    customer.address = request.form.get('address', customer.address).strip() or None
    customer.city = request.form.get('city', customer.city).strip() or None
    customer.country = request.form.get('country', customer.country)
    customer.notes = request.form.get('notes', customer.notes).strip() or None
    
    db.session.commit()
    flash('Customer updated successfully!')
    return redirect(url_for('customer_detail', customer_id=customer.id))

@app.route('/api/customers/search')
@login_required
def api_search_customers():
    query = request.args.get('q', '')
    if len(query) < 2:
        return jsonify([])
    
    customers = Customer.query.filter(
        Customer.name.ilike(f'%{query}%')
    ).limit(10).all()
    
    return jsonify([{
        'id': c.id,
        'name': c.name,
        'email': c.email,
        'phone': c.phone,
        'loyalty_tier': c.get_loyalty_tier()
    } for c in customers])

@app.route('/api/customers/<int:customer_id>/add_points', methods=['POST'])
@login_required
@role_required('owner', 'admin')
def add_customer_points(customer_id):
    customer = Customer.query.get_or_404(customer_id)
    points = request.json.get('points', 0)
    
    if points <= 0:
        return jsonify({'error': 'Points must be positive'}), 400
    
    customer.loyalty_points += points
    db.session.commit()
    
    return jsonify({
        'status': 'success',
        'loyalty_points': customer.loyalty_points,
        'tier': customer.get_loyalty_tier()
    })