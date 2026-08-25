from sqlalchemy import inspect, text

from extensions import db


PRACTICE_PROBLEM_SET_COLUMNS = {
    'runtime_platform': 'VARCHAR(30) NULL',
    'project_type': 'VARCHAR(30) NULL',
}


def apply_schema_migrations():
    """Apply the small, idempotent schema additions used by this project."""
    inspector = inspect(db.engine)
    if 'practice_problem_sets' not in inspector.get_table_names():
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
