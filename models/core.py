# models/core.py - Unified product identity across all categories
from database import db
from datetime import datetime
import json

class Role(db.Model):
    __tablename__ = 'role'
    id = db.Column(db.Integer, primary_key=True)
    name = db.Column(db.String(20), unique=True, nullable=False)

class User(db.Model):
    __tablename__ = 'user'
    id = db.Column(db.Integer, primary_key=True)
    username = db.Column(db.String(50), unique=True, nullable=False)
    password_hash = db.Column(db.String(200), nullable=False)
    role_id = db.Column(db.Integer, db.ForeignKey('role.id'), nullable=False)
    role = db.relationship('Role')
    email = db.Column(db.String(100))
    is_active = db.Column(db.Boolean, default=True)
    created_at = db.Column(db.DateTime, default=datetime.utcnow)
    last_login = db.Column(db.DateTime)

class Shoe(db.Model):
    __tablename__ = 'shoe'
    id = db.Column(db.Integer, primary_key=True)
    brand = db.Column(db.String(150), nullable=False)   # Product Name / Brand
    model = db.Column(db.String(150), nullable=False)   # required for shoes
    category = db.Column(db.String(50), default='shoe')
    image_url = db.Column(db.String(500), default='https://via.placeholder.com/200x200?text=No+Image')
    sku = db.Column(db.String(50), unique=True)
    is_active = db.Column(db.Boolean, default=True)
    created_at = db.Column(db.DateTime, default=datetime.utcnow)
    updated_at = db.Column(db.DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)

    sizes = db.relationship('ShoeSize', backref='shoe', lazy=True, cascade='all, delete-orphan')

    __table_args__ = (
        db.UniqueConstraint('brand', 'model', name='uq_shoe_brand_model'),
    )

    def display_name(self):
        return f"{self.brand} {self.model}".strip()

    def get_total_quantity(self):
        return sum(size.quantity for size in self.sizes)

    def get_available_sizes(self):
        return [size for size in self.sizes if size.quantity > 0]

    def get_size_by_name(self, size_name):
        for size in self.sizes:
            if size.size.lower() == str(size_name).lower():
                return size
        return None

class ShoeSize(db.Model):
    __tablename__ = 'shoe_size'
    id = db.Column(db.Integer, primary_key=True)
    shoe_id = db.Column(db.Integer, db.ForeignKey('shoe.id'), nullable=False)
    size = db.Column(db.String(50), nullable=False)
    quantity = db.Column(db.Integer, default=0)
    cost_usd = db.Column(db.Float, nullable=False)
    sell_usd = db.Column(db.Float, nullable=False)
    created_at = db.Column(db.DateTime, default=datetime.utcnow)
    updated_at = db.Column(db.DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)

class Product(db.Model):
    __tablename__ = 'product'
    id = db.Column(db.Integer, primary_key=True)

    # ---- Unified identity fields (used across ALL non-shoe categories) ----
    brand = db.Column(db.String(150), nullable=False)   # Product Name / Brand
    model = db.Column(db.String(150), nullable=False, default='')  # optional -> empty string
    category = db.Column(db.String(50), nullable=False)

    image_url = db.Column(db.String(500), default='https://via.placeholder.com/200x200?text=No+Image')
    sku = db.Column(db.String(50), unique=True)
    is_active = db.Column(db.Boolean, default=True)
    created_at = db.Column(db.DateTime, default=datetime.utcnow)

    # Additional non-identifying attributes (specs, expiry, description)
    attributes = db.Column(db.Text, default='{}')

    variants = db.relationship('ProductVariant', backref='product',
                               lazy=True, cascade='all, delete-orphan')

    __table_args__ = (
        db.UniqueConstraint('category', 'brand', 'model',
                            name='uq_product_category_brand_model'),
    )

    def display_name(self):
        if self.model:
            return f"{self.brand} {self.model}".strip()
        return self.brand

    def get_attributes(self):
        try:
            return json.loads(self.attributes) if self.attributes else {}
        except Exception:
            return {}

    def set_attributes(self, data):
        self.attributes = json.dumps(data or {})

    def get_total_quantity(self):
        return sum(v.quantity for v in self.variants)

    def get_available_variants(self):
        return [v for v in self.variants if v.quantity > 0]

    def get_variant_by_value(self, variant_value):
        for v in self.variants:
            if v.variant_value.lower() == str(variant_value).lower():
                return v
        return None

class ProductVariant(db.Model):
    __tablename__ = 'product_variant'
    id = db.Column(db.Integer, primary_key=True)
    product_id = db.Column(db.Integer, db.ForeignKey('product.id'), nullable=False)
    variant_label = db.Column(db.String(50), nullable=False)   # "size", "weight", "color"
    variant_value = db.Column(db.String(50), nullable=False)
    quantity = db.Column(db.Integer, default=0)
    cost_usd = db.Column(db.Float, nullable=False)
    sell_usd = db.Column(db.Float, nullable=False)
    created_at = db.Column(db.DateTime, default=datetime.utcnow)
    updated_at = db.Column(db.DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)

class Sale(db.Model):
    __tablename__ = 'sale'
    id = db.Column(db.Integer, primary_key=True)
    shoe_id = db.Column(db.Integer, db.ForeignKey('shoe.id'), nullable=True)
    shoe_size_id = db.Column(db.Integer, db.ForeignKey('shoe_size.id'), nullable=True)
    product_id = db.Column(db.Integer, db.ForeignKey('product.id'), nullable=True)
    product_variant_id = db.Column(db.Integer, db.ForeignKey('product_variant.id'), nullable=True)
    size = db.Column(db.String(50), nullable=True)
    quantity = db.Column(db.Integer, nullable=False)
    total_usd = db.Column(db.Float, nullable=False)
    profit_usd = db.Column(db.Float, nullable=False)
    sold_by = db.Column(db.String(50))
    date = db.Column(db.DateTime, default=datetime.utcnow)
    customer_id = db.Column(db.Integer, db.ForeignKey('customer.id'), nullable=True)
    sale_type = db.Column(db.String(20), default='in_store')
    notes = db.Column(db.Text)

    shoe = db.relationship('Shoe')
    shoe_size = db.relationship('ShoeSize')
    product = db.relationship('Product')
    product_variant = db.relationship('ProductVariant')
    customer = db.relationship('Customer', backref='sales')

class Expense(db.Model):
    __tablename__ = 'expense'
    id = db.Column(db.Integer, primary_key=True)
    title = db.Column(db.String(100))
    amount_usd = db.Column(db.Float)
    date = db.Column(db.DateTime, default=datetime.utcnow)
    category = db.Column(db.String(50))
    description = db.Column(db.Text)
    receipt_url = db.Column(db.String(255))

class AuditLog(db.Model):
    __tablename__ = 'audit_log'
    id = db.Column(db.Integer, primary_key=True)
    user = db.Column(db.String(50))
    action = db.Column(db.String(200))
    timestamp = db.Column(db.DateTime, default=datetime.utcnow)

class Notification(db.Model):
    __tablename__ = 'notification'
    id = db.Column(db.Integer, primary_key=True)
    message = db.Column(db.String(255), nullable=False)
    category = db.Column(db.String(50))
    user_id = db.Column(db.Integer,
                        db.ForeignKey("user.id", ondelete="SET NULL"),
                        nullable=True)
    is_read = db.Column(db.Boolean, default=False)
    created_at = db.Column(db.DateTime, default=datetime.utcnow)
    user = db.relationship("User")

class Restock(db.Model):
    __tablename__ = 'restock'
    id = db.Column(db.Integer, primary_key=True)
    shoe_id = db.Column(db.Integer, db.ForeignKey('shoe.id'), nullable=True)
    shoe_size_id = db.Column(db.Integer, db.ForeignKey('shoe_size.id'), nullable=True)
    product_id = db.Column(db.Integer, db.ForeignKey('product.id'), nullable=True)
    product_variant_id = db.Column(db.Integer, db.ForeignKey('product_variant.id'), nullable=True)
    size = db.Column(db.String(50))
    quantity = db.Column(db.Integer)
    cost_usd = db.Column(db.Float)
    sell_usd = db.Column(db.Float)
    supplier = db.Column(db.String(100))
    created_at = db.Column(db.DateTime, default=datetime.utcnow)

    shoe = db.relationship('Shoe')
    shoe_size = db.relationship('ShoeSize')
    product = db.relationship('Product')
    product_variant = db.relationship('ProductVariant')