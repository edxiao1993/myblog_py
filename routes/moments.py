import logging
from flask import Blueprint, render_template, request, redirect, url_for, flash
from flask_login import login_required, current_user
from database import db
from models.secret_moment import SecretMoment

logger = logging.getLogger(__name__)

bp = Blueprint('moments', __name__)


@bp.route('/')
@bp.route('/page/<int:page>')
@login_required
def moments(page=1):
    per_page = 10
    moments_pagination = SecretMoment.query.filter_by(user_id=current_user.id).order_by(SecretMoment.create_time.desc()).paginate(
        page=page, per_page=per_page, error_out=False
    )
    return render_template('moments.html', moments=moments_pagination.items, pagination=moments_pagination)


@bp.route('/add_moment', methods=['GET', 'POST'])
@login_required
def add_moment():
    if request.method == 'POST':
        moment_text = request.form['moment']

        if moment_text:
            moment = SecretMoment(moment=moment_text, user_id=current_user.id)
            db.session.add(moment)
            db.session.commit()
            logger.info("Moment created (id=%s)", moment.id)
            flash('Secret moment added successfully!', 'success')
            return redirect(url_for('moments.moments'))
        else:
            flash('Moment content is required!', 'error')

    return render_template('add_moment.html')


@bp.route('/delete_moment/<string:id>', methods=['POST'])
@login_required
def delete_moment(id):
    moment = SecretMoment.query.filter_by(id=id, user_id=current_user.id).first_or_404()
    db.session.delete(moment)
    db.session.commit()
    logger.info("Moment deleted (id=%s)", id)
    flash('Secret moment deleted successfully!', 'success')
    return redirect(url_for('moments.moments'))
