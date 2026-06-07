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

    investments = db.relationship(
        "Investment",
        backref="finance",
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

    __table_args__ = (
        db.UniqueConstraint('year', 'month', name='uix_year_month'),
    )