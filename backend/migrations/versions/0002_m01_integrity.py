"""Complete circular ownership link and immutable data-source identity."""

from alembic import op

revision = "0002_m01"
down_revision = "0001_m01"
branch_labels = None
depends_on = None


def upgrade():
    op.create_foreign_key(
        "fk_messages_task",
        "messages",
        "analysis_tasks",
        ["task_id"],
        ["id"],
        ondelete="CASCADE",
        deferrable=True,
        initially="DEFERRED",
    )
    op.execute("""CREATE FUNCTION protect_data_source_identity() RETURNS trigger LANGUAGE plpgsql AS $$
      BEGIN
        IF ROW(NEW.id, NEW.connection_target, NEW.database_name, NEW.schema_name)
           IS DISTINCT FROM ROW(OLD.id, OLD.connection_target, OLD.database_name, OLD.schema_name) THEN
          RAISE EXCEPTION 'Data-source identity is immutable';
        END IF;
        RETURN NEW;
      END $$""")
    op.execute("""CREATE TRIGGER immutable_data_source_identity BEFORE UPDATE ON data_sources
                  FOR EACH ROW EXECUTE FUNCTION protect_data_source_identity()""")


def downgrade():
    op.execute("DROP TRIGGER immutable_data_source_identity ON data_sources")
    op.execute("DROP FUNCTION protect_data_source_identity()")
    op.drop_constraint("fk_messages_task", "messages", type_="foreignkey")
