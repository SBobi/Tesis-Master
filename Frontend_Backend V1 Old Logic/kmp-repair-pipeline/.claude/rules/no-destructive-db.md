# Rule: No destructive database commands from application code

Never generate or suggest running the following without explicit user confirmation:
- `DROP TABLE`
- `DROP DATABASE`
- `DELETE FROM` without a `WHERE` clause
- `TRUNCATE`
- `alembic downgrade` below the current head without a stated reason

Always use Alembic migrations for schema changes. Never alter the schema by hand in production.
