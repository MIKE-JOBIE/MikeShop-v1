# routes/auth.py - Login and Logout only
from flask import render_template, request, redirect, url_for, session, flash
from app import app, csrf
from database import db, limiter
from models.core import User
from werkzeug.security import check_password_hash
from datetime import datetime

# ==================== AUTH ====================
@app.route("/", methods=["GET", "POST"])
@limiter.limit("5 per minute")
def login():
    if request.method == "POST":
        username = request.form.get("username", "").strip()
        password = request.form.get("password", "")

        if not username or not password:
            flash("Username and password are required.")
            return render_template("login.html")

        user = User.query.filter_by(username=username).first()
        if user and not user.is_active:
            flash("This account has been deactivated. Contact the owner.")
            return render_template("login.html")

        if user and check_password_hash(user.password_hash, password):
            session.clear()
            session.update({
                "user_id": user.id,
                "username": user.username,
                "role": user.role.name
            })
            # Update last login
            user.last_login = datetime.utcnow()
            db.session.commit()
            return redirect(url_for("dashboard"))

        flash("Invalid login credentials.")

    return render_template("login.html")

@app.route("/logout")
def logout():
    session.clear()
    return redirect(url_for("login"))