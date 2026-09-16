# models/customer.py
from database import db
from datetime import datetime

class Customer(db.Model):
    __tablename__ = 'customer'
    
    id = db.Column(db.Integer, primary_key=True)
    name = db.Column(db.String(100), nullable=False)
    email = db.Column(db.String(100), unique=True)
    phone = db.Column(db.String(20))
    address = db.Column(db.String(255))
    city = db.Column(db.String(50))
    country = db.Column(db.String(50), default='Sierra Leone')
    # REMOVED: sales = db.relationship('Sale', backref='customer', lazy=True)
    # This is already defined as a backref in models/core.py

    # Loyalty
    loyalty_points = db.Column(db.Integer, default=0)
    total_spent = db.Column(db.Float, default=0.0)
    total_orders = db.Column(db.Integer, default=0)
    
    # Timestamps
    created_at = db.Column(db.DateTime, default=datetime.utcnow)
    last_purchase = db.Column(db.DateTime)
    
    # Notes
    notes = db.Column(db.Text)
    
    def __repr__(self):
        return f'<Customer {self.name}>'
    
    def get_loyalty_tier(self):
        if self.total_spent >= 1000:
            return 'Gold'
        elif self.total_spent >= 500:
            return 'Silver'
        elif self.total_spent >= 100:
            return 'Bronze'
        return 'New'
    
    def add_points(self, amount):
        self.loyalty_points += int(amount * 0.1)  # 10% back as points

class CustomerPurchase(db.Model):
    __tablename__ = 'customer_purchase'
    
    id = db.Column(db.Integer, primary_key=True)
    customer_id = db.Column(db.Integer, db.ForeignKey('customer.id'))
    sale_id = db.Column(db.Integer, db.ForeignKey('sale.id'))
    total_usd = db.Column(db.Float)
    purchased_at = db.Column(db.DateTime, default=datetime.utcnow)
    
    customer = db.relationship('Customer', backref='purchases')
    sale = db.relationship('Sale')
    
    def __repr__(self):
        return f'<Purchase {self.id} - ${self.total_usd}>'