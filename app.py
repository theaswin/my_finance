from flask import Flask, render_template, request, redirect, jsonify, session
from flask_login import LoginManager, login_user, logout_user, login_required, current_user
from datetime import datetime, date
from calendar import monthrange
import json
from sqlalchemy import func, extract, text
from collections import defaultdict
from models import db, Finance, Investment, PortfolioGrowth, SubCategory, User, AppMode, TrackingStock, StockPriceLine

app = Flask(__name__)

app.config["SQLALCHEMY_DATABASE_URI"] = "sqlite:///finance.db"
app.config["SQLALCHEMY_TRACK_MODIFICATIONS"] = False
app.config["SECRET_KEY"] = "your-secret-key-change-this"

db.init_app(app)

# Initialize Flask-Login
login_manager = LoginManager()
login_manager.init_app(app)
login_manager.login_view = "login"


@login_manager.user_loader
def load_user(user_id):
    return db.session.get(User, int(user_id))


@app.route("/login", methods=["GET", "POST"])
def login():
    modes = AppMode.query.order_by(AppMode.name).all()
    if request.method == "POST":
        username = request.form.get("username", "").strip()
        password = request.form.get("password", "").strip()
        active_mode_id = request.form.get("active_mode_id")
        
        user = User.query.filter_by(username=username).first()
        
        if user and user.check_password(password):
            if active_mode_id:
                user.active_mode_id = int(active_mode_id)
                db.session.commit()
            login_user(user)
            if user.active_mode and user.active_mode.name == "Stock":
                return redirect("/stock")
            return redirect("/")
        else:
            return render_template("login.html", error="Invalid username or password", modes=modes)
    
    return render_template("login.html", modes=modes)


@app.route("/logout")
@login_required
def logout():
    logout_user()
    return redirect("/login")


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
@login_required
def home():
    if current_user.active_mode and current_user.active_mode.name == "Stock":
        return redirect("/stock")
        
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
@login_required
def payment_history(finance_id=None, subcategory_id=None):
    if current_user.active_mode and current_user.active_mode.name == "Stock":
        return redirect("/stock")
        
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
@login_required
def category_page(finance_id, subcategory_id=None):
    if current_user.active_mode and current_user.active_mode.name == "Stock":
        return redirect("/stock")
        
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
@login_required
def category_redirect():
    if current_user.active_mode and current_user.active_mode.name == "Stock":
        return redirect("/stock")
        
    finances = Finance.query.order_by(Finance.name).all()
    if finances:
        return redirect(f"/category/{finances[0].id}")
    return redirect("/")


@app.route("/api/chart_data")
@login_required
def api_chart_data():
    """JSON API for chart data (useful for future AJAX refresh)."""
    return jsonify(get_monthly_chart_data())


@app.route("/add_finance", methods=["POST"])
@login_required
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
@login_required
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
@login_required
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
@login_required
def delete_finance(id):
    finance = db.session.get(Finance, id)
    if finance:
        db.session.delete(finance)
        db.session.commit()
    return redirect("/")


@app.route("/add_investment", methods=["POST"])
@login_required
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
@login_required
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
@login_required
def delete_investment(id):
    investment = db.session.get(Investment, id)
    if investment:
        db.session.delete(investment)
        db.session.commit()

    return redirect(request.referrer or "/")


@app.route("/update_portfolio_growth", methods=["POST"])
@login_required
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


@app.context_processor
def inject_modes():
    if current_user.is_authenticated:
        return {
            "all_modes": AppMode.query.order_by(AppMode.name).all(),
            "current_mode": current_user.active_mode
        }
    return {
        "all_modes": [],
        "current_mode": None
    }


@app.route("/switch_mode/<int:mode_id>")
@login_required
def switch_mode(mode_id):
    mode = db.session.get(AppMode, mode_id)
    if mode:
        current_user.active_mode_id = mode.id
        db.session.commit()
    if mode and mode.name == "Stock":
        return redirect("/stock")
    return redirect("/")


@app.route("/stock")
@login_required
def stock_view():
    if current_user.active_mode and current_user.active_mode.name == "Finance":
        return redirect("/")
        
    stocks = TrackingStock.query.order_by(TrackingStock.stock_name).all()
    today_date = date.today().isoformat()
    return render_template("stock.html", stocks=stocks, today_date=today_date)


@app.route("/add_stock", methods=["POST"])
@login_required
def add_stock():
    if current_user.active_mode and current_user.active_mode.name == "Finance":
        return redirect("/")
        
    stock_name = request.form.get("stock_name", "").strip()
    logo = request.form.get("logo", "").strip()
    earnings_str = request.form.get("earnings", "0")
    lost_str = request.form.get("lost", "0")
    
    try:
        earnings = float(earnings_str) if earnings_str else 0.0
    except ValueError:
        earnings = 0.0
        
    try:
        lost = float(lost_str) if lost_str else 0.0
    except ValueError:
        lost = 0.0
        
    if stock_name:
        stock = TrackingStock(
            stock_name=stock_name,
            logo=logo or None,
            earnings=earnings,
            lost=lost
        )
        db.session.add(stock)
        db.session.commit()
        
        # Add initial price line if low/high specified
        low_str = request.form.get("low", "").strip()
        high_str = request.form.get("high", "").strip()
        date_str = request.form.get("date", "").strip()
        
        if low_str or high_str or date_str:
            try:
                low = float(low_str) if low_str else 0.0
            except ValueError:
                low = 0.0
            try:
                high = float(high_str) if high_str else 0.0
            except ValueError:
                high = 0.0
            if date_str:
                try:
                    stock_date = datetime.strptime(date_str, "%Y-%m-%d").date()
                except ValueError:
                    stock_date = date.today()
            else:
                stock_date = date.today()
                
            line = StockPriceLine(
                stock_id=stock.id,
                low=low,
                high=high,
                date=stock_date
            )
            db.session.add(line)
            db.session.commit()
        
    return redirect("/stock")


@app.route("/edit_stock/<int:id>", methods=["POST"])
@login_required
def edit_stock(id):
    if current_user.active_mode and current_user.active_mode.name == "Finance":
        return redirect("/")
        
    stock = db.session.get(TrackingStock, id)
    if stock:
        stock_name = request.form.get("stock_name", "").strip()
        logo = request.form.get("logo", "").strip()
        earnings_str = request.form.get("earnings", "0")
        lost_str = request.form.get("lost", "0")
        
        try:
            earnings = float(earnings_str) if earnings_str else 0.0
        except ValueError:
            earnings = 0.0
            
        try:
            lost = float(lost_str) if lost_str else 0.0
        except ValueError:
            lost = 0.0
            
        if stock_name:
            stock.stock_name = stock_name
            stock.logo = logo or None
            stock.earnings = earnings
            stock.lost = lost
            db.session.commit()
            
    return redirect("/stock")


@app.route("/add_price_line/<int:stock_id>", methods=["POST"])
@login_required
def add_price_line(stock_id):
    if current_user.active_mode and current_user.active_mode.name == "Finance":
        return redirect("/")
        
    stock = db.session.get(TrackingStock, stock_id)
    if stock:
        low_str = request.form.get("low", "0").strip()
        high_str = request.form.get("high", "0").strip()
        date_str = request.form.get("date", "").strip()
        
        try:
            low = float(low_str) if low_str else 0.0
        except ValueError:
            low = 0.0
            
        try:
            high = float(high_str) if high_str else 0.0
        except ValueError:
            high = 0.0
            
        if date_str:
            try:
                stock_date = datetime.strptime(date_str, "%Y-%m-%d").date()
            except ValueError:
                stock_date = date.today()
        else:
            stock_date = date.today()
            
        line = StockPriceLine(
            stock_id=stock.id,
            low=low,
            high=high,
            date=stock_date
        )
        db.session.add(line)
        db.session.commit()
        
    return redirect(f"/stock_detail/{stock_id}")


@app.route("/edit_price_line/<int:id>", methods=["POST"])
@login_required
def edit_price_line(id):
    if current_user.active_mode and current_user.active_mode.name == "Finance":
        return redirect("/")
        
    line = db.session.get(StockPriceLine, id)
    if line:
        low_str = request.form.get("low", "0").strip()
        high_str = request.form.get("high", "0").strip()
        date_str = request.form.get("date", "").strip()
        
        try:
            low = float(low_str) if low_str else 0.0
        except ValueError:
            low = 0.0
            
        try:
            high = float(high_str) if high_str else 0.0
        except ValueError:
            high = 0.0
            
        if date_str:
            try:
                stock_date = datetime.strptime(date_str, "%Y-%m-%d").date()
            except ValueError:
                stock_date = date.today()
        else:
            stock_date = date.today()
            
        line.low = low
        line.high = high
        line.date = stock_date
        db.session.commit()
        
    return redirect("/stock")


@app.route("/delete_price_line/<int:id>")
@login_required
def delete_price_line(id):
    if current_user.active_mode and current_user.active_mode.name == "Finance":
        return redirect("/")
        
    line = db.session.get(StockPriceLine, id)
    stock_id = line.stock_id if line else None
    if line:
        db.session.delete(line)
        db.session.commit()
        
    return redirect(f"/stock_detail/{stock_id}") if stock_id else redirect("/stock")


@app.route("/delete_stock/<int:id>")
@login_required
def delete_stock(id):
    if current_user.active_mode and current_user.active_mode.name == "Finance":
        return redirect("/")
        
    stock = db.session.get(TrackingStock, id)
    if stock:
        db.session.delete(stock)
        db.session.commit()
        
    return redirect("/stock")


@app.route("/stock_detail/<int:stock_id>")
@login_required
def stock_detail(stock_id):
    if current_user.active_mode and current_user.active_mode.name == "Finance":
        return redirect("/")
        
    stock = db.session.get(TrackingStock, stock_id)
    if not stock:
        return redirect("/stock")
    
    today_date = date.today().isoformat()
    return render_template("stock_detail.html", stock=stock, today_date=today_date)


def ensure_user_active_mode_column():
    """Add user.active_mode_id column if it does not exist in SQLite."""
    engine = db.engine
    if engine.dialect.name == 'sqlite':
        with engine.connect() as conn:
            result = conn.execute(text("PRAGMA table_info(user)"))
            existing_columns = {row[1] for row in result}
            if 'active_mode_id' not in existing_columns:
                conn.execute(text("ALTER TABLE user ADD COLUMN active_mode_id INTEGER REFERENCES app_mode(id)"))
                conn.commit()


def populate_app_modes():
    """Populate default app modes if they don't exist."""
    finance_mode = AppMode.query.filter_by(name="Finance").first()
    if not finance_mode:
        finance_mode = AppMode(name="Finance")
        db.session.add(finance_mode)
    
    stock_mode = AppMode.query.filter_by(name="Stock").first()
    if not stock_mode:
        stock_mode = AppMode(name="Stock")
        db.session.add(stock_mode)
        
    db.session.commit()
    
    # Also assign a default active mode to existing users
    users = User.query.filter_by(active_mode_id=None).all()
    for u in users:
        u.active_mode_id = finance_mode.id
    db.session.commit()


def ensure_stock_schema():
    """Ensure that stock_price_lines table and earnings/lost columns exist."""
    engine = db.engine
    if engine.dialect.name == 'sqlite':
        with engine.connect() as conn:
            conn.execute(text("""
                CREATE TABLE IF NOT EXISTS stock_price_lines (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    stock_id INTEGER NOT NULL REFERENCES tracking_stocks(id) ON DELETE CASCADE,
                    low REAL,
                    high REAL,
                    date DATE
                )
            """))
            conn.commit()

            result = conn.execute(text("PRAGMA table_info(tracking_stocks)"))
            existing_columns = {row[1] for row in result}
            if 'earnings' not in existing_columns:
                conn.execute(text("ALTER TABLE tracking_stocks ADD COLUMN earnings REAL DEFAULT 0.0"))
                conn.commit()
            if 'lost' not in existing_columns:
                conn.execute(text("ALTER TABLE tracking_stocks ADD COLUMN lost REAL DEFAULT 0.0"))
                conn.commit()


if __name__ == "__main__":
    with app.app_context():
        db.create_all()
        ensure_user_active_mode_column()
        populate_app_modes()
        ensure_stock_schema()
        ensure_finance_status_column()
        ensure_subcategory_schema()
        ensure_portfolio_meta_column()
        
        # Create or update default user
        default_user = User.query.filter_by(username="aswin").first()
        if not default_user:
            default_user = User(username="aswin")
            default_user.set_password("perfin")
            db.session.add(default_user)
            db.session.commit()
            print("Default user 'aswin' created successfully!")
        else:
            # Update password if user exists
            default_user.set_password("perfin")
            db.session.commit()
            print("Default user 'aswin' password updated successfully!")

    app.run(host='0.0.0.0', debug=True)