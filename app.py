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


def get_monthly_chart_data(current_net_worth=None):
    """Load monthly portfolio growth from portfolio_growth and append current month value when needed."""
    if current_net_worth is None:
        current_net_worth = sum(inv.amount for inv in Investment.query.all())

    growth_records = PortfolioGrowth.query.order_by(PortfolioGrowth.year, PortfolioGrowth.month).all()
    labels = []
    totals = []
    for record in growth_records:
        labels.append(record.date)
        totals.append(round(record.total, 2))

    today = date.today()
    current_label = today.strftime("%Y %b")
    month_last_day = monthrange(today.year, today.month)[1]
    current_month_complete = today.day == month_last_day
    current_total = round(current_net_worth, 2)

    if not growth_records:
        if current_net_worth > 0:
            labels.append(current_label)
            totals.append(current_total)
    else:
        if not current_month_complete:
            if labels and labels[-1] == current_label:
                totals[-1] = current_total
            else:
                labels.append(current_label)
                totals.append(current_total)
        else:
            if labels and labels[-1] != current_label:
                labels.append(current_label)
                totals.append(current_total)

    monthly_changes = []
    for index, total in enumerate(totals):
        if index == 0:
            monthly_changes.append(total)
        else:
            monthly_changes.append(round(total - totals[index - 1], 2))

    investments = Investment.query.order_by(Investment.investment_date).all()
    category_data = {}
    for inv in investments:
        cat_name = inv.finance.name
        if cat_name not in category_data:
            category_data[cat_name] = defaultdict(float)
        key = inv.investment_date.strftime("%Y-%m")
        category_data[cat_name][key] += inv.amount

    category_series = {}
    if growth_records:
        # Use the ordered growth labels when mapping category series for display.
        month_keys = [datetime.strptime(label, "%Y %b").strftime("%Y-%m") for label in labels]
    else:
        month_keys = []

    for cat_name, monthly in category_data.items():
        series = []
        running = 0
        for month_key in month_keys:
            running += monthly.get(month_key, 0)
            series.append(round(running, 2))
        category_series[cat_name] = series

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

    return redirect("/")


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

    return redirect("/")


@app.route("/delete_investment/<int:id>")
def delete_investment(id):
    investment = db.session.get(Investment, id)
    if investment:
        db.session.delete(investment)
        db.session.commit()

    return redirect("/")


if __name__ == "__main__":
    with app.app_context():
        db.create_all()

    app.run(debug=True)