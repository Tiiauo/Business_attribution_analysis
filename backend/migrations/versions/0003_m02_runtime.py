"""Pin in-flight logins to their auth version and retain source identities."""

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects.postgresql import JSONB

revision = "0003_m02"
down_revision = "0002_m01"
branch_labels = None
depends_on = None


def upgrade():
    op.add_column("login_attempts", sa.Column("auth_config", JSONB(), nullable=True))
    op.execute("""CREATE FUNCTION retain_data_source_identity() RETURNS trigger LANGUAGE plpgsql AS $$
        BEGIN RAISE EXCEPTION 'Data-source identity must be retained'; END $$""")
    op.execute(
        "CREATE TRIGGER retain_data_source_identity BEFORE DELETE ON data_sources FOR EACH ROW EXECUTE FUNCTION retain_data_source_identity()"
    )


def downgrade():
    op.execute("DROP TRIGGER retain_data_source_identity ON data_sources")
    op.execute("DROP FUNCTION retain_data_source_identity()")
    op.drop_column("login_attempts", "auth_config")
