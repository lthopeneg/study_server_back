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
