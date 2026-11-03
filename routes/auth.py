import logging
from flask import Blueprint, render_template, request, redirect, url_for, flash
from flask_login import login_required, logout_user, login_user
from database import db
from models.user import User
from utils.i18n import translate as _

logger = logging.getLogger(__name__)

bp = Blueprint('auth', __name__)


@bp.route('/register', methods=['GET', 'POST'])
def register():
    if request.method == 'POST':
        username = (request.form.get('username') or '').strip()
        email = (request.form.get('email') or '').strip()
        password = request.form.get('password') or ''

        if not username or not email or not password:
            flash('All fields are required!', 'error')
            return render_template('register.html')

        # Check if username or email already exists
        if User.query.filter_by(username=username).first():
            logger.warning("Registration failed: username '%s' already exists", username)
            flash('Username already exists! Please choose another one.', 'error')
            return render_template('register.html')

        if User.query.filter_by(email=email).first():
            logger.warning("Registration failed: email '%s' already registered", email)
            flash('Email already registered! Please log in.', 'error')
            return render_template('register.html')

        # Create user and save to database
        new_user = User(username=username, email=email)
        new_user.set_password(password)
        db.session.add(new_user)
        db.session.commit()

        logger.info("New user registered: '%s' (%s)", username, email)
        flash('Registration successful! Please log in.', 'success')
        return redirect(url_for('auth.login'))

    return render_template('register.html')


@bp.route('/login', methods=['GET', 'POST'])
def login():
    if request.method == 'POST':
        username = request.form['username']
        password = request.form['password']
        user = User.query.filter_by(username=username).first()

        if user and user.check_password(password):
            login_user(user, remember=True)
            next_page = request.args.get('next')
            logger.info("User '%s' logged in successfully", username)
            return redirect(next_page) if next_page else redirect(url_for('pi_agent.page'))
        else:
            logger.warning("Login failed for username '%s'", username)
            flash('Invalid username or password!', 'error')

    return render_template('login.html')


@bp.route('/logout')
@login_required
def logout():
    logger.info("User logged out")
    logout_user()
    flash('You have been logged out.', 'info')
    return redirect(url_for('auth.login'))
