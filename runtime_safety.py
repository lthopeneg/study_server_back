"""Bound database waits and log request progress without credentials or bodies."""
import logging
import time
from uuid import uuid4

from flask import g, request
from sqlalchemy.engine import make_url


def database_engine_options(uri):
    if make_url(uri).drivername != 'mysql+pymysql':
        return {}
    return {
        'pool_pre_ping': True,
        'pool_recycle': 300,
        'pool_timeout': 5,
        'connect_args': {
            'connect_timeout': 5,
            'read_timeout': 15,
            'write_timeout': 15,
        },
    }


def install_request_logging(app):
    logger = logging.getLogger('study.requests')
    logger.setLevel(logging.INFO)
    if not logger.handlers:
        handler = logging.StreamHandler()
        handler.setFormatter(logging.Formatter('%(asctime)s %(levelname)s %(message)s'))
        logger.addHandler(handler)
    logger.propagate = False

    @app.before_request
    def start_request():
        g.request_id = uuid4().hex
        g.request_started = time.monotonic()
        # Log route templates only, never raw URLs, query strings, tokens or bodies.
        g.request_route = request.url_rule.rule if request.url_rule else '<unmatched>'
        logger.info('request_start id=%s method=%s route=%s',
                    g.request_id, request.method, g.request_route)

    @app.after_request
    def finish_request(response):
        if hasattr(g, 'request_started'):
            elapsed = time.monotonic() - g.request_started
            log = logger.warning if elapsed >= 5 else logger.info
            log('request_end id=%s status=%s duration_ms=%.0f',
                g.request_id, response.status_code, elapsed * 1000)
            response.headers['X-Request-ID'] = g.request_id
        return response

    @app.teardown_request
    def failed_request(error):
        if error is not None and hasattr(g, 'request_id'):
            logger.error('request_failed id=%s error_type=%s',
                         g.request_id, type(error).__name__)
