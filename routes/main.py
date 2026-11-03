import logging
from flask import Blueprint, render_template, request, redirect, url_for, flash
from flask_login import login_required, current_user
from database import db
from models.post import Post

logger = logging.getLogger(__name__)

bp = Blueprint('main', __name__)


@bp.route('/posts')
@login_required
def index():
    posts = Post.query.order_by(Post.created_at.desc()).all()
    return render_template('index.html', posts=posts)


@bp.route('/post/<int:id>')
@login_required
def view_post(id):
    post = Post.query.get_or_404(id)
    return render_template('post.html', post=post)


@bp.route('/create', methods=['GET', 'POST'])
@login_required
def create_post():
    if request.method == 'POST':
        title = request.form['title']
        content = request.form['content']

        if title and content:
            post = Post(title=title, content=content)
            db.session.add(post)
            db.session.commit()
            logger.info("Post created: '%s' (id=%d) by user '%s'", title, post.id, current_user.username)
            flash('Post created successfully!', 'success')
            return redirect(url_for('main.index'))
        else:
            flash('Title and content are required!', 'error')

    return render_template('create.html')
