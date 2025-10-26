from app import db
from datetime import datetime
from sqlalchemy.schema import CheckConstraint
from decimal import Decimal


class User(db.Model):
    """
    Model for users.
    (No changes)
    """

    __tablename__ = "users"

    id = db.Column(db.Integer, primary_key=True)
    name = db.Column(db.String(100), nullable=False)
    email = db.Column(db.String(120), unique=True, nullable=False)
    phone_number = db.Column(db.String(20), unique=True, nullable=True)
    created_at = db.Column(db.DateTime, default=datetime.utcnow)
    updated_at = db.Column(
        db.DateTime, default=datetime.utcnow, onupdate=datetime.utcnow
    )

    # Relationships
    gold_transactions = db.relationship(
        "GoldTransaction",
        back_populates="user",
        lazy=True,
        cascade="all, delete-orphan",
    )
    gold_sell_transactions = db.relationship(
        "GoldSellTransaction",
        back_populates="user",
        lazy=True,
        cascade="all, delete-orphan",
    )

    def __repr__(self):
        return f"User('{self.name}', '{self.email}')"


class GoldTransaction(db.Model):
    """
    Model for gold BUY transactions (Holdings).
    """

    __tablename__ = "gold_transactions"

    id = db.Column(db.Integer, primary_key=True)
    invoice_date = db.Column(db.Date, nullable=False)
    grams = db.Column(db.Numeric(10, 4), nullable=False)
    per_gm_price = db.Column(db.Numeric(10, 2), nullable=False)
    purity = db.Column(db.String(50), nullable=True)
    platform = db.Column(db.String(100), nullable=True)
    type = db.Column(db.String(50), nullable=False)
    brand = db.Column(db.String(100), nullable=True)
    notes = db.Column(db.Text, nullable=True)
    created_at = db.Column(db.DateTime, default=datetime.utcnow)
    updated_at = db.Column(
        db.DateTime, default=datetime.utcnow, onupdate=datetime.utcnow
    )

    user_id = db.Column(db.Integer, db.ForeignKey("users.id"), nullable=False)
    user = db.relationship("User", back_populates="gold_transactions")

    linked_sells = db.relationship(
        "GoldSellTransaction",
        back_populates="linked_buy",
        lazy="dynamic",
        cascade="all, delete-orphan",
    )

    __table_args__ = (
        CheckConstraint("grams > 0", name="check_grams_positive"),
        CheckConstraint("per_gm_price >= 0", name="check_price_non_negative"),
        # --- Updated Purity Options ---
        CheckConstraint(
            purity.in_(["18K", "22K", "24K"]), name="check_purity_values"
        ),
    )

    @property
    def sold_grams(self):
        """Calculates the total grams sold *specifically against this holding*."""
        total_sold = (
            db.session.query(db.func.sum(GoldSellTransaction.grams))
            .filter(GoldSellTransaction.linked_buy_id == self.id)
            .scalar()
        )
        return total_sold or Decimal("0.0")

    @property
    def remaining_grams(self):
        """Calculates the remaining grams for this specific holding."""
        return self.grams - self.sold_grams

    def __repr__(self):
        return f"GoldTransaction(User: {self.user_id}, Grams: {self.grams}, Date: {self.invoice_date})"


class GoldSellTransaction(db.Model):
    """
    Model for gold SELL transactions.
    """

    __tablename__ = "gold_sell_transactions"

    id = db.Column(db.Integer, primary_key=True)
    sell_date = db.Column(db.Date, nullable=False)
    grams = db.Column(db.Numeric(10, 4), nullable=False)
    sell_price_per_gram = db.Column(db.Numeric(10, 2), nullable=False)
    platform = db.Column(db.String(100), nullable=True)
    notes = db.Column(db.Text, nullable=True)
    created_at = db.Column(db.DateTime, default=datetime.utcnow)
    updated_at = db.Column(
        db.DateTime, default=datetime.utcnow, onupdate=datetime.utcnow
    )

    user_id = db.Column(db.Integer, db.ForeignKey("users.id"), nullable=False)
    user = db.relationship("User", back_populates="gold_sell_transactions")

    # --- This is now MANDATORY ---
    linked_buy_id = db.Column(
        db.Integer, db.ForeignKey("gold_transactions.id"), nullable=False
    )

    linked_buy = db.relationship(
        "GoldTransaction", back_populates="linked_sells"
    )

    __table_args__ = (
        CheckConstraint("grams > 0", name="check_sell_grams_positive"),
        CheckConstraint(
            "sell_price_per_gram >= 0", name="check_sell_price_non_negative"
        ),
    )

    def __repr__(self):
        return f"GoldSellTransaction(User: {self.user_id}, Grams: {self.grams}, Date: {self.sell_date})"


class GoldPrice(db.Model):
    # (No changes)
    __tablename__ = "gold_prices"
    id = db.Column(db.Integer, primary_key=True)
    date = db.Column(db.Date, unique=True, nullable=False)
    price_per_gram_24k = db.Column(db.Numeric(10, 2), nullable=False)
    source = db.Column(db.String(100), nullable=True)

    def __repr__(self):
        return f"GoldPrice({self.date}, {self.price_per_gram_24k})"
