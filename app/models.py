from app import db
from datetime import datetime
from sqlalchemy.schema import CheckConstraint, UniqueConstraint
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
    properties = db.relationship(
        "Property",
        back_populates="user",
        lazy=True,
        cascade="all, delete-orphan",
    )
    mutual_fund_folios = db.relationship(
        "MutualFundFolio",
        back_populates="user",
        lazy=True,
        cascade="all, delete-orphan",
    )

    stock_transactions = db.relationship(
        "StockTransaction",
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


class Property(db.Model):
    """
    Model for a single real estate property holding.
    """

    __tablename__ = "properties"

    id = db.Column(db.Integer, primary_key=True)
    user_id = db.Column(db.Integer, db.ForeignKey("users.id"), nullable=False)
    name = db.Column(db.String(255), nullable=False)
    property_type = db.Column(db.String(50), nullable=False)
    address = db.Column(db.Text, nullable=True)
    city = db.Column(db.String(100), nullable=True)
    area = db.Column(db.Numeric(10, 2), nullable=True)
    area_unit = db.Column(db.String(20), nullable=True)
    purchase_date = db.Column(db.Date, nullable=False)
    purchase_value = db.Column(db.Numeric(14, 2), nullable=False)
    registration_cost = db.Column(db.Numeric(14, 2), nullable=True, default=0)
    other_costs = db.Column(db.Numeric(14, 2), nullable=True, default=0)
    notes = db.Column(db.Text, nullable=True)
    sell_date = db.Column(db.Date, nullable=True)
    sell_value = db.Column(db.Numeric(14, 2), nullable=True)
    selling_costs = db.Column(db.Numeric(14, 2), nullable=True, default=0)
    created_at = db.Column(db.DateTime, default=datetime.utcnow)
    updated_at = db.Column(
        db.DateTime, default=datetime.utcnow, onupdate=datetime.utcnow
    )

    user = db.relationship("User", back_populates="properties")
    valuations = db.relationship(
        "PropertyValuation",
        back_populates="property",
        lazy="dynamic",
        cascade="all, delete-orphan",
    )
    expenses = db.relationship(
        "PropertyExpense",
        back_populates="property",
        lazy="dynamic",
        cascade="all, delete-orphan",
    )

    __table_args__ = (
        CheckConstraint(
            "purchase_value >= 0",
            name="check_property_purchase_value_non_negative",
        ),
        CheckConstraint("area > 0", name="check_property_area_positive"),
    )

    # --- Helper Properties ---
    @property
    def total_purchase_cost(self):
        """Initial cost to acquire the property."""
        return (
            self.purchase_value
            + (self.registration_cost or 0)
            + (self.other_costs or 0)
        )

    @property
    def total_capital_improvements(self):
        """Total cost of renovations/improvements."""
        total = (
            db.session.query(db.func.sum(PropertyExpense.amount))
            .filter_by(property_id=self.id, is_capital_improvement=True)
            .scalar()
        )
        return total or Decimal("0.0")

    @property
    def total_cost_basis(self):
        """Full cost basis for P&L calculation."""
        return self.total_purchase_cost + self.total_capital_improvements

    @property
    def latest_valuation(self):
        """Get the most recent valuation entry."""
        return self.valuations.order_by(
            PropertyValuation.valuation_date.desc()
        ).first()

    def __repr__(self):
        return f"<Property(Name: '{self.name}', User: {self.user_id})>"


class PropertyValuation(db.Model):
    """
    Model for manually recording the estimated value of a property over time.
    """

    __tablename__ = "property_valuations"

    id = db.Column(db.Integer, primary_key=True)
    property_id = db.Column(
        db.Integer, db.ForeignKey("properties.id"), nullable=False
    )
    valuation_date = db.Column(db.Date, nullable=False)
    estimated_value = db.Column(db.Numeric(14, 2), nullable=False)
    source = db.Column(db.String(100), nullable=True)
    created_at = db.Column(db.DateTime, default=datetime.utcnow)

    property = db.relationship("Property", back_populates="valuations")

    __table_args__ = (
        CheckConstraint(
            "estimated_value >= 0", name="check_valuation_value_non_negative"
        ),
    )

    def __repr__(self):
        return f"<PropertyValuation(Property: {self.property_id}, Date: {self.valuation_date}, Value: {self.estimated_value})>"


class PropertyExpense(db.Model):
    """
    Model for tracking expenses and capital improvements for a property.
    """

    __tablename__ = "property_expenses"

    id = db.Column(db.Integer, primary_key=True)
    property_id = db.Column(
        db.Integer, db.ForeignKey("properties.id"), nullable=False
    )
    expense_date = db.Column(db.Date, nullable=False)
    amount = db.Column(db.Numeric(10, 2), nullable=False)
    expense_type = db.Column(
        db.String(50), nullable=False
    )  # 'Maintenance', 'Property Tax', 'Renovation', 'Utility'
    description = db.Column(db.Text, nullable=True)
    is_capital_improvement = db.Column(
        db.Boolean, nullable=False, default=False
    )
    created_at = db.Column(db.DateTime, default=datetime.utcnow)

    property = db.relationship("Property", back_populates="expenses")

    __table_args__ = (
        CheckConstraint("amount > 0", name="check_expense_amount_positive"),
    )

    def __repr__(self):
        return f"<PropertyExpense(Property: {self.property_id}, Type: {self.expense_type}, Amount: {self.amount})>"


class MutualFundScheme(db.Model):
    """
    Represents a specific Mutual Fund scheme.
    Identified primarily by ISIN.
    """

    __tablename__ = "mutual_fund_schemes"

    id = db.Column(db.Integer, primary_key=True)
    name = db.Column(db.String(255), nullable=False, index=True)
    isin = db.Column(
        db.String(20), unique=True, nullable=False, index=True
    )  # Primary unique identifier
    amfi_code = db.Column(
        db.String(20), unique=True, nullable=True, index=True
    )  # Alternate identifier
    rta_code = db.Column(
        db.String(50), nullable=True
    )  # Registrar Transfer Agent Code
    rta = db.Column(
        db.String(50), nullable=True
    )  # Registrar Transfer Agent Name
    type = db.Column(
        db.String(50), nullable=True
    )  # e.g., Equity, Debt, Hybrid

    # Relationships
    transactions = db.relationship(
        "MutualFundTransaction",
        back_populates="scheme",
        lazy="dynamic",
        cascade="all, delete-orphan",
    )
    navs = db.relationship(
        "MutualFundNAV",
        back_populates="scheme",
        lazy="dynamic",
        cascade="all, delete-orphan",
    )

    def __repr__(self):
        return f"<MutualFundScheme(ISIN='{self.isin}', Name='{self.name[:30]}...')>"


class MutualFundFolio(db.Model):
    """
    Represents a folio (account) with a specific AMC for a user.
    """

    __tablename__ = "mutual_fund_folios"

    id = db.Column(db.Integer, primary_key=True)
    user_id = db.Column(
        db.Integer, db.ForeignKey("users.id"), nullable=False, index=True
    )
    folio_number = db.Column(db.String(50), nullable=False, index=True)
    amc = db.Column(db.String(100), nullable=False)

    # Relationships
    user = db.relationship("User", back_populates="mutual_fund_folios")
    transactions = db.relationship(
        "MutualFundTransaction",
        back_populates="folio",
        lazy="dynamic",
        cascade="all, delete-orphan",
    )

    # Ensure a user doesn't have the same folio number twice with the same AMC
    __table_args__ = (
        UniqueConstraint(
            "user_id", "amc", "folio_number", name="uq_user_amc_folio"
        ),
    )

    def __repr__(self):
        return f"<MutualFundFolio(User: {self.user_id}, Folio: '{self.folio_number}', AMC: '{self.amc}')>"


class MutualFundTransaction(db.Model):
    """
    Represents a single transaction within a folio for a specific scheme.
    Includes buy, sell, dividend reinvestment, etc.
    """

    __tablename__ = "mutual_fund_transactions"

    id = db.Column(db.Integer, primary_key=True)
    folio_id = db.Column(
        db.Integer,
        db.ForeignKey("mutual_fund_folios.id"),
        nullable=False,
        index=True,
    )
    scheme_id = db.Column(
        db.Integer,
        db.ForeignKey("mutual_fund_schemes.id"),
        nullable=False,
        index=True,
    )

    transaction_date = db.Column(db.Date, nullable=False, index=True)
    amount = db.Column(
        db.Numeric(12, 2), nullable=True
    )  # Can be null for certain types like switch out
    units = db.Column(
        db.Numeric(14, 4), nullable=True
    )  # Can be null for dividend payout
    nav = db.Column(
        db.Numeric(10, 4), nullable=True
    )  # NAV at transaction time
    type = db.Column(
        db.String(50), nullable=False, index=True
    )  # PURCHASE, REDEMPTION

    # --- For Deduplication ---
    # Hash created from key immutable fields to prevent duplicate entries from CAS uploads
    # Example fields: folio_id, scheme_id, date, type, units, amount
    unique_hash = db.Column(
        db.String(64), unique=True, nullable=False, index=True
    )

    # Relationships
    folio = db.relationship("MutualFundFolio", back_populates="transactions")
    scheme = db.relationship(
        "MutualFundScheme", back_populates="transactions"
    )

    __table_args__ = (
        CheckConstraint(
            "amount >= 0", name="check_mf_tx_amount_non_negative"
        ),
        # Units can be negative for redemptions/switches
        # NAV should be positive if present
        CheckConstraint(
            "nav > 0 OR nav IS NULL", name="check_mf_tx_nav_positive"
        ),
    )

    def __repr__(self):
        units_str = f"{self.units:.4f}" if self.units is not None else "N/A"
        amount_str = (
            f"{self.amount:.2f}" if self.amount is not None else "N/A"
        )
        return f"<MutualFundTransaction(Date: {self.transaction_date}, Type: {self.type}, Units: {units_str}, Amount: {amount_str})>"


class MutualFundNAV(db.Model):
    """
    Stores the historical Net Asset Value (NAV) for a scheme on a specific date.
    """

    __tablename__ = "mutual_fund_navs"

    id = db.Column(db.Integer, primary_key=True)
    scheme_id = db.Column(
        db.Integer,
        db.ForeignKey("mutual_fund_schemes.id"),
        nullable=False,
        index=True,
    )
    nav_date = db.Column(db.Date, nullable=False, index=True)
    nav = db.Column(db.Numeric(10, 4), nullable=False)

    # Relationship
    scheme = db.relationship("MutualFundScheme", back_populates="navs")

    # Ensure only one NAV entry per scheme per date
    __table_args__ = (
        UniqueConstraint("scheme_id", "nav_date", name="uq_scheme_date_nav"),
        CheckConstraint("nav > 0", name="check_mf_nav_positive"),
    )

    def __repr__(self):
        return f"<MutualFundNAV(Scheme: {self.scheme_id}, Date: {self.nav_date}, NAV: {self.nav})>"


class Stock(db.Model):
    """
    Represents a unique stock, ETF, or security.
    Uniquely identified by its ISIN.
    """

    __tablename__ = "stocks"

    id = db.Column(db.Integer, primary_key=True)
    isin = db.Column(db.String(20), unique=True, nullable=False, index=True)
    symbol = db.Column(db.String(50), nullable=False, index=True)
    name = db.Column(
        db.String(255), nullable=True
    )  # e.g., "Titagharh Rail Systems Ltd."
    segment = db.Column(db.String(10), nullable=True)  # e.g., "EQ"
    series = db.Column(db.String(10), nullable=True)  # e.g., "EQ"

    # Relationships
    transactions = db.relationship(
        "StockTransaction",
        back_populates="stock",
        lazy="dynamic",
        cascade="all, delete-orphan",
    )
    valuations = db.relationship(
        "StockValuation",
        back_populates="stock",
        lazy="dynamic",
        cascade="all, delete-orphan",
    )

    def __repr__(self):
        return f"<Stock(Symbol: '{self.symbol}', ISIN: '{self.isin}')>"


class StockTransaction(db.Model):
    """
    Represents a single stock trade (buy or sell) by a user.
    """

    __tablename__ = "stock_transactions"

    id = db.Column(db.Integer, primary_key=True)
    user_id = db.Column(
        db.Integer, db.ForeignKey("users.id"), nullable=False, index=True
    )
    stock_id = db.Column(
        db.Integer, db.ForeignKey("stocks.id"), nullable=False, index=True
    )

    trade_date = db.Column(db.Date, nullable=False, index=True)
    trade_type = db.Column(db.String(10), nullable=False)  # 'buy' or 'sell'
    quantity = db.Column(db.Numeric(14, 4), nullable=False)
    price = db.Column(db.Numeric(14, 4), nullable=False)

    exchange = db.Column(db.String(10), nullable=True)  # "NSE", "BSE"

    # --- For Deduplication ---
    # trade_id is unique per broker trade. Can be NULL for manual entries.
    trade_id = db.Column(db.String(50), nullable=True, index=True)
    order_id = db.Column(db.String(50), nullable=True, index=True)
    order_execution_time = db.Column(db.DateTime, nullable=True)

    # Relationships
    user = db.relationship("User", back_populates="stock_transactions")
    stock = db.relationship("Stock", back_populates="transactions")

    __table_args__ = (
        # A user cannot have the same trade_id twice.
        UniqueConstraint("user_id", "trade_id", name="uq_user_trade_id"),
        CheckConstraint(
            "quantity > 0", name="check_stock_tx_quantity_positive"
        ),
        CheckConstraint("price >= 0", name="check_stock_tx_price_positive"),
    )

    def __repr__(self):
        return f"<StockTransaction(User: {self.user_id}, Type: {self.trade_type}, Qty: {self.quantity}, Price: {self.price})>"


class StockValuation(db.Model):
    """
    Stores manual or API-fetched prices for a stock on a given date.
    """

    __tablename__ = "stock_valuations"

    id = db.Column(db.Integer, primary_key=True)
    stock_id = db.Column(
        db.Integer, db.ForeignKey("stocks.id"), nullable=False, index=True
    )
    valuation_date = db.Column(db.Date, nullable=False, index=True)
    price = db.Column(db.Numeric(14, 4), nullable=False)
    source = db.Column(
        db.String(50), nullable=True, default="Manual"
    )  # "Manual", "API_LIVE"

    # Relationship
    stock = db.relationship("Stock", back_populates="valuations")

    __table_args__ = (
        # One price per stock per day
        UniqueConstraint(
            "stock_id", "valuation_date", name="uq_stock_date_price"
        ),
        CheckConstraint(
            "price > 0", name="check_stock_valuation_price_positive"
        ),
    )

    def __repr__(self):
        return f"<StockValuation(Stock: {self.stock_id}, Date: {self.valuation_date}, Price: {self.price})>"
