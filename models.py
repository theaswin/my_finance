from flask_sqlalchemy import SQLAlchemy
from datetime import date

db = SQLAlchemy()


class Finance(db.Model):
    __tablename__ = "finance"

    id = db.Column(db.Integer, primary_key=True)

    name = db.Column(
        db.String(100),
        nullable=False
    )

    logo = db.Column(
        db.String(500)
    )

    status = db.Column(
        db.String(20),
        default='growing'
    )

    sub_categories = db.relationship(
        "SubCategory",
        back_populates="finance",
        lazy=True,
        cascade="all, delete-orphan"
    )

    investments = db.relationship(
        "Investment",
        back_populates="finance",
        lazy=True,
        cascade="all, delete-orphan"
    )


class SubCategory(db.Model):
    __tablename__ = "sub_category"

    id = db.Column(db.Integer, primary_key=True)

    name = db.Column(
        db.String(100),
        nullable=False
    )

    icon = db.Column(
        db.String(500)
    )

    finance_id = db.Column(
        db.Integer,
        db.ForeignKey("finance.id"),
        nullable=False
    )

    finance = db.relationship(
        "Finance",
        back_populates="sub_categories"
    )

    payments = db.relationship(
        "Investment",
        back_populates="subcategory",
        lazy=True,
        cascade="all, delete-orphan"
    )


class Investment(db.Model):
    __tablename__ = "investment"

    id = db.Column(
        db.Integer,
        primary_key=True
    )

    amount = db.Column(
        db.Float,
        default=0
    )

    description = db.Column(
        db.String(255)
    )

    investment_date = db.Column(
        db.Date,
        default=date.today
    )

    finance_id = db.Column(
        db.Integer,
        db.ForeignKey("finance.id"),
        nullable=False
    )

    subcategory_id = db.Column(
        db.Integer,
        db.ForeignKey("sub_category.id"),
        nullable=False
    )

    finance = db.relationship(
        "Finance",
        back_populates="investments"
    )

    subcategory = db.relationship(
        "SubCategory",
        back_populates="payments"
    )


class PortfolioGrowth(db.Model):
    __tablename__ = "portfolio_growth"

    id = db.Column(
        db.Integer,
        primary_key=True
    )

    date = db.Column(
        db.String(20),
        nullable=False,
        unique=True,
        doc="Month-year label stored as 'YYYY Mon', e.g. 2026 Jun"
    )

    year = db.Column(
        db.Integer,
        nullable=False
    )

    month = db.Column(
        db.Integer,
        nullable=False
    )

    total = db.Column(
        db.Float,
        default=0
    )

    # Optional JSON metadata to store per-category breakdowns or monthly additions
    meta = db.Column(
        db.Text,
        nullable=True,
        doc="JSON string with optional keys: 'category_series', 'monthly'"
    )

    __table_args__ = (
        db.UniqueConstraint('year', 'month', name='uix_year_month'),
    )