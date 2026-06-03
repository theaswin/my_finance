from flask import Flask, render_template, request, redirect, jsonify
from datetime import datetime, date
from calendar import monthrange
from sqlalchemy import func, extract
from collections import defaultdict
from models import db, Finance, Investment, PortfolioGrowth

app = Flask(__name__)

app.config["SQLALCHEMY_DATABASE_URI"] = "sqlite:///finance.db"
app.config["SQLALCHEMY_TRACK_MODIFICATIONS"] = False

db.init_app(app)


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
    # Sync database records first
    sync_portfolio_growth()
    
    growth_records = PortfolioGrowth.query.order_by(PortfolioGrowth.year, PortfolioGrowth.month).all()
    
    labels = []
    totals = []
    for record in growth_records:
        labels.append(record.date)
        totals.append(round(record.total, 2))
        
    monthly_changes = []
    today = date.today()
    current_year = today.year
    all_investments = Investment.query.order_by(Investment.investment_date).all()
    
    for index, record in enumerate(growth_records):
        total = record.total
        if index == 0:
            if record.year == current_year and record.month == 1:
                last_year_end = date(current_year - 1, 12, 31)
                prior_year_total = sum(inv.amount for inv in all_investments if inv.investment_date <= last_year_end)
                monthly_changes.append(round(total - prior_year_total, 2))
            else:
                monthly_changes.append(total)
        else:
            monthly_changes.append(round(total - totals[index - 1], 2))
            
    # Calculate category breakdowns for each month in labels
    category_series = {}
    finances = Finance.query.all()
    
    for f in finances:
        series = []
        for record in growth_records:
            last_day = date(record.year, record.month, monthrange(record.year, record.month)[1])
            total_for_cat_and_month = sum(inv.amount for inv in all_investments if inv.finance_id == f.id and inv.investment_date <= last_day)
            series.append(round(total_for_cat_and_month, 2))
        category_series[f.name] = series
        
    return {
        "labels": labels,
        "cumulative": totals,
        "monthly": monthly_changes,
        "category_series": category_series,
    }


@app.route("/")
def home():
    finances = Finance.query.all()
    
    # Calculate global dashboard metrics
    total_balance = 0
    total_investments_count = 0
    
    for f in finances:
        category_total = sum(inv.amount for inv in f.investments)
        # Store temporary sum on the object for easy rendering in jinja template
        f.total_amount = category_total
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


@app.route("/history")
@app.route("/history/<int:finance_id>")
def payment_history(finance_id=None):
    finances = Finance.query.order_by(Finance.name).all()
    selected_finance = None

    if finance_id is not None:
        selected_finance = db.session.get(Finance, finance_id)
    elif finances:
        selected_finance = finances[0]

    if selected_finance is None and finances:
        return redirect(f"/history/{finances[0].id}")

    investments = []
    total_amount = 0.0
    if selected_finance:
        investments = sorted(
            selected_finance.investments,
            key=lambda inv: inv.investment_date,
            reverse=True,
        )
        total_amount = sum(inv.amount for inv in investments)

    return render_template(
        "history.html",
        finances=finances,
        selected_finance=selected_finance,
        investments=investments,
        total_amount=total_amount,
    )


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
        finance = Finance(name=name, logo=logo)
        db.session.add(finance)
        db.session.commit()

    return redirect("/")


@app.route("/edit_finance/<int:id>", methods=["POST"])
def edit_finance(id):
    finance = db.session.get(Finance, id)
    if finance:
        name = request.form.get("name", "").strip()
        custom_logo = request.form.get("custom_logo", "").strip()
        logo = custom_logo if custom_logo else request.form.get("logo", "wallet").strip()
        
        if name:
            finance.name = name
            finance.logo = logo
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
    finance_id = request.form.get("finance_id")
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

    if finance_id:
        investment = Investment(
            finance_id=finance_id,
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


if __name__ == "__main__":
    with app.app_context():
        db.create_all()

    app.run(debug=True)