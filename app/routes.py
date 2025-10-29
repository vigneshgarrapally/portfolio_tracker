import csv
import io
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
)
from datetime import datetime, date
from decimal import Decimal
from markupsafe import Markup

from app.utils import calculate_xirr


from selenium import webdriver
from selenium.webdriver.common.by import By
from selenium.webdriver.support.ui import WebDriverWait
from selenium.webdriver.support import expected_conditions as EC
from selenium.common.exceptions import TimeoutException

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
