from flask import Flask, render_template, request, redirect, jsonify
from datetime import datetime, date
from calendar import monthrange
import json
from sqlalchemy import func, extract, text
from collections import defaultdict
from models import db, Finance, Investment, PortfolioGrowth, SubCategory

app = Flask(__name__)

app.config["SQLALCHEMY_DATABASE_URI"] = "sqlite:///finance.db"
app.config["SQLALCHEMY_TRACK_MODIFICATIONS"] = False

db.init_app(app)


def ensure_finance_status_column():
    """Add the finance.status column if it does not exist in the SQLite schema."""
    engine = db.engine
    if engine.dialect.name == 'sqlite':
        with engine.connect() as conn:
            result = conn.execute(text("PRAGMA table_info(finance)"))
            existing_columns = {row[1] for row in result}
            if 'status' not in existing_columns:
                conn.execute(text("ALTER TABLE finance ADD COLUMN status VARCHAR(20) DEFAULT 'growing'"))
                conn.commit()


def ensure_subcategory_schema():
    """Ensure subcategory schema exists and migrate existing investments into a default subcategory."""
    engine = db.engine
    if engine.dialect.name == 'sqlite':
        with engine.connect() as conn:
            result = conn.execute(text("PRAGMA table_info(investment)"))
            existing_columns = {row[1] for row in result}
            if 'subcategory_id' not in existing_columns:
                conn.execute(text("ALTER TABLE investment ADD COLUMN subcategory_id INTEGER"))
                conn.commit()

            subcat_columns = conn.execute(text("PRAGMA table_info(sub_category)")).fetchall()
            subcat_existing_columns = {row[1] for row in subcat_columns}
            if 'icon' not in subcat_existing_columns:
                conn.execute(text("ALTER TABLE sub_category ADD COLUMN icon VARCHAR(500)"))
                conn.commit()

            # Ensure every finance has a default subcategory
            existing_finances = conn.execute(text("SELECT id FROM finance")).fetchall()
            for row in existing_finances:
                finance_id = row[0]
                subcat_exists = conn.execute(
                    text("SELECT 1 FROM sub_category WHERE finance_id = :fid LIMIT 1"),
                    {"fid": finance_id}
                ).fetchone()
                if not subcat_exists:
                    conn.execute(
                        text("INSERT INTO sub_category (name, finance_id) VALUES (:name, :fid)"),
                        {"name": "General", "fid": finance_id}
                    )
                    conn.commit()

            default_subcategories = conn.execute(text(
                "SELECT sub_category.id, sub_category.finance_id FROM sub_category"
            )).fetchall()
            default_by_finance = {row[1]: row[0] for row in default_subcategories}

            investments_to_update = conn.execute(
                text("SELECT id, finance_id FROM investment WHERE subcategory_id IS NULL")
            ).fetchall()
            for row in investments_to_update:
                inv_id, finance_id = row
                subcat_id = default_by_finance.get(finance_id)
                if subcat_id is not None:
                    conn.execute(
                        text("UPDATE investment SET subcategory_id = :scid WHERE id = :iid"),
                        {"scid": subcat_id, "iid": inv_id}
                    )
            conn.commit()


def ensure_portfolio_meta_column():
    """Ensure PortfolioGrowth.meta column exists (SQLite ALTER TABLE if needed)."""
    engine = db.engine
    if engine.dialect.name == 'sqlite':
        with engine.connect() as conn:
            cols = conn.execute(text("PRAGMA table_info(portfolio_growth)")).fetchall()
            existing = {row[1] for row in cols}
            if 'meta' not in existing:
                conn.execute(text("ALTER TABLE portfolio_growth ADD COLUMN meta TEXT"))
                conn.commit()


def sync_portfolio_growth():
    """Synchronize the PortfolioGrowth table for each month of the current year."""
    today = date.today()
    current_year = today.year
    month_names = ["Jan", "Feb", "Mar", "Apr", "May", "Jun", "Jul", "Aug", "Sep", "Oct", "Nov", "Dec"]
    
    all_investments = Investment.query.all()
    
    for m in range(1, 13):
        label = f"{current_year} {month_names[m-1]}"
        last_day = date(current_year, m, monthrange(current_year, m)[1])
        
        # Calculate the cumulative total up to the last day of this month
        total_for_month = sum(inv.amount for inv in all_investments if inv.investment_date <= last_day)
        
        # Check if record already exists
        record = PortfolioGrowth.query.filter_by(year=current_year, month=m).first()
        if record:
            record.total = round(total_for_month, 2)
            record.date = label
        else:
            new_record = PortfolioGrowth(
                date=label,
                year=current_year,
                month=m,
                total=round(total_for_month, 2)
            )
            db.session.add(new_record)
            
    db.session.commit()


def get_monthly_chart_data(current_net_worth=None):
    """Load monthly portfolio growth from PortfolioGrowth database table and calculate category breakdowns."""
    today = date.today()
    current_year = today.year

    month_names = ["Jan", "Feb", "Mar", "Apr", "May", "Jun", "Jul", "Aug", "Sep", "Oct", "Nov", "Dec"]
    labels = [f"{current_year} {mn}" for mn in month_names]

    cumulative = [None] * 12
    monthly = [None] * 12

    category_series = {}
    finances = Finance.query.all()
    for f in finances:
        category_series[f.name] = [None] * 12

    records = PortfolioGrowth.query.filter_by(year=current_year).order_by(PortfolioGrowth.month).all()
    last_total = None
    for record in records:
        index = record.month - 1
        cumulative[index] = round(record.total, 2)

        if last_total is None:
            monthly[index] = round(record.total, 2)
        else:
            monthly[index] = round(record.total - last_total, 2)

        if record.meta:
            try:
                meta = json.loads(record.meta)
                if isinstance(meta.get('monthly_value'), (int, float)):
                    monthly[index] = round(meta['monthly_value'], 2)
                elif isinstance(meta.get('monthly'), (int, float)):
                    monthly[index] = round(meta['monthly'], 2)

                if isinstance(meta.get('category_series'), dict):
                    for cat_name, value in meta['category_series'].items():
                        if cat_name not in category_series:
                            continue

                        if isinstance(value, (int, float)):
                            category_series[cat_name][index] = round(value, 2)
                        elif isinstance(value, list) and len(value) > index:
                            category_series[cat_name][index] = round(value[index], 2)
            except Exception:
                pass

        last_total = record.total

    return {
        "labels": labels,
        "cumulative": cumulative,
        "monthly": monthly,
        "category_series": category_series,
    }


@app.route("/")
def home():
    finances = Finance.query.all()
    
    # Calculate global dashboard metrics
    total_balance = 0
    total_investments_count = 0
    
    today = date.today()
    prev_month = today.month - 1 or 12
    prev_year = today.year if today.month != 1 else today.year - 1
    prev_month_last_day = date(prev_year, prev_month, monthrange(prev_year, prev_month)[1])

    for f in finances:
        category_total = sum(inv.amount for inv in f.investments)
        previous_total = sum(inv.amount for inv in f.investments if inv.investment_date <= prev_month_last_day)
        derived_status = 'growing' if category_total > previous_total else 'hold'
        f.total_amount = category_total
        f.status = getattr(f, 'status', None) or derived_status
        total_balance += category_total
        total_investments_count += len(f.investments)
    
    chart_data = get_monthly_chart_data(total_balance)
    today_date = date.today().isoformat()
        
    return render_template(
        "index.html",
        finances=finances,
        total_balance=total_balance,
        total_investments_count=total_investments_count,
        categories_count=len(finances),
        chart_data=chart_data,
        today_date=today_date,
    )


def load_finance_context(finance_id=None, subcategory_id=None):
    finances = Finance.query.order_by(Finance.name).all()
    selected_finance = None
    selected_subcategory = None

    if finance_id is not None:
        selected_finance = db.session.get(Finance, finance_id)
    elif finances:
        selected_finance = finances[0]

    if selected_finance is None and finances:
        return None, None, None, None, None, finances

    subcategories = []
    investments = []
    total_amount = 0.0

    if selected_finance:
        subcategories = selected_finance.sub_categories
        if subcategory_id is not None:
            selected_subcategory = SubCategory.query.filter_by(
                id=subcategory_id,
                finance_id=selected_finance.id
            ).first()

        if subcategory_id is None:
            investments = []
            total_amount = sum(inv.amount for sub in subcategories for inv in sub.payments)
        elif selected_subcategory:
            investments = sorted(
                selected_subcategory.payments,
                key=lambda inv: inv.investment_date,
                reverse=True,
            )
            total_amount = sum(inv.amount for inv in investments)

    return finances, selected_finance, subcategories, selected_subcategory, investments, total_amount


@app.route("/history")
@app.route("/history/<int:finance_id>")
@app.route("/history/<int:finance_id>/<int:subcategory_id>")
def payment_history(finance_id=None, subcategory_id=None):
    finances, selected_finance, subcategories, selected_subcategory, investments, total_amount = load_finance_context(finance_id, subcategory_id)

    if selected_finance is None and finances:
        return redirect(f"/history/{finances[0].id}")

    return render_template(
        "history.html",
        finances=finances,
        selected_finance=selected_finance,
        subcategories=subcategories,
        selected_subcategory=selected_subcategory,
        investments=investments,
        total_amount=total_amount,
    )


@app.route("/category/<int:finance_id>")
@app.route("/category/<int:finance_id>/subcategory/<int:subcategory_id>")
def category_page(finance_id, subcategory_id=None):
    finances, selected_finance, subcategories, selected_subcategory, investments, total_amount = load_finance_context(finance_id, subcategory_id)
    if selected_finance is None and finances:
        return redirect(f"/category/{finances[0].id}")

    return render_template(
        "category.html",
        finances=finances,
        selected_finance=selected_finance,
        subcategories=subcategories,
        selected_subcategory=selected_subcategory,
        investments=investments,
        total_amount=total_amount,
    )


@app.route("/category")
def category_redirect():
    finances = Finance.query.order_by(Finance.name).all()
    if finances:
        return redirect(f"/category/{finances[0].id}")
    return redirect("/")


@app.route("/api/chart_data")
def api_chart_data():
    """JSON API for chart data (useful for future AJAX refresh)."""
    return jsonify(get_monthly_chart_data())


@app.route("/add_finance", methods=["POST"])
def add_finance():
    name = request.form.get("name", "").strip()
    custom_logo = request.form.get("custom_logo", "").strip()
    logo = custom_logo if custom_logo else request.form.get("logo", "wallet").strip()
    
    if name:
        status = request.form.get('status', 'growing')
        finance = Finance(name=name, logo=logo, status=status)
        db.session.add(finance)
        db.session.commit()

    return redirect("/")


@app.route("/add_subcategory", methods=["POST"])
def add_subcategory():
    finance_id = request.form.get("finance_id")
    name = request.form.get("name", "").strip()
    icon = request.form.get("icon", "").strip()

    if finance_id and name:
        finance = db.session.get(Finance, finance_id)
        if finance:
            subcategory = SubCategory(name=name, finance_id=finance.id, icon=icon or None)
            db.session.add(subcategory)
            db.session.commit()

    return redirect(request.referrer or "/")


@app.route("/edit_finance/<int:id>", methods=["POST"])
def edit_finance(id):
    finance = db.session.get(Finance, id)
    if finance:
        name = request.form.get("name", "").strip()
        custom_logo = request.form.get("custom_logo", "").strip()
        logo = custom_logo if custom_logo else request.form.get("logo", "wallet").strip()
        status = request.form.get("status", "growing").strip()
        
        if name:
            finance.name = name
            finance.logo = logo
            finance.status = status
            db.session.commit()
            
    return redirect("/")


@app.route("/delete_finance/<int:id>")
def delete_finance(id):
    finance = db.session.get(Finance, id)
    if finance:
        db.session.delete(finance)
        db.session.commit()
    return redirect("/")


@app.route("/add_investment", methods=["POST"])
def add_investment():
    subcategory_id = request.form.get("subcategory_id")
    amount_str = request.form.get("amount", "0")
    description = request.form.get("description", "").strip()
    date_str = request.form.get("investment_date", "").strip()
    
    try:
        amount = float(amount_str)
    except ValueError:
        amount = 0.0
        
    if date_str:
        try:
            investment_date = datetime.strptime(date_str, "%Y-%m-%d").date()
        except ValueError:
            investment_date = date.today()
    else:
        investment_date = date.today()

    if subcategory_id:
        subcategory = db.session.get(SubCategory, subcategory_id)
        if subcategory:
            investment = Investment(
                finance_id=subcategory.finance_id,
                subcategory_id=subcategory.id,
                amount=amount,
                description=description,
                investment_date=investment_date
            )
            db.session.add(investment)
            db.session.commit()

    return redirect(request.referrer or "/")


@app.route("/update_investment", methods=["POST"])
def update_investment():
    investment_id = request.form.get("investment_id")
    amount_str = request.form.get("amount", "0")
    description = request.form.get("description", "").strip()
    date_str = request.form.get("investment_date", "").strip()
    
    investment = db.session.get(Investment, investment_id)
    if investment:
        try:
            investment.amount = float(amount_str)
        except ValueError:
            pass
            
        investment.description = description

        subcategory_id = request.form.get("subcategory_id")
        if subcategory_id:
            subcategory = db.session.get(SubCategory, subcategory_id)
            if subcategory:
                investment.subcategory_id = subcategory.id
                investment.finance_id = subcategory.finance_id

        if date_str:
            try:
                investment.investment_date = datetime.strptime(date_str, "%Y-%m-%d").date()
            except ValueError:
                pass

        db.session.commit()

    return redirect(request.referrer or "/")


@app.route("/delete_investment/<int:id>")
def delete_investment(id):
    investment = db.session.get(Investment, id)
    if investment:
        db.session.delete(investment)
        db.session.commit()

    return redirect(request.referrer or "/")


@app.route("/update_portfolio_growth", methods=["POST"])
def update_portfolio_growth():
    """Upsert a PortfolioGrowth record for a given year+month.

    Accepts form fields: year, month, total, and optional meta (JSON string or object).
    """
    data = request.form.to_dict() if request.form else (request.get_json(silent=True) or {})
    year = data.get('year')
    month = data.get('month')
    total = data.get('total')
    meta = data.get('meta')

    try:
        year = int(year)
        month = int(month)
    except Exception:
        return jsonify({'error': 'Invalid year or month'}), 400

    try:
        total_value = float(total) if total is not None else 0.0
    except Exception:
        total_value = 0.0

    record = PortfolioGrowth.query.filter_by(year=year, month=month).first()
    if record:
        record.total = round(total_value, 2)
        if meta:
            # store as JSON string
            record.meta = json.dumps(meta) if not isinstance(meta, str) else meta
    else:
        new = PortfolioGrowth(
            date=f"{year} {['Jan','Feb','Mar','Apr','May','Jun','Jul','Aug','Sep','Oct','Nov','Dec'][month-1]}",
            year=year,
            month=month,
            total=round(total_value, 2),
            meta=(json.dumps(meta) if meta and not isinstance(meta, str) else meta)
        )
        db.session.add(new)

    db.session.commit()
    return jsonify({'status': 'ok'})


if __name__ == "__main__":
    with app.app_context():
        db.create_all()
        ensure_finance_status_column()
        ensure_subcategory_schema()
        ensure_portfolio_meta_column()

    app.run(debug=True)