import json
import logging
from flask import Blueprint, Response, request, jsonify
from database import db
from models.secret_moment import SecretMoment
from utils.jwt import jwt_required

logger = logging.getLogger(__name__)

bp = Blueprint('app_api', __name__)


@bp.route('/protected-example', methods=['GET'])
@jwt_required
def protected_example():
    """
    Example of a token-protected API endpoint.
    
    Requires:
    - X-Token: <generated_token>
    
    Response: Protected data for the authenticated user
    """
    user = request.current_user
    return jsonify({
        'message': 'This is protected data',
        'user': user.username,
        'email': user.email
    })


@bp.route('/moments', methods=['GET'])
@bp.route('/mm/<int:page>', methods=['GET'])
@jwt_required
def api_moments(page=1):
    page = request.args.get('page', page, type=int) or 1
    per_page = request.args.get('per_page', 10, type=int) or 10
    per_page = min(max(per_page, 1), 50)
    moments_pagination = SecretMoment.query.filter_by(user_id=request.current_user.id).order_by(SecretMoment.create_time.desc()).paginate(
        page=page, per_page=per_page, error_out=False
    )

    moments = [{
        'id': moment.id,
        'moment': moment.moment,
        'create_time': moment.create_time.strftime('%Y-%m-%d %H:%M:%S')
    } for moment in moments_pagination.items]

    response_data = {
        'moments': moments,
        'page': moments_pagination.page,
        'per_page': per_page,
        'pages': moments_pagination.pages,
        'total': moments_pagination.total,
        'has_next': moments_pagination.has_next,
        'has_prev': moments_pagination.has_prev
    }

    return Response(
        json.dumps(response_data, ensure_ascii=False),
        mimetype='application/json'
    )


@bp.route('/moments', methods=['POST'])
@jwt_required
def api_create_moment():
    """
    Create a new SecretMoment.

    Headers: Authorization: Bearer <access_token>
    Request:  {"moment": "Your secret moment content here"}
    Response: {
        "message": "Secret moment created successfully",
        "moment": {
            "id": "c1f7...",
            "moment": "Your secret moment content here",
            "create_time": "2026-09-23 19:15:00"
        }
    }
    """
    data = request.get_json(silent=True) or {}
    moment_text = (data.get('moment') or '').strip()

    if not moment_text:
        return jsonify({'error': 'moment content is required'}), 400

    new_moment = SecretMoment(moment=moment_text, user_id=request.current_user.id)
    db.session.add(new_moment)
    db.session.commit()

    logger.info("API moment created (id=%s) by user '%s'", new_moment.id, request.current_user.username)

    return jsonify({
        'message': 'Secret moment created successfully',
        'moment': {
            'id': new_moment.id,
            'moment': new_moment.moment,
            'create_time': new_moment.create_time.strftime('%Y-%m-%d %H:%M:%S')
        }
    }), 201


@bp.route('/moments/<string:moment_id>', methods=['DELETE'])
@jwt_required
def api_delete_moment(moment_id):
    """
    Delete a SecretMoment by its ID.

    Headers: Authorization: Bearer <access_token>
    Response: {"message": "Secret moment deleted successfully"}
    """
    moment = SecretMoment.query.filter_by(id=moment_id, user_id=request.current_user.id).first()
    if not moment:
        return jsonify({'error': 'Secret moment not found'}), 404

    db.session.delete(moment)
    db.session.commit()

    return jsonify({'message': 'Secret moment deleted successfully'})

