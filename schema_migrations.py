from sqlalchemy import inspect, text

from extensions import db


PRACTICE_PROBLEM_SET_COLUMNS = {
    'runtime_platform': 'VARCHAR(30) NULL',
    'project_type': 'VARCHAR(30) NULL',
    'source_key': 'VARCHAR(255) NULL',
    'source_revision': 'VARCHAR(64) NULL',
    "managed_by": "VARCHAR(20) NOT NULL DEFAULT 'web'",
}

PENDING_SIGNUP_COLUMNS = {
    "notification_status": "VARCHAR(20) NOT NULL DEFAULT 'not_sent'",
    "notification_sent_at": "DATETIME NULL",
}


def apply_schema_migrations():
    """Apply the small, idempotent schema additions used by this project."""
    inspector = inspect(db.engine)
    table_names = inspector.get_table_names()

    if 'pending_signups' in table_names:
        pending_columns = {
            column['name'] for column in inspector.get_columns('pending_signups')
        }
        with db.engine.begin() as connection:
            for column_name, column_definition in PENDING_SIGNUP_COLUMNS.items():
                if column_name not in pending_columns:
                    connection.execute(text(
                        f'ALTER TABLE pending_signups ADD COLUMN {column_name} {column_definition}'
                    ))

    if 'email_verifications' in table_names:
        with db.engine.begin() as connection:
            connection.execute(text('DROP TABLE email_verifications'))

    if 'practice_problem_sets' not in table_names:
        return

    existing_columns = {
        column['name'] for column in inspector.get_columns('practice_problem_sets')
    }
    with db.engine.begin() as connection:
        for column_name, column_definition in PRACTICE_PROBLEM_SET_COLUMNS.items():
            if column_name not in existing_columns:
                connection.execute(text(
                    f'ALTER TABLE practice_problem_sets ADD COLUMN {column_name} {column_definition}'
                ))
        connection_inspector = inspect(connection)
        source_key_is_unique = any(
            index.get('unique') and index.get('column_names') == ['source_key']
            for index in connection_inspector.get_indexes('practice_problem_sets')
        ) or any(
            constraint.get('column_names') == ['source_key']
            for constraint in connection_inspector.get_unique_constraints('practice_problem_sets')
        )
        if not source_key_is_unique:
            connection.execute(text(
                'CREATE UNIQUE INDEX uq_practice_problem_sets_source_key '
                'ON practice_problem_sets (source_key)'
            ))
