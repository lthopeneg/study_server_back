def parse_boolean_setting(value, default=False):
    if value is None:
        return default
    normalized = value.strip().lower()
    if normalized in {'1', 'true', 'yes', 'on'}:
        return True
    if normalized in {'0', 'false', 'no', 'off'}:
        return False
    raise ValueError('Boolean environment setting must be true or false.')


def parse_cors_origins(value):
    origins = [origin.strip().rstrip('/') for origin in (value or '').split(',') if origin.strip()]
    if not origins:
        return ['http://localhost:5173']
    if '*' in origins:
        raise ValueError('Credentialed CORS cannot allow every origin.')
    return origins


def install_security_headers(app):
    """Apply defense-in-depth headers to API responses."""
    @app.after_request
    def add_security_headers(response):
        response.headers.setdefault('Strict-Transport-Security', 'max-age=31536000')
        response.headers.setdefault('X-Content-Type-Options', 'nosniff')
        response.headers.setdefault('X-Frame-Options', 'DENY')
        response.headers.setdefault('Referrer-Policy', 'strict-origin-when-cross-origin')
        response.headers.setdefault(
            'Permissions-Policy',
            'camera=(), microphone=(), geolocation=(), payment=(), usb=()',
        )
        response.headers.setdefault('Cross-Origin-Opener-Policy', 'same-origin')
        response.headers.setdefault('Cross-Origin-Resource-Policy', 'same-origin')
        response.headers.setdefault('Cache-Control', 'no-store')
        return response
