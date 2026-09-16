# models/store.py
from database import db
from datetime import datetime
import uuid

class StoreOrder(db.Model):
    __tablename__ = 'store_order'
    
    id = db.Column(db.Integer, primary_key=True)
    order_number = db.Column(db.String(50), unique=True, nullable=False)
    
    # Customer info
    customer_id = db.Column(db.Integer, db.ForeignKey('customer.id'), nullable=True)
    customer_name = db.Column(db.String(100), nullable=False)
    customer_email = db.Column(db.String(100))
    customer_phone = db.Column(db.String(20))
    
    # Order details
    total_amount = db.Column(db.Float, nullable=False)
    status = db.Column(db.String(20), default='pending')
    # pending, confirmed, processing, shipped, delivered, cancelled
    
    # Shipping
    shipping_address = db.Column(db.Text)
    shipping_city = db.Column(db.String(50))
    shipping_country = db.Column(db.String(50), default='Sierra Leone')
    tracking_number = db.Column(db.String(100))
    
    # Payment
    payment_method = db.Column(db.String(50))
    payment_status = db.Column(db.String(20), default='pending')
    
    # Notes
    notes = db.Column(db.Text)
    admin_notes = db.Column(db.Text)
    
    # Timestamps
    created_at = db.Column(db.DateTime, default=datetime.utcnow)
    updated_at = db.Column(db.DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)
    shipped_at = db.Column(db.DateTime)
    delivered_at = db.Column(db.DateTime)
    
    # Relationships
    items = db.relationship('StoreOrderItem', backref='order', lazy=True, cascade='all, delete-orphan')
    customer = db.relationship('Customer', backref='orders')
    
    def __init__(self, **kwargs):
        super().__init__(**kwargs)
        if not self.order_number:
            self.order_number = f"MS-{datetime.now().strftime('%Y%m%d')}-{uuid.uuid4().hex[:8].upper()}"
    
    def __repr__(self):
        return f'<Order {self.order_number}>'

class StoreOrderItem(db.Model):
    __tablename__ = 'store_order_item'
    
    id = db.Column(db.Integer, primary_key=True)
    order_id = db.Column(db.Integer, db.ForeignKey('store_order.id'), nullable=False)
    shoe_id = db.Column(db.Integer, db.ForeignKey('shoe.id'), nullable=True)
    product_id = db.Column(db.Integer, db.ForeignKey('product.id'), nullable=True)
    
    product_name = db.Column(db.String(100), nullable=False)
    product_sku = db.Column(db.String(50))
    quantity = db.Column(db.Integer, nullable=False)
    price_usd = db.Column(db.Float, nullable=False)
    total_usd = db.Column(db.Float, nullable=False)
    
    shoe = db.relationship('Shoe')
    product = db.relationship('Product')