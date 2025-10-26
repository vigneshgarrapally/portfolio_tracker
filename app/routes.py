import json
from flask import render_template, request, redirect, url_for, flash, jsonify
from app import app, db
from app.models import User, GoldTransaction, GoldSellTransaction
from datetime import datetime
from decimal import Decimal

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

    buy_query = GoldTransaction.query

    if user_id:
        buy_query = buy_query.filter_by(user_id=user_id)

    # --- Only query for holdings (Buy Transactions) ---
    buy_transactions = buy_query.order_by(
        GoldTransaction.invoice_date.desc()
    ).all()

    # --- Summary stats are still calculated from totals ---
    total_buy_grams = db.session.query(
        db.func.sum(GoldTransaction.grams)
    ).select_from(buy_query.subquery()).scalar() or Decimal("0.0")
    total_investment = db.session.query(
        db.func.sum(GoldTransaction.grams * GoldTransaction.per_gm_price)
    ).select_from(buy_query.subquery()).scalar() or Decimal("0.0")

    # Get total sell grams *for the filtered user*
    sell_query = GoldSellTransaction.query
    if user_id:
        sell_query = sell_query.filter_by(user_id=user_id)
    total_sell_grams = db.session.query(
        db.func.sum(GoldSellTransaction.grams)
    ).select_from(sell_query.subquery()).scalar() or Decimal("0.0")

    current_holdings = total_buy_grams - total_sell_grams
    average_price = (
        (total_investment / total_buy_grams)
        if total_buy_grams > 0
        else Decimal("0.0")
    )

    summary = {
        "current_holdings": current_holdings,
        "total_investment": total_investment,
        "average_price": average_price,
        "current_value": 0,
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


@app.route("/gold/upload_json", methods=["POST"])
def upload_gold_json():
    """Add gold transactions from a JSON file upload."""
    if "file" not in request.files:
        flash("No file part", "danger")
        return redirect(url_for("add_gold"))

    file = request.files["file"]
    if file.filename == "":
        flash("No selected file", "danger")
        return redirect(url_for("add_gold"))

    if file and file.filename.endswith(".json"):
        try:
            data = json.load(file.stream)
            count = 0
            for item in data:
                new_tx = GoldTransaction(
                    user_id=item.get("user_id"),
                    invoice_date=datetime.strptime(
                        item.get("invoice_date"), "%Y-%m-%d"
                    ).date(),
                    grams=Decimal(item.get("grams")),
                    per_gm_price=Decimal(item.get("per_gm_price")),
                    purity=item.get("purity"),
                    platform=item.get("platform"),
                    type=item.get("type"),
                    brand=item.get("brand"),
                )
                db.session.add(new_tx)
                count += 1

            db.session.commit()
            flash(
                f"Successfully uploaded and added {count} transactions.",
                "success",
            )
        except Exception as e:
            db.session.rollback()
            flash(f"Error processing JSON file: {str(e)}", "danger")
    else:
        flash("Invalid file type. Please upload a .json file.", "danger")

    return redirect(url_for("gold"))
