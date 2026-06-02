from flask import Flask, render_template, request, redirect
from datetime import datetime, date
from models import db, Finance, Investment

app = Flask(__name__)

app.config["SQLALCHEMY_DATABASE_URI"] = "sqlite:///finance.db"
app.config["SQLALCHEMY_TRACK_MODIFICATIONS"] = False

db.init_app(app)





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
        
    return render_template(
        "index.html",
        finances=finances,
        total_balance=total_balance,
        total_investments_count=total_investments_count,
        categories_count=len(finances)
    )


@app.route("/add_finance", methods=["POST"])
def add_finance():
    name = request.form.get("name", "").strip()
    logo = request.form.get("logo", "wallet").strip()
    
    if name:
        finance = Finance(name=name, logo=logo)
        db.session.add(finance)
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