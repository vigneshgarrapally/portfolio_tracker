import csv
import io
import json
import hashlib
import requests
from flask import render_template, request, redirect, url_for, flash
from app import app, db
from app.models import (
    User,
    GoldTransaction,
    GoldSellTransaction,
    GoldPrice,
    Property,
    PropertyValuation,
    PropertyExpense,
    MutualFundScheme,
    MutualFundFolio,
    MutualFundTransaction,
    MutualFundNAV,
)
from datetime import datetime, date
from decimal import Decimal, InvalidOperation
from markupsafe import Markup
from sqlalchemy.exc import IntegrityError
from sqlalchemy import func, cast, TEXT
from casparser import read_cas_pdf
from app.utils import calculate_xirr


from selenium import webdriver
from selenium.webdriver.common.by import By
from selenium.webdriver.support.ui import WebDriverWait
from selenium.webdriver.support import expected_conditions as EC
from selenium.common.exceptions import TimeoutException


def format_inr(value):
    """Formats a number into Indian Lakh/Crore system."""
    try:
        val = float(value)
        if val >= 10000000:  # Crores
            return f"₹{val / 10000000:.2f} Cr"
        elif val >= 100000:  # Lakhs
            return f"₹{val / 100000:.2f} L"
        else:
            return f"₹{val:,.2f}"  # Use comma separators for thousands
    except (ValueError, TypeError):
        return "₹0.00"


app.jinja_env.filters["inr"] = format_inr
# === Main Routes ===


@app.route("/")
def index():
    """Renders the main welcome page."""
    return render_template("index.html")


# === User CRUD Routes ===


@app.route("/users")
def users():
    """Display all users."""
    all_users = User.query.order_by(User.name).all()
    return render_template("users.html", users=all_users)


@app.route("/users/add", methods=["GET", "POST"])
def add_user():
    """Add a new user."""
    if request.method == "POST":
        name = request.form.get("name")
        email = request.form.get("email")
        phone = request.form.get("phone_number")

        if not name or not email:
            flash("Name and Email are required.", "danger")
            return redirect(url_for("add_user"))

        # Check for existing user
        if User.query.filter_by(email=email).first():
            flash("Email already exists.", "danger")
            return redirect(url_for("add_user"))

        new_user = User(name=name, email=email, phone_number=phone)
        db.session.add(new_user)
        db.session.commit()

        flash(f'User "{name}" added successfully!', "success")
        return redirect(url_for("users"))

    return render_template("add_user.html")


@app.route("/users/edit/<int:user_id>", methods=["GET", "POST"])
def edit_user(user_id):
    """Edit an existing user."""
    user = User.query.get_or_404(user_id)

    if request.method == "POST":
        name = request.form.get("name")
        email = request.form.get("email")
        phone = request.form.get("phone_number")

        if not name or not email:
            flash("Name and Email are required.", "danger")
            return render_template("edit_user.html", user=user)

        # Check if email is being changed to one that already exists
        if email != user.email and User.query.filter_by(email=email).first():
            flash("That email is already in use.", "danger")
            return render_template("edit_user.html", user=user)

        user.name = name
        user.email = email
        user.phone_number = phone
        user.updated_at = datetime.utcnow()

        db.session.commit()
        flash(f'User "{name}" updated successfully!', "success")
        return redirect(url_for("users"))

    return render_template("edit_user.html", user=user)


@app.route("/users/delete/<int:user_id>", methods=["POST"])
def delete_user(user_id):
    """Delete a user."""
    user = User.query.get_or_404(user_id)

    try:
        db.session.delete(user)
        db.session.commit()
        flash(
            f'User "{user.name}" and all associated data deleted.', "success"
        )
    except Exception as e:
        db.session.rollback()
        flash(f"Error deleting user: {str(e)}", "danger")

    return redirect(url_for("users"))


# === Gold CRUD Routes ===


@app.route("/gold")
def gold():
    """Display gold dashboard with all holdings."""
    user_id = request.args.get("user_id", type=int)
    all_users = User.query.order_by(User.name).all()

    # --- Base Queries ---
    buy_query = GoldTransaction.query
    sell_query = GoldSellTransaction.query

    if user_id:
        buy_query = buy_query.filter_by(user_id=user_id)
        sell_query = sell_query.filter_by(user_id=user_id)

    # --- Get all transactions for cash flow calculation ---
    buy_transactions = buy_query.order_by(
        GoldTransaction.invoice_date.desc()
    ).all()
    sell_transactions = sell_query.all()

    # --- Fix for SAWarning: Use .c attribute from subquery ---
    buy_subquery = buy_query.subquery()
    sell_subquery = sell_query.subquery()

    total_buy_grams = db.session.query(
        db.func.sum(buy_subquery.c.grams)
    ).scalar() or Decimal("0.0")

    total_investment = db.session.query(
        db.func.sum(buy_subquery.c.grams * buy_subquery.c.per_gm_price)
    ).scalar() or Decimal("0.0")

    total_sell_grams = db.session.query(
        db.func.sum(sell_subquery.c.grams)
    ).scalar() or Decimal("0.0")

    total_sell_value = db.session.query(
        db.func.sum(
            sell_subquery.c.grams * sell_subquery.c.sell_price_per_gram
        )
    ).scalar() or Decimal("0.0")

    # --- New Calculations ---
    current_holdings = total_buy_grams - total_sell_grams
    average_price = (
        (total_investment / total_buy_grams)
        if total_buy_grams > 0
        else Decimal("0.0")
    )

    # Get latest price
    latest_price_entry = GoldPrice.query.order_by(
        GoldPrice.date.desc()
    ).first()

    current_value = Decimal("0.0")
    absolute_profit = Decimal("0.0")
    absolute_return = Decimal("0.0")
    xirr = 0.0
    latest_price_date = None

    if latest_price_entry:
        latest_price = latest_price_entry.price_per_gram_24k
        latest_price_date = latest_price_entry.date
        current_value = current_holdings * latest_price

    # Profit = (Current Value + What you sold for) - What you invested
    absolute_profit = (current_value + total_sell_value) - total_investment

    if total_investment > 0:
        absolute_return = (absolute_profit / total_investment) * 100

    # --- XIRR Cash Flow ---
    cash_flows = []
    for buy in buy_transactions:
        # Investment is a negative cash flow
        cash_flows.append((buy.invoice_date, -(buy.grams * buy.per_gm_price)))
    for sell in sell_transactions:
        # Sale is a positive cash flow
        cash_flows.append(
            (sell.sell_date, (sell.grams * sell.sell_price_per_gram))
        )

    # Add current holdings as a final "sale" on today's date
    if current_holdings > 0 and latest_price_entry:
        cash_flows.append((date.today(), current_value))

    xirr = calculate_xirr(cash_flows)

    # --- Update Summary ---
    summary = {
        "current_holdings": current_holdings,
        "total_investment": total_investment,
        "average_price": average_price,
        "current_value": current_value,
        "latest_price_date": latest_price_date,
        "absolute_profit": absolute_profit,
        "absolute_return": absolute_return,
        "xirr": xirr,
    }

    return render_template(
        "gold.html",
        buy_transactions=buy_transactions,
        summary=summary,
        all_users=all_users,
        selected_user_id=user_id,
    )


@app.route("/gold/view/<int:tx_id>")
def view_gold(tx_id):
    """
    Shows read-only details for a holding and list of its linked sales.
    This is where new sales are added from.
    """
    tx = GoldTransaction.query.get_or_404(tx_id)
    linked_sells = tx.linked_sells.order_by(
        GoldSellTransaction.sell_date.desc()
    ).all()

    return render_template("view_gold.html", tx=tx, linked_sells=linked_sells)


@app.route("/gold/add", methods=["GET", "POST"])
def add_gold():
    """Add a new gold BUY transaction (holding)."""
    if request.method == "POST":
        try:
            new_tx = GoldTransaction(
                user_id=request.form.get("user_id"),
                invoice_date=datetime.strptime(
                    request.form.get("invoice_date"), "%Y-%m-%d"
                ).date(),
                grams=Decimal(request.form.get("grams")),
                per_gm_price=Decimal(request.form.get("per_gm_price")),
                purity=request.form.get("purity"),
                platform=request.form.get("platform"),
                type=request.form.get("type"),
                brand=request.form.get("brand"),
                notes=request.form.get("notes"),
            )
            db.session.add(new_tx)
            db.session.commit()
            flash("Gold holding added successfully!", "success")
            return redirect(url_for("gold"))
        except Exception as e:
            db.session.rollback()
            flash(f"Error adding holding: {str(e)}", "danger")
            return redirect(url_for("add_gold"))

    users = User.query.all()
    if not users:
        flash("You must add a User before you can add assets.", "warning")
        return redirect(url_for("add_user"))

    existing_platforms = [
        p[0]
        for p in db.session.query(GoldTransaction.platform).distinct()
        if p[0]
    ]
    existing_brands = [
        b[0]
        for b in db.session.query(GoldTransaction.brand).distinct()
        if b[0]
    ]

    return render_template(
        "add_gold.html",
        users=users,
        existing_platforms=existing_platforms,
        existing_brands=existing_brands,
    )


@app.route("/gold/edit/<int:tx_id>", methods=["GET", "POST"])
def edit_gold(tx_id):
    """Edit an existing gold BUY transaction (holding)."""
    tx = GoldTransaction.query.get_or_404(tx_id)

    if request.method == "POST":
        try:
            tx.user_id = request.form.get("user_id")
            tx.invoice_date = datetime.strptime(
                request.form.get("invoice_date"), "%Y-%m-%d"
            ).date()
            tx.grams = Decimal(request.form.get("grams"))
            tx.per_gm_price = Decimal(request.form.get("per_gm_price"))
            tx.purity = request.form.get("purity")
            tx.platform = request.form.get("platform")
            tx.type = request.form.get("type")
            tx.brand = request.form.get("brand")
            tx.notes = request.form.get("notes")
            tx.updated_at = datetime.utcnow()

            db.session.commit()
            flash("Holding updated successfully!", "success")
            return redirect(url_for("view_gold", tx_id=tx.id))
        except Exception as e:
            db.session.rollback()
            flash(f"Error updating holding: {str(e)}", "danger")
            # Re-fetch data on error
            users = User.query.all()
            existing_platforms = [
                p[0]
                for p in db.session.query(GoldTransaction.platform).distinct()
                if p[0]
            ]
            existing_brands = [
                b[0]
                for b in db.session.query(GoldTransaction.brand).distinct()
                if b[0]
            ]
            return render_template(
                "edit_gold.html",
                tx=tx,
                users=users,
                existing_platforms=existing_platforms,
                existing_brands=existing_brands,
            )

    users = User.query.all()
    existing_platforms = [
        p[0]
        for p in db.session.query(GoldTransaction.platform).distinct()
        if p[0]
    ]
    existing_brands = [
        b[0]
        for b in db.session.query(GoldTransaction.brand).distinct()
        if b[0]
    ]

    # --- No longer fetches linked_sells ---
    return render_template(
        "edit_gold.html",
        tx=tx,
        users=users,
        existing_platforms=existing_platforms,
        existing_brands=existing_brands,
    )


@app.route("/gold/delete/<int:tx_id>", methods=["POST"])
def delete_gold(tx_id):
    """Delete a gold BUY transaction."""
    tx = GoldTransaction.query.get_or_404(tx_id)
    if tx.linked_sells.count() > 0:
        flash(
            "Cannot delete a holding that has linked sell transactions. Please delete the sell transactions first.",
            "danger",
        )
        return redirect(url_for("gold"))
    try:
        db.session.delete(tx)
        db.session.commit()
        flash("Holding deleted successfully.", "success")
    except Exception as e:
        db.session.rollback()
        flash(f"Error deleting holding: {str(e)}", "danger")
    return redirect(url_for("gold"))


@app.route("/gold/sell/<int:buy_id>", methods=["GET", "POST"])
def add_gold_sell(buy_id):
    """Add a new gold SELL transaction LINKED to a holding."""
    buy_tx = GoldTransaction.query.get_or_404(buy_id)
    max_grams = buy_tx.remaining_grams

    if request.method == "POST":
        grams_to_sell = Decimal(request.form.get("grams"))

        if grams_to_sell > max_grams or grams_to_sell <= 0:
            flash(
                f"Grams must be positive and no more than {max_grams} gm.",
                "danger",
            )
            return redirect(url_for("add_gold_sell", buy_id=buy_id))

        try:
            new_tx = GoldSellTransaction(
                user_id=buy_tx.user_id,
                sell_date=datetime.strptime(
                    request.form.get("sell_date"), "%Y-%m-%d"
                ).date(),
                grams=grams_to_sell,
                sell_price_per_gram=Decimal(
                    request.form.get("sell_price_per_gram")
                ),
                platform=request.form.get("platform"),
                notes=request.form.get("notes"),
                linked_buy_id=buy_id,
            )
            db.session.add(new_tx)
            db.session.commit()
            flash("Gold (Sell) transaction added successfully!", "success")
            # --- Redirect back to the VIEW page ---
            return redirect(url_for("view_gold", tx_id=buy_id))
        except Exception as e:
            db.session.rollback()
            flash(f"Error adding sell transaction: {str(e)}", "danger")
            return redirect(url_for("add_gold_sell", buy_id=buy_id))

    return render_template(
        "add_gold_sell.html", buy_tx=buy_tx, max_grams=max_grams
    )


@app.route("/gold/sell/edit/<int:tx_id>", methods=["GET", "POST"])
def edit_gold_sell(tx_id):
    """Edit an existing gold SELL transaction."""
    tx = GoldSellTransaction.query.get_or_404(tx_id)
    max_grams = tx.linked_buy.remaining_grams + tx.grams

    if request.method == "POST":
        grams_to_sell = Decimal(request.form.get("grams"))

        if grams_to_sell > max_grams or grams_to_sell <= 0:
            flash(
                f"Grams must be positive and no more than {max_grams} gm.",
                "danger",
            )
            return redirect(url_for("edit_gold_sell", tx_id=tx.id))

        try:
            tx.sell_date = datetime.strptime(
                request.form.get("sell_date"), "%Y-%m-%d"
            ).date()
            tx.grams = grams_to_sell
            tx.sell_price_per_gram = Decimal(
                request.form.get("sell_price_per_gram")
            )
            tx.platform = request.form.get("platform")
            tx.notes = request.form.get("notes")

            db.session.commit()
            flash("Sell transaction updated successfully!", "success")
            # --- Redirect back to the VIEW page ---
            return redirect(url_for("view_gold", tx_id=tx.linked_buy_id))
        except Exception as e:
            db.session.rollback()
            flash(f"Error updating sell transaction: {str(e)}", "danger")
            return render_template(
                "edit_gold_sell.html", tx=tx, max_grams=max_grams
            )

    return render_template("edit_gold_sell.html", tx=tx, max_grams=max_grams)


@app.route("/gold/sell/delete/<int:tx_id>", methods=["POST"])
def delete_gold_sell(tx_id):
    """Delete a gold SELL transaction."""
    tx = GoldSellTransaction.query.get_or_404(tx_id)
    buy_id = tx.linked_buy_id
    try:
        db.session.delete(tx)
        db.session.commit()
        flash("Sell transaction deleted successfully.", "success")
    except Exception as e:
        db.session.rollback()
        flash(f"Error deleting sell transaction: {str(e)}", "danger")

    # --- Redirect back to the VIEW page ---
    return redirect(url_for("view_gold", tx_id=buy_id))


@app.route("/gold/upload_csv", methods=["POST"])
def upload_gold_csv():
    """Add gold transactions from a CSV file upload with validation (All or Nothing)."""
    if "file" not in request.files:
        flash("No file part", "danger")
        return redirect(url_for("add_gold"))

    file = request.files["file"]
    if file.filename == "":
        flash("No selected file", "danger")
        return redirect(url_for("add_gold"))

    if not file.filename.endswith(".csv"):
        flash("Invalid file type. Please upload a .csv file.", "danger")
        return redirect(url_for("add_gold"))

    valid_transactions = []
    errors = []

    # Pre-fetch valid IDs and values for efficient checking
    all_user_ids = {user.id for user in User.query.all()}
    allowed_types = {"Coin", "Jewellery", "SGB", "Digital", "Other"}
    allowed_purity = {
        "24K",
        "22K",
        "18K",
        None,
        "",
        " ",
    }  # Allow null/empty string

    try:
        # Read the file stream as text, handle potential BOM (Byte Order Mark)
        file_stream = io.StringIO(file.stream.read().decode("utf-8-sig"))
        csv_reader = csv.DictReader(file_stream)

        # Read all data into a list to check if it's empty
        data = list(csv_reader)
        if not data:
            flash("CSV file is empty or has no data rows.", "info")
            return redirect(url_for("add_gold"))

        for i, item in enumerate(data):
            row_num = i + 1
            item_errors = []

            if not isinstance(item, dict):
                errors.append(f"Row {row_num}: Invalid CSV data format.")
                continue

            # --- Validate Fields ---
            user_id = item.get("user_id")
            user_id_obj = None
            if not user_id:
                item_errors.append("`user_id` is missing.")
            else:
                try:
                    user_id_obj = int(user_id)
                    if user_id_obj not in all_user_ids:
                        item_errors.append(
                            f"User with id {user_id_obj} does not exist."
                        )
                except (ValueError, TypeError):
                    item_errors.append(
                        f"`user_id` '{user_id}' must be an integer."
                    )

            date_str = item.get("invoice_date")
            date_obj = None
            if not date_str:
                item_errors.append("`invoice_date` is missing.")
            else:
                try:
                    date_obj = datetime.strptime(date_str, "%Y-%m-%d").date()
                except ValueError:
                    item_errors.append(
                        "`invoice_date` must be in YYYY-MM-DD format."
                    )

            grams_val = item.get("grams")
            grams_obj = None
            if not grams_val:
                item_errors.append("`grams` is missing.")
            else:
                try:
                    grams_obj = Decimal(str(grams_val))
                    if grams_obj <= 0:
                        item_errors.append("`grams` must be greater than 0.")
                except Exception:
                    item_errors.append(
                        f"`grams` value '{grams_val}' must be a valid number."
                    )

            price_val = item.get("per_gm_price")
            price_obj = None
            if not price_val:
                item_errors.append("`per_gm_price` is missing.")
            else:
                try:
                    price_obj = Decimal(str(price_val))
                    if price_obj < 0:
                        item_errors.append(
                            "`per_gm_price` cannot be negative."
                        )
                except Exception:
                    item_errors.append(
                        f"`per_gm_price` value '{price_val}' must be a valid number."
                    )

            type_val = item.get("type")
            if not type_val:
                item_errors.append("`type` is missing.")
            elif type_val not in allowed_types:
                item_errors.append(
                    f"`type` '{type_val}' is invalid. Must be one of: {allowed_types}."
                )

            purity_val = item.get("purity")
            if purity_val not in allowed_purity:
                item_errors.append(
                    f"`purity` '{purity_val}' is invalid. Must be one of: {allowed_purity}."
                )

            # --- Collate errors or create object ---
            if item_errors:
                errors.append(
                    f"Row {row_num} ({date_str or 'No Date'}): {'; '.join(item_errors)}"
                )
            else:
                valid_transactions.append(
                    GoldTransaction(
                        user_id=user_id_obj,
                        invoice_date=date_obj,
                        grams=grams_obj,
                        per_gm_price=price_obj,
                        purity=purity_val
                        or None,  # Ensure empty strings become None
                        platform=item.get("platform") or None,
                        type=type_val,
                        brand=item.get("brand") or None,
                        notes=item.get("notes") or None,
                    )
                )

        # --- ATOMIC COMMIT: Only commit if there are NO errors ---
        if errors:
            db.session.rollback()  # Ensure nothing is committed
            flash(
                f"Upload failed. {len(errors)} transactions had errors. No records were added.",
                "danger",
            )
            error_html = (
                "<ul>"
                + "".join([f"<li>{e}</li>" for e in errors[:10]])
                + "</ul>"
            )
            if len(errors) > 10:
                error_html += f"<p>...and {len(errors) - 10} more errors.</p>"
            flash(Markup(error_html), "danger")

        elif not valid_transactions:
            flash(
                "CSV file was empty or contained no valid transactions.",
                "info",
            )

        else:
            # --- All rows are valid, now we commit ---
            try:
                db.session.add_all(valid_transactions)
                db.session.commit()
                flash(
                    f"Successfully uploaded and added {len(valid_transactions)} transactions.",
                    "success",
                )
            except Exception as e:
                db.session.rollback()
                flash(
                    f"An error occurred during the final commit: {str(e)}",
                    "danger",
                )

    except csv.Error as e:
        flash(
            f"Error parsing CSV file. Please check file format. Error: {e}",
            "danger",
        )
    except UnicodeDecodeError:
        flash(
            "Error: Could not decode file. Please ensure it is saved as UTF-8.",
            "danger",
        )
    except Exception as e:
        db.session.rollback()
        flash(f"An unexpected error occurred: {str(e)}", "danger")

    return redirect(url_for("add_gold"))


@app.route("/gold/prices")
def gold_prices():
    """
    Display the page for manually adding/updating prices
    and triggering the auto-fetch.
    """
    prices = GoldPrice.query.order_by(GoldPrice.date.desc()).all()
    today_str = date.today().strftime("%Y-%m-%d")
    return render_template(
        "gold_prices.html", prices=prices, today_str=today_str
    )


@app.route("/gold/prices/add", methods=["POST"])
def add_gold_price():
    """
    Handle the manual form submission for adding or updating a price.
    """
    try:
        date_str = request.form.get("date")
        price_str = request.form.get("price_per_gram_24k")

        date_obj = datetime.strptime(date_str, "%Y-%m-%d").date()
        price_24k = Decimal(price_str)

        if price_24k < 0:
            flash("Price cannot be negative.", "danger")
            return redirect(url_for("gold_prices"))

        existing_price = GoldPrice.query.filter_by(date=date_obj).first()

        if existing_price:
            if existing_price.price_per_gram_24k != price_24k:
                existing_price.price_per_gram_24k = price_24k
                existing_price.source = "Manual"
                db.session.add(existing_price)
                flash(
                    f"Price for {date_str} updated successfully.", "success"
                )
            else:
                flash(
                    f"Price for {date_str} is already set to this value.",
                    "info",
                )
        else:
            new_price = GoldPrice(
                date=date_obj, price_per_gram_24k=price_24k, source="Manual"
            )
            db.session.add(new_price)
            flash(f"Price for {date_str} added successfully.", "success")

        db.session.commit()
    except Exception as e:
        db.session.rollback()
        flash(f"Error adding price: {str(e)}", "danger")

    return redirect(url_for("gold_prices"))


@app.route("/gold/prices/fetch", methods=["POST"])
def fetch_gold_prices():
    """
    Run the Selenium scraper to fetch prices from Tanishq.
    """
    url = "https://www.tanishq.co.in/gold-rate.html"
    options = webdriver.ChromeOptions()
    options.add_argument("--headless=new")
    options.add_argument("--no-sandbox")
    options.add_argument("--disable-dev-shm-usage")
    options.add_argument("--window-size=1920,1080")
    options.add_argument(
        "user-agent=Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/100.0.4896.127 Safari/537.36"
    )

    try:
        driver = webdriver.Chrome(options=options)
        driver.set_page_load_timeout(30)  # 30 second timeout for page load
    except Exception as e:
        flash(
            f"Error initializing WebDriver: {e}. Is chromedriver installed and in your PATH?",
            "danger",
        )
        return redirect(url_for("gold_prices"))

    try:
        driver.get(url)
        wait = WebDriverWait(driver, 30)  # 30 second timeout for element

        TABLE_XPATH = (
            "//table[@class='table goldrate-table goldrate-history-table']"
        )
        table_element = wait.until(
            EC.presence_of_element_located((By.XPATH, TABLE_XPATH))
        )

        rows = table_element.find_elements(By.XPATH, "./tbody/tr")
        if not rows:
            flash(
                "Scrape successful, but no data rows were found in the table.",
                "warning",
            )
            return redirect(url_for("gold_prices"))

        added_count = 0
        updated_count = 0
        ignored_count = 0
        failed_count = 0

        for row in rows:
            try:
                cells = row.find_elements(By.TAG_NAME, "td")

                # --- MODIFIED: Check for 2 cells based on your output ---
                if not cells or len(cells) < 2:
                    failed_count += 1
                    continue

                # --- MODIFIED: Get date and price from cells 0 and 1 ---
                date_str = cells[0].text
                price_22k_10g_str = cells[1].text  # This is for 10g of 22K

                # --- MODIFIED: New date format %d-%m-%Y ---
                date_obj = datetime.strptime(date_str, "%d-%m-%Y").date()

                price_22k_1g = Decimal(
                    price_22k_10g_str.replace("₹", "")
                    .replace(",", "")
                    .strip()
                )

                # --- MODIFIED: Convert 22K 1g price to 24K 1g price ---
                price_24k_1g = (price_22k_1g / Decimal("22")) * Decimal("24")

                # Upsert logic
                existing_price = GoldPrice.query.filter_by(
                    date=date_obj
                ).first()

                if existing_price:
                    # Use a small tolerance for comparison
                    if not abs(
                        existing_price.price_per_gram_24k - price_24k_1g
                    ) < Decimal("0.01"):
                        existing_price.price_per_gram_24k = price_24k_1g
                        existing_price.source = "Tanishq (auto-fetch)"
                        db.session.add(existing_price)
                        updated_count += 1
                    else:
                        ignored_count += 1
                else:
                    new_price = GoldPrice(
                        date=date_obj,
                        price_per_gram_24k=price_24k_1g,
                        source="Tanishq (auto-fetch)",
                    )
                    db.session.add(new_price)
                    added_count += 1

            except Exception as e:
                # Catch errors on a per-row basis
                print(f"Failed to process row for date {cells[0].text}: {e}")
                failed_count += 1

        db.session.commit()
        flash(
            f"Fetch complete. Added: {added_count}, Updated: {updated_count}, Ignored: {ignored_count}, Failed rows: {failed_count}",
            "success",
        )

    except TimeoutException:
        flash(
            "Failed to fetch prices: The page (or a required element) took too long to load (Timeout > 30s).",
            "danger",
        )
    except Exception as e:
        flash(f"An error occurred during data extraction: {e}", "danger")
    finally:
        driver.quit()

    return redirect(url_for("gold_prices"))


# --- NEW: Delete Price Route ---
@app.route("/gold/prices/delete/<int:price_id>", methods=["POST"])
def delete_gold_price(price_id):
    """
    Delete a manually entered or fetched price.
    """
    price = GoldPrice.query.get_or_404(price_id)
    try:
        db.session.delete(price)
        db.session.commit()
        flash(
            f'Price for {price.date.strftime("%Y-%m-%d")} deleted.', "success"
        )
    except Exception as e:
        db.session.rollback()
        flash(f"Error deleting price: {str(e)}", "danger")

    return redirect(url_for("gold_prices"))


@app.route("/real_estate")
def real_estate():
    """Display the real estate dashboard."""
    user_id = request.args.get("user_id", type=int)
    all_users = User.query.order_by(User.name).all()

    prop_query = Property.query
    if user_id:
        prop_query = prop_query.filter_by(user_id=user_id)

    # Separate properties into unsold and sold
    unsold_properties = (
        prop_query.filter(Property.sell_date == None)
        .order_by(Property.purchase_date.desc())
        .all()
    )
    sold_properties = (
        prop_query.filter(Property.sell_date != None)
        .order_by(Property.sell_date.desc())
        .all()
    )

    # --- Summary Calculations ---
    total_investment = Decimal("0.0")
    total_current_value = Decimal("0.0")
    total_realized_pnl = Decimal("0.0")

    for prop in unsold_properties:
        total_investment += prop.total_cost_basis
        latest_val = prop.latest_valuation
        if latest_val:
            total_current_value += latest_val.estimated_value

    for prop in sold_properties:
        # total_investment += prop.total_cost_basis
        net_sale = (prop.sell_value or 0) - (prop.selling_costs or 0)
        total_realized_pnl += net_sale - prop.total_cost_basis

    summary = {
        "total_investment": total_investment,
        "total_current_value": total_current_value,
        "unrealized_pnl": (
            total_current_value - total_investment
            if total_investment > 0
            else Decimal("0.0")
        ),
        "realized_pnl": total_realized_pnl,
    }

    return render_template(
        "real_estate.html",
        unsold_properties=unsold_properties,
        sold_properties=sold_properties,
        summary=summary,
        all_users=all_users,
        selected_user_id=user_id,
    )


@app.route("/real_estate/add", methods=["GET", "POST"])
def add_property():
    """Add a new real estate property."""
    if request.method == "POST":
        try:
            new_prop = Property(
                user_id=request.form.get("user_id"),
                name=request.form.get("name"),
                property_type=request.form.get("property_type"),
                address=request.form.get("address"),
                city=request.form.get("city"),
                area=Decimal(request.form.get("area")),
                area_unit=request.form.get("area_unit"),
                purchase_date=datetime.strptime(
                    request.form.get("purchase_date"), "%Y-%m-%d"
                ).date(),
                purchase_value=Decimal(request.form.get("purchase_value")),
                registration_cost=Decimal(
                    request.form.get("registration_cost") or 0
                ),
                other_costs=Decimal(request.form.get("other_costs") or 0),
                notes=request.form.get("notes"),
            )
            db.session.add(new_prop)
            db.session.commit()
            flash(
                f'Property "{new_prop.name}" added successfully!', "success"
            )
            return redirect(url_for("real_estate"))
        except Exception as e:
            db.session.rollback()
            flash(f"Error adding property: {str(e)}", "danger")

    users = User.query.all()
    if not users:
        flash("You must add a User before you can add assets.", "warning")
        return redirect(url_for("add_user"))

    return render_template("add_property.html", users=users)


@app.route("/real_estate/view/<int:prop_id>")
def view_property(prop_id):
    """View details, valuations, and expenses for a single property."""
    prop = Property.query.get_or_404(prop_id)

    # --- This is the fix ---
    # Query the dynamic relationships and apply sorting here, in Python
    valuations = prop.valuations.order_by(
        PropertyValuation.valuation_date.desc()
    ).all()
    expenses = prop.expenses.order_by(
        PropertyExpense.expense_date.desc()
    ).all()

    return render_template(
        "view_property.html",
        prop=prop,
        valuations=valuations,  # Pass the sorted list
        expenses=expenses,  # Pass the sorted list
    )


@app.route("/real_estate/edit/<int:prop_id>", methods=["GET", "POST"])
def edit_property(prop_id):
    """Edit an existing property's core details."""
    prop = Property.query.get_or_404(prop_id)

    if request.method == "POST":
        try:
            prop.user_id = request.form.get("user_id")
            prop.name = request.form.get("name")
            prop.property_type = request.form.get("property_type")
            prop.address = request.form.get("address")
            prop.city = request.form.get("city")
            prop.area = Decimal(request.form.get("area"))
            prop.area_unit = request.form.get("area_unit")
            prop.purchase_date = datetime.strptime(
                request.form.get("purchase_date"), "%Y-%m-%d"
            ).date()
            prop.purchase_value = Decimal(request.form.get("purchase_value"))
            prop.registration_cost = Decimal(
                request.form.get("registration_cost") or 0
            )
            prop.other_costs = Decimal(request.form.get("other_costs") or 0)
            prop.notes = request.form.get("notes")
            prop.updated_at = datetime.utcnow()

            db.session.commit()
            flash(f'Property "{prop.name}" updated successfully!', "success")
            return redirect(url_for("view_property", prop_id=prop_id))
        except Exception as e:
            db.session.rollback()
            flash(f"Error updating property: {str(e)}", "danger")

    users = User.query.all()
    return render_template("edit_property.html", prop=prop, users=users)


@app.route("/real_estate/delete/<int:prop_id>", methods=["POST"])
def delete_property(prop_id):
    """Delete a property and all its related data."""
    prop = Property.query.get_or_404(prop_id)
    try:
        db.session.delete(prop)
        db.session.commit()
        flash(f'Property "{prop.name}" deleted successfully.', "success")
    except Exception as e:
        db.session.rollback()
        flash(f"Error deleting property: {str(e)}", "danger")
    return redirect(url_for("real_estate"))


@app.route("/real_estate/sell/<int:prop_id>", methods=["POST"])
def sell_property(prop_id):
    """Mark a property as sold."""
    prop = Property.query.get_or_404(prop_id)
    try:
        sell_date = datetime.strptime(
            request.form.get("sell_date"), "%Y-%m-%d"
        ).date()
        sell_value = Decimal(request.form.get("sell_value"))
        selling_costs = Decimal(request.form.get("selling_costs") or 0)

        if sell_date < prop.purchase_date:
            flash("Sell date cannot be before purchase date.", "danger")
            return redirect(url_for("view_property", prop_id=prop_id))

        prop.sell_date = sell_date
        prop.sell_value = sell_value
        prop.selling_costs = selling_costs
        prop.updated_at = datetime.utcnow()

        db.session.commit()
        flash(f'Property "{prop.name}" marked as sold!', "success")
    except Exception as e:
        db.session.rollback()
        flash(f"Error selling property: {str(e)}", "danger")

    return redirect(url_for("view_property", prop_id=prop_id))


# --- Valuation and Expense Routes ---


@app.route("/real_estate/valuation/add/<int:prop_id>", methods=["POST"])
def add_valuation(prop_id):
    """Add a new valuation entry for a property."""
    prop = Property.query.get_or_404(prop_id)
    try:
        new_val = PropertyValuation(
            property_id=prop_id,
            valuation_date=datetime.strptime(
                request.form.get("valuation_date"), "%Y-%m-%d"
            ).date(),
            estimated_value=Decimal(request.form.get("estimated_value")),
            source=request.form.get("source"),
        )
        db.session.add(new_val)
        db.session.commit()
        flash("New valuation added successfully.", "success")
    except Exception as e:
        db.session.rollback()
        flash(f"Error adding valuation: {str(e)}", "danger")
    return redirect(url_for("view_property", prop_id=prop_id))


@app.route("/real_estate/valuation/delete/<int:val_id>", methods=["POST"])
def delete_valuation(val_id):
    """Delete a valuation entry."""
    val = PropertyValuation.query.get_or_404(val_id)
    prop_id = val.property_id
    try:
        db.session.delete(val)
        db.session.commit()
        flash("Valuation entry deleted.", "success")
    except Exception as e:
        db.session.rollback()
        flash(f"Error deleting valuation: {str(e)}", "danger")
    return redirect(url_for("view_property", prop_id=prop_id))


@app.route("/real_estate/expense/add/<int:prop_id>", methods=["POST"])
def add_expense(prop_id):
    """Add a new expense entry for a property."""
    prop = Property.query.get_or_404(prop_id)
    try:
        is_capital = request.form.get("is_capital_improvement") == "on"

        new_exp = PropertyExpense(
            property_id=prop_id,
            expense_date=datetime.strptime(
                request.form.get("expense_date"), "%Y-%m-%d"
            ).date(),
            amount=Decimal(request.form.get("amount")),
            expense_type=request.form.get("expense_type"),
            description=request.form.get("description"),
            is_capital_improvement=is_capital,
        )
        db.session.add(new_exp)
        db.session.commit()
        flash("New expense added successfully.", "success")
    except Exception as e:
        db.session.rollback()
        flash(f"Error adding expense: {str(e)}", "danger")
    return redirect(url_for("view_property", prop_id=prop_id))


@app.route("/real_estate/expense/delete/<int:exp_id>", methods=["POST"])
def delete_expense(exp_id):
    """Delete an expense entry."""
    exp = PropertyExpense.query.get_or_404(exp_id)
    prop_id = exp.property_id
    try:
        db.session.delete(exp)
        db.session.commit()
        flash("Expense entry deleted.", "success")
    except Exception as e:
        db.session.rollback()
        flash(f"Error deleting expense: {str(e)}", "danger")
    return redirect(url_for("view_property", prop_id=prop_id))


@app.route("/real_estate/unmark_sold/<int:prop_id>", methods=["POST"])
def unmark_sold_property(prop_id):
    """Mark a property as unsold by setting sell details to NULL."""
    prop = Property.query.get_or_404(prop_id)
    try:
        prop.sell_date = None
        prop.sell_value = None
        prop.selling_costs = None
        prop.updated_at = datetime.utcnow()

        db.session.commit()
        flash(f'Property "{prop.name}" marked as unsold.', "success")
    except Exception as e:
        db.session.rollback()
        flash(f"Error marking property as unsold: {str(e)}", "danger")

    return redirect(url_for("view_property", prop_id=prop_id))


def generate_mf_hash(folio_id, scheme_id, date_obj, tx_type, units, amount):
    """Generates a unique hash for a transaction based on key immutable fields."""
    # Ensure consistent data types and formats
    units_str = f"{units:.4f}" if units is not None else "None"
    amount_str = f"{abs(amount):.2f}" if amount is not None else "None"
    date_str = date_obj.strftime("%Y-%m-%d")

    data_string = f"{folio_id}|{scheme_id}|{date_str}|{tx_type}|{units_str}|{amount_str}"
    return hashlib.sha256(data_string.encode("utf-8")).hexdigest()


# === Mutual Fund Routes ===


@app.route("/mutual_funds")
def mutual_funds():
    """Display the main Mutual Funds dashboard grouped by Scheme."""
    user_id = request.args.get("user_id", type=int)
    all_users = User.query.order_by(User.name).all()

    tx_query = MutualFundTransaction.query.join(MutualFundFolio)
    if user_id:
        tx_query = tx_query.filter(MutualFundFolio.user_id == user_id)

    transactions = (
        tx_query.options(db.joinedload(MutualFundTransaction.scheme))
        .order_by(
            MutualFundTransaction.scheme_id,
            MutualFundTransaction.transaction_date,
        )
        .all()
    )

    # --- Get Latest NAVs (logic unchanged) ---
    latest_navs = {}
    latest_nav_date = None
    latest_nav_subq = (
        db.session.query(
            MutualFundNAV.scheme_id,
            db.func.max(MutualFundNAV.nav_date).label("max_date"),
        )
        .group_by(MutualFundNAV.scheme_id)
        .subquery()
    )
    latest_nav_query = (
        db.session.query(MutualFundNAV)
        .join(
            latest_nav_subq,
            db.and_(
                MutualFundNAV.scheme_id == latest_nav_subq.c.scheme_id,
                MutualFundNAV.nav_date == latest_nav_subq.c.max_date,
            ),
        )
        .all()
    )
    for nav_entry in latest_nav_query:
        latest_navs[nav_entry.scheme_id] = nav_entry.nav
        if latest_nav_date is None or nav_entry.nav_date > latest_nav_date:
            latest_nav_date = nav_entry.nav_date

    # --- Process transactions (logic unchanged) ---
    schemes_summary_temp = {}  # Use a temporary dict first
    xirr_cash_flows = []

    for tx in transactions:
        scheme_id = tx.scheme_id
        if scheme_id not in schemes_summary_temp:
            schemes_summary_temp[scheme_id] = {
                "scheme": tx.scheme,
                "current_units": Decimal("0.0"),
                "total_purchased_units": Decimal("0.0"),
                "investment": Decimal("0.0"),
                "total_redemption_value": Decimal("0.0"),
                "transactions": [],
            }
        summary = schemes_summary_temp[scheme_id]
        summary["transactions"].append(tx)
        amount = tx.amount or Decimal("0.0")
        units = tx.units or Decimal("0.0")

        if tx.type in [
            "PURCHASE",
            "SWITCH_IN",
            "DIVIDEND_REINVEST",
            "STP_IN",
        ]:
            summary["investment"] += amount
            summary["current_units"] += units
            summary["total_purchased_units"] += units
            xirr_cash_flows.append((tx.transaction_date, -amount))
        elif tx.type in ["REDEMPTION", "SWITCH_OUT", "STP_OUT"]:
            summary["current_units"] -= abs(units)
            summary["total_redemption_value"] += amount
            xirr_cash_flows.append((tx.transaction_date, amount))

    # --- Calculate derived metrics and SEPARATE active/inactive ---
    active_schemes_summary = {}  # Holdings with units > 0
    inactive_schemes_summary = {}  # Holdings with units <= 0
    total_investment_overall = Decimal("0.0")
    total_current_value_overall = Decimal("0.0")  # Only for active holdings

    # Use a small tolerance for zero check
    ZERO_TOLERANCE = Decimal("0.0001")

    for scheme_id, summary in schemes_summary_temp.items():
        latest_nav = latest_navs.get(scheme_id, Decimal("0.0"))
        summary["current_nav"] = latest_nav
        summary["current_value"] = summary["current_units"] * latest_nav
        summary["pnl"] = (
            summary["current_value"] + summary["total_redemption_value"]
        ) - summary["investment"]
        summary["avg_nav"] = (
            (summary["investment"] / summary["total_purchased_units"])
            if summary["total_purchased_units"] > 0
            else Decimal("0.0")
        )

        # Scheme XIRR (logic unchanged)
        scheme_cash_flows = []
        for tx in summary["transactions"]:
            amount = tx.amount or Decimal("0.0")
            if tx.type in [
                "PURCHASE",
                "SWITCH_IN",
                "DIVIDEND_REINVEST",
                "STP_IN",
            ]:
                scheme_cash_flows.append((tx.transaction_date, -amount))
            elif tx.type in ["REDEMPTION", "SWITCH_OUT", "STP_OUT"]:
                scheme_cash_flows.append((tx.transaction_date, amount))
        if summary["current_units"] > ZERO_TOLERANCE and latest_nav_date:
            scheme_cash_flows.append((date.today(), summary["current_value"]))
        summary["xirr"] = calculate_xirr(scheme_cash_flows)

        # --- SEPARATION LOGIC ---
        if abs(summary["current_units"]) < ZERO_TOLERANCE:
            inactive_schemes_summary[scheme_id] = summary
            # Include investment cost in overall investment even if inactive
            total_investment_overall += summary["investment"]
        else:
            active_schemes_summary[scheme_id] = summary
            total_investment_overall += summary["investment"]
            total_current_value_overall += summary[
                "current_value"
            ]  # Only count current value of active

        del summary["transactions"]  # Clean up before sending

    # Calculate Overall XIRR (using active holdings' current value)
    if total_current_value_overall > 0 and latest_nav_date:
        xirr_cash_flows.append((date.today(), total_current_value_overall))
    overall_xirr = calculate_xirr(xirr_cash_flows)

    overall_summary = {
        "total_investment": total_investment_overall,
        "total_current_value": total_current_value_overall,  # Based on active holdings
        "total_pnl": total_current_value_overall - total_investment_overall,
        "overall_xirr": overall_xirr,
        "latest_nav_date": latest_nav_date,
    }

    return render_template(
        "mutual_funds.html",
        active_schemes_summary=active_schemes_summary,  # Pass active schemes
        inactive_schemes_summary=inactive_schemes_summary,  # Pass inactive schemes
        summary=overall_summary,
        all_users=all_users,
        selected_user_id=user_id,
    )


@app.route("/mutual_funds/scheme_details/<int:scheme_id>")
def mf_scheme_details(scheme_id):
    """Show transaction history for a specific scheme."""
    user_id = request.args.get(
        "user_id", type=int
    )  # Get user_id from query param

    scheme = MutualFundScheme.query.get_or_404(scheme_id)

    # Base query for transactions of this scheme
    tx_query = (
        MutualFundTransaction.query.filter_by(scheme_id=scheme_id)
        .join(MutualFundFolio)
        .options(db.joinedload(MutualFundTransaction.folio))
    )  # Eager load folio

    # Filter by user if specified
    if user_id:
        tx_query = tx_query.filter(MutualFundFolio.user_id == user_id)

    transactions = tx_query.order_by(
        MutualFundTransaction.transaction_date.asc()
    ).all()

    # --- Calculate Scheme specific details ---
    current_units = Decimal("0.0")
    investment = Decimal("0.0")
    total_redemption_value = Decimal("0.0")
    scheme_cash_flows = []
    folios_involved = set()  # Track unique folios

    for tx in transactions:
        folios_involved.add(tx.folio.folio_number)
        amount = tx.amount or Decimal("0.0")
        units = tx.units or Decimal("0.0")

        if tx.type in [
            "PURCHASE",
            "SWITCH_IN",
            "DIVIDEND_REINVEST",
            "STP_IN",
        ]:
            current_units += units
            investment += amount
            scheme_cash_flows.append((tx.transaction_date, -amount))
        elif tx.type in ["REDEMPTION", "SWITCH_OUT", "STP_OUT"]:
            current_units -= abs(units)
            total_redemption_value += amount
            scheme_cash_flows.append((tx.transaction_date, amount))

    # Get latest NAV for current value
    latest_nav_entry = (
        MutualFundNAV.query.filter_by(scheme_id=scheme_id)
        .order_by(MutualFundNAV.nav_date.desc())
        .first()
    )
    current_value = Decimal("0.0")
    if latest_nav_entry and current_units > 0:
        current_value = current_units * latest_nav_entry.nav
        scheme_cash_flows.append(
            (date.today(), current_value)
        )  # Add current value for XIRR

    scheme_xirr = calculate_xirr(scheme_cash_flows)

    # Pass user_id back for consistent filtering
    selected_user_id = user_id
    all_users = User.query.order_by(User.name).all()  # For filter consistency

    return render_template(
        "mf_scheme_details.html",
        scheme=scheme,
        transactions=transactions,
        current_units=current_units,
        scheme_xirr=scheme_xirr,
        folio_numbers=", ".join(
            sorted(list(folios_involved))
        ),  # Comma-separated list
        all_users=all_users,
        selected_user_id=selected_user_id,
    )


@app.route("/mutual_funds/add", methods=["GET", "POST"])
def add_mf_transaction():
    """Add a new MF transaction manually."""
    if request.method == "POST":
        try:
            # --- Get or Create Folio ---
            user_id = int(request.form.get("user_id"))
            folio_number = request.form.get("folio_number")
            amc = request.form.get("amc")
            folio = MutualFundFolio.query.filter_by(
                user_id=user_id, amc=amc, folio_number=folio_number
            ).first()
            if not folio:
                folio = MutualFundFolio(
                    user_id=user_id, amc=amc, folio_number=folio_number
                )
                db.session.add(folio)
                db.session.flush()  # Get folio.id before commit

            # --- Get or Create Scheme ---
            isin = request.form.get("isin")
            scheme_name = request.form.get("scheme_name")
            scheme = MutualFundScheme.query.filter_by(isin=isin).first()
            if not scheme:
                scheme = MutualFundScheme(name=scheme_name, isin=isin)
                db.session.add(scheme)
                db.session.flush()  # Get scheme.id before commit

            # --- Create Transaction ---
            units = Decimal(request.form.get("units") or 0)
            amount = Decimal(request.form.get("amount") or 0)
            nav = Decimal(request.form.get("nav") or 0)
            tx_date = datetime.strptime(
                request.form.get("transaction_date"), "%Y-%m-%d"
            ).date()
            tx_type = request.form.get("type")

            # Manual transactions don't have a reliable unique hash like CAS
            # We rely on user not duplicating or handle it via UI constraints later
            new_tx = MutualFundTransaction(
                folio_id=folio.id,
                scheme_id=scheme.id,
                transaction_date=tx_date,
                description=request.form.get("description"),
                amount=amount if amount > 0 else None,
                units=units if units != 0 else None,  # Allow negative units
                nav=nav if nav > 0 else None,
                type=tx_type,
                dividend_rate=(
                    Decimal(request.form.get("dividend_rate") or 0)
                    if request.form.get("dividend_rate")
                    else None
                ),
                unique_hash=None,  # Mark as manual entry
            )
            db.session.add(new_tx)
            db.session.commit()
            flash("Mutual Fund transaction added successfully!", "success")
            return redirect(url_for("mutual_funds"))

        except IntegrityError:
            db.session.rollback()
            flash(
                "Database error: Could not add transaction, possibly a constraint violation.",
                "danger",
            )
        except Exception as e:
            db.session.rollback()
            flash(f"Error adding transaction: {str(e)}", "danger")

    # GET request or failed POST
    users = User.query.order_by(User.name).all()
    # Pass existing schemes/folios for potential dropdowns/datalists
    schemes = MutualFundScheme.query.order_by(MutualFundScheme.name).all()
    folios = MutualFundFolio.query.order_by(
        MutualFundFolio.amc, MutualFundFolio.folio_number
    ).all()
    return render_template(
        "add_mf_transaction.html", users=users, schemes=schemes, folios=folios
    )


@app.route("/mutual_funds/edit/<int:tx_id>", methods=["GET", "POST"])
def edit_mf_transaction(tx_id):
    """Edit an existing manually added MF transaction."""
    tx = MutualFundTransaction.query.get_or_404(tx_id)
    # Potentially restrict editing CAS-imported transactions if tx.unique_hash is not None

    if request.method == "POST":
        try:
            # --- Update Transaction Fields ---
            # NOTE: We generally SHOULD NOT allow changing Folio or Scheme easily
            # as it breaks historical context. Best to delete and re-add if wrong.
            tx.transaction_date = datetime.strptime(
                request.form.get("transaction_date"), "%Y-%m-%d"
            ).date()
            tx.description = request.form.get("description")
            tx.amount = Decimal(request.form.get("amount") or 0) or None
            tx.units = Decimal(request.form.get("units") or 0) or None
            tx.nav = Decimal(request.form.get("nav") or 0) or None
            tx.type = request.form.get("type")
            tx.dividend_rate = (
                Decimal(request.form.get("dividend_rate") or 0)
                if request.form.get("dividend_rate")
                else None
            )

            # Add validation: balance units shouldn't be negative, etc.

            db.session.commit()
            flash("Transaction updated successfully!", "success")
            return redirect(
                url_for("mutual_funds")
            )  # Redirect to main list for now

        except Exception as e:
            db.session.rollback()
            flash(f"Error updating transaction: {str(e)}", "danger")

    # GET request or failed POST
    # Pass necessary data for the form (users, schemes, folios if needed for dropdowns)
    return render_template("edit_mf_transaction.html", tx=tx)


@app.route("/mutual_funds/delete/<int:tx_id>", methods=["POST"])
def delete_mf_transaction(tx_id):
    """Delete an MF transaction."""
    tx = MutualFundTransaction.query.get_or_404(tx_id)
    try:
        db.session.delete(tx)
        db.session.commit()
        flash("Transaction deleted successfully.", "success")
    except Exception as e:
        db.session.rollback()
        flash(f"Error deleting transaction: {str(e)}", "danger")
    return redirect(url_for("mutual_funds"))


@app.route("/mutual_funds/upload_cas", methods=["GET", "POST"])
def upload_cas():
    """Handle CAS PDF upload."""
    if request.method == "POST":
        if "file" not in request.files:
            flash("No file part", "danger")
            return redirect(url_for("upload_cas"))

        file = request.files["file"]
        password = request.form.get("password")
        user_id = request.form.get("user_id")

        if file.filename == "":
            flash("No selected file", "danger")
            return redirect(url_for("upload_cas"))

        if not file.filename.endswith(".pdf"):
            flash(
                "Invalid file type. Please upload a password-protected PDF file.",
                "danger",
            )
            return redirect(url_for("upload_cas"))

        try:
            # Read file content into memory
            file_content = file.read()

            # Use casparser
            data = json.loads(
                read_cas_pdf(
                    io.BytesIO(file_content), password, output="json"
                )
            )

            if not data or "folios" not in data:
                flash(
                    "Could not parse CAS data or no folios found.", "warning"
                )
                return redirect(url_for("upload_cas"))

            # --- Process Data (All or Nothing) ---
            schemes_cache = (
                {}
            )  # Cache schemes found/created in this upload (ISIN -> Scheme object)
            folios_cache = (
                {}
            )  # Cache folios found/created ( (user_id, amc, folio_num) -> Folio object)
            transactions_to_add = []
            navs_to_add_or_update = {}
            errors = []

            user = User.query.get(user_id)
            if not user:
                flash(
                    f"User with ID {user_id} not found. Cannot process CAS.",
                    "danger",
                )
                return redirect(url_for("upload_cas"))

            # --- Iterate and Validate ---
            for folio_data in data["folios"]:
                folio_key = (user.id, folio_data["amc"], folio_data["folio"])
                folio = folios_cache.get(folio_key)
                if not folio:
                    folio = MutualFundFolio.query.filter_by(
                        user_id=user.id,
                        amc=folio_data["amc"],
                        folio_number=folio_data["folio"],
                    ).first()
                    if not folio:
                        folio = MutualFundFolio(
                            user_id=user.id,
                            amc=folio_data["amc"],
                            folio_number=folio_data["folio"],
                        )
                        db.session.add(folio)
                        try:
                            db.session.flush()  # Get ID before full commit
                        except IntegrityError:
                            db.session.rollback()
                            errors.append(
                                f"Error creating folio {folio_data['folio']} for AMC {folio_data['amc']}. Duplicate?"
                            )
                            continue  # Skip this folio
                    folios_cache[folio_key] = folio

                for scheme_data in folio_data["schemes"]:
                    isin = scheme_data.get("isin")
                    if not isin:
                        errors.append(
                            f"Scheme '{scheme_data['scheme']}' in folio {folio.folio_number} is missing ISIN. Skipping."
                        )
                        continue

                    scheme = schemes_cache.get(isin)
                    if not scheme:
                        scheme = MutualFundScheme.query.filter_by(
                            isin=isin
                        ).first()
                        if not scheme:
                            scheme = MutualFundScheme(
                                name=scheme_data["scheme"],
                                isin=isin,
                                amfi_code=scheme_data.get("amfi"),
                                rta_code=scheme_data.get("rta_code"),
                                rta=scheme_data.get("rta"),
                                type=scheme_data.get("type"),
                            )
                            db.session.add(scheme)
                            try:
                                db.session.flush()  # Get ID
                            except IntegrityError:
                                db.session.rollback()
                                errors.append(
                                    f"Error creating scheme {scheme_data['scheme']} (ISIN: {isin}). Duplicate?"
                                )
                                continue  # Skip schemes in this folio if creation fails
                        schemes_cache[isin] = scheme

                    for tx_data in scheme_data["transactions"]:
                        try:
                            tx_type = tx_data.get("type")
                            if tx_type in [
                                "PURCHASE",
                                "REDEMPTION",
                            ]:
                                tx_date = datetime.strptime(
                                    tx_data["date"], "%Y-%m-%d"
                                ).date()  # CAS date format
                                units = (
                                    Decimal(str(tx_data.get("units") or 0))
                                    if tx_data.get("units") is not None
                                    else None
                                )
                                amount = (
                                    Decimal(str(tx_data.get("amount") or 0))
                                    if tx_data.get("amount") is not None
                                    else None
                                )
                                nav = (
                                    Decimal(str(tx_data.get("nav") or 0))
                                    if tx_data.get("nav") is not None
                                    else None
                                )
                                abs_amount = (
                                    abs(amount)
                                    if amount is not None
                                    else None
                                )

                                # Generate hash for deduplication
                                tx_hash = generate_mf_hash(
                                    folio.id,
                                    scheme.id,
                                    tx_date,
                                    tx_type,
                                    units,
                                    abs_amount,
                                )

                                # Check if hash already exists (using a query for atomicity)
                                exists = (
                                    db.session.query(MutualFundTransaction.id)
                                    .filter_by(unique_hash=tx_hash)
                                    .first()
                                    is not None
                                )
                                if not exists:
                                    transactions_to_add.append(
                                        MutualFundTransaction(
                                            folio_id=folio.id,
                                            scheme_id=scheme.id,
                                            transaction_date=tx_date,
                                            amount=abs_amount,
                                            units=units,
                                            nav=nav,
                                            type=tx_type,
                                            unique_hash=tx_hash,
                                        )
                                    )
                                # Add NAV data to cache for later upsert
                                if nav and tx_date:
                                    nav_key = (scheme.id, tx_date)
                                    navs_to_add_or_update[nav_key] = nav

                        except Exception as e:
                            errors.append(
                                f"Row Error (Folio: {folio.folio_number}, Scheme: {scheme.name}, Date: {tx_data.get('date')}): {str(e)}"
                            )

            # --- ATOMIC COMMIT ---
            if errors:
                db.session.rollback()
                flash(
                    f"Upload failed. {len(errors)} errors found. No records were added.",
                    "danger",
                )
                error_html = (
                    "<ul>"
                    + "".join([f"<li>{e}</li>" for e in errors[:10]])
                    + "</ul>"
                )
                if len(errors) > 10:
                    error_html += (
                        f"<p>...and {len(errors) - 10} more errors.</p>"
                    )
                flash(Markup(error_html), "danger")

            elif not transactions_to_add and not navs_to_add_or_update:
                flash(
                    "No new transactions or NAV updates found in the file.",
                    "info",
                )

            else:
                try:
                    # Add new transactions
                    if transactions_to_add:
                        db.session.add_all(transactions_to_add)

                    # Upsert NAVs
                    if navs_to_add_or_update:
                        existing_navs = {
                            (n.scheme_id, n.nav_date): n
                            for n in MutualFundNAV.query.filter(
                                MutualFundNAV.scheme_id.in_(
                                    [k[0] for k in navs_to_add_or_update]
                                ),
                                MutualFundNAV.nav_date.in_(
                                    [k[1] for k in navs_to_add_or_update]
                                ),
                            ).all()
                        }

                        for (
                            scheme_id,
                            nav_date,
                        ), nav_value in navs_to_add_or_update.items():
                            nav_entry = existing_navs.get(
                                (scheme_id, nav_date)
                            )
                            if nav_entry:
                                if not abs(
                                    nav_entry.nav - nav_value
                                ) < Decimal("0.0001"):
                                    nav_entry.nav = (
                                        nav_value  # Update if different
                                    )
                                    db.session.add(nav_entry)
                            else:
                                new_nav = MutualFundNAV(
                                    scheme_id=scheme_id,
                                    nav_date=nav_date,
                                    nav=nav_value,
                                )
                                db.session.add(new_nav)

                    db.session.commit()
                    flash(
                        f"Successfully processed CAS file. Added {len(transactions_to_add)} new transactions. Updated/Added NAVs.",
                        "success",
                    )
                except Exception as e:
                    db.session.rollback()
                    flash(f"Error during final commit: {str(e)}", "danger")

        except Exception as e:
            db.session.rollback()  # Rollback any partial flushes
            flash(f"Error processing PDF: {str(e)}", "danger")

        return redirect(url_for("mutual_funds"))

    # GET request
    users = User.query.order_by(User.name).all()
    return render_template("upload_cas.html", users=users)


@app.route("/mutual_funds/fetch_navs", methods=["POST"])
def fetch_all_navs():
    """Fetch historical NAVs for all schemes with AMFI codes."""

    schemes_to_fetch = MutualFundScheme.query.filter(
        MutualFundScheme.amfi_code != None
    ).all()

    if not schemes_to_fetch:
        flash(
            "No schemes with AMFI codes found in the database to fetch NAVs for.",
            "info",
        )
        return redirect(url_for("mutual_funds"))

    schemes_processed = 0
    new_navs_added_count = 0
    schemes_failed = []

    # --- FIX: Use string_agg for PostgreSQL ---
    existing_nav_dates = (
        db.session.query(
            MutualFundNAV.scheme_id,
            # Cast date to TEXT/VARCHAR before aggregating
            func.string_agg(
                cast(MutualFundNAV.nav_date, TEXT).distinct(), ","
            ).label("dates_str"),
        )
        .group_by(MutualFundNAV.scheme_id)
        .all()
    )
    # ----------------------------------------

    existing_nav_map = {}
    for scheme_id, dates_str in existing_nav_dates:
        if dates_str:
            try:
                # Date format from DB/cast should be YYYY-MM-DD
                existing_nav_map[scheme_id] = {
                    datetime.strptime(d.strip(), "%Y-%m-%d").date()
                    for d in dates_str.split(",")
                }
            except ValueError:
                print(
                    f"Warning: Could not parse dates '{dates_str}' for scheme_id {scheme_id}"
                )
                existing_nav_map[scheme_id] = set()

    navs_to_add = []

    for scheme in schemes_to_fetch:
        api_url = f"https://api.mfapi.in/mf/{scheme.amfi_code}"
        try:
            response = requests.get(api_url, timeout=30)
            response.raise_for_status()
            data = response.json()

            if (
                not data
                or data.get("status") != "SUCCESS"
                or "data" not in data
            ):
                schemes_failed.append(
                    f"{scheme.name} (AMFI: {scheme.amfi_code}) - Invalid API response"
                )
                continue

            scheme_existing_dates = existing_nav_map.get(scheme.id, set())

            for nav_entry in data["data"]:
                try:
                    nav_date_str = nav_entry.get("date")
                    nav_value_str = nav_entry.get("nav")
                    nav_date = datetime.strptime(
                        nav_date_str, "%d-%m-%Y"
                    ).date()  # API format
                    nav_value = Decimal(str(nav_value_str))

                    if nav_date not in scheme_existing_dates:
                        if nav_value > 0:
                            navs_to_add.append(
                                MutualFundNAV(
                                    scheme_id=scheme.id,
                                    nav_date=nav_date,
                                    nav=nav_value,
                                )
                            )
                            scheme_existing_dates.add(
                                nav_date
                            )  # Add locally to prevent duplicates in batch
                            new_navs_added_count += 1

                except (
                    ValueError,
                    TypeError,
                    KeyError,
                    InvalidOperation,
                ) as e:
                    print(
                        f"Warning: Skipping NAV entry for {scheme.name} due to data error: {e} - Data: {nav_entry}"
                    )
                    continue

            schemes_processed += 1

        except requests.exceptions.RequestException as e:
            schemes_failed.append(
                f"{scheme.name} (AMFI: {scheme.amfi_code}) - Network/API Error: {e}"
            )
        except Exception as e:
            schemes_failed.append(
                f"{scheme.name} (AMFI: {scheme.amfi_code}) - Processing Error: {e}"
            )

    # --- Commit logic (unchanged) ---
    if navs_to_add:
        try:
            db.session.add_all(navs_to_add)
            db.session.commit()
            flash(
                f"Successfully added {new_navs_added_count} new NAV entries for {schemes_processed} schemes.",
                "success",
            )
        except Exception as e:
            db.session.rollback()
            flash(
                f"Error saving new NAV entries to database: {str(e)}",
                "danger",
            )
    else:
        flash(
            f"Processed {schemes_processed} schemes. No new NAV entries needed.",
            "info",
        )

    if schemes_failed:
        flash(
            f"Failed to fetch NAVs for {len(schemes_failed)} schemes:",
            "warning",
        )
        error_html = (
            "<ul>"
            + "".join([f"<li>{err}</li>" for err in schemes_failed[:5]])
            + "</ul>"
        )
        if len(schemes_failed) > 5:
            error_html += "<p>...and more.</p>"
        flash(Markup(error_html), "warning")

    return redirect(url_for("mutual_funds"))
