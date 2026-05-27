"""Phase 9 — Authentication & User Management Tables

Revision ID: 0004
Revises: 0003
Create Date: 2026-05-25

Adds:
  auth schema                — isolated namespace for all auth objects
  auth.users                 — tenant-scoped user accounts with bcrypt passwords
  auth.user_sessions         — durable refresh token audit log
  auth.password_history      — last-12-password reuse prevention
"""

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "0004"
down_revision: Union[str, None] = "0003"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # ── Create auth schema ────────────────────────────────────────────────────
    op.execute("CREATE SCHEMA IF NOT EXISTS auth")

    # ── auth.users ────────────────────────────────────────────────────────────
    op.create_table(
        "users",
        sa.Column("user_id",             postgresql.UUID(as_uuid=True), primary_key=True,
                  server_default=sa.text("gen_random_uuid()")),
        sa.Column("email",               sa.Text(),    nullable=False),
        sa.Column("full_name",           sa.Text(),    nullable=True),
        sa.Column("hashed_password",     sa.Text(),    nullable=False),
        sa.Column("role",                sa.String(30), nullable=False, server_default="analyst"),
        sa.Column("tenant_id",           postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("is_active",           sa.Boolean(), nullable=False, server_default="TRUE"),
        sa.Column("is_verified",         sa.Boolean(), nullable=False, server_default="FALSE"),
        sa.Column("force_password_reset",sa.Boolean(), nullable=False, server_default="FALSE"),
        sa.Column("last_login_at",       sa.TIMESTAMP(timezone=True), nullable=True),
        sa.Column("failed_login_count",  sa.Integer(), nullable=False, server_default="0"),
        sa.Column("locked_until",        sa.TIMESTAMP(timezone=True), nullable=True),
        sa.Column("created_at",          sa.TIMESTAMP(timezone=True), nullable=False,
                  server_default=sa.text("NOW()")),
        sa.Column("updated_at",          sa.TIMESTAMP(timezone=True), nullable=False,
                  server_default=sa.text("NOW()")),
        sa.Column("created_by",          postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("deactivated_at",      sa.TIMESTAMP(timezone=True), nullable=True),
        sa.ForeignKeyConstraint(["tenant_id"], ["ref.covered_entities.ce_id"],
                                ondelete="RESTRICT"),
        sa.UniqueConstraint("email", "tenant_id", name="uq_users_email_tenant"),
        sa.CheckConstraint(
            "role IN ('analyst','senior_analyst','auditor','admin','system')",
            name="ck_users_role",
        ),
        sa.CheckConstraint(
            "email ~* '^[^@]+@[^@]+\\.[^@]+$'",
            name="ck_users_email_format",
        ),
        schema="auth",
    )
    op.create_index("idx_users_email",      "users", ["email"],     schema="auth")
    op.create_index("idx_users_tenant_id",  "users", ["tenant_id"], schema="auth")
    op.create_index("idx_users_role",       "users", ["role"],      schema="auth")

    # ── auth.user_sessions ────────────────────────────────────────────────────
    op.create_table(
        "user_sessions",
        sa.Column("session_id",    postgresql.UUID(as_uuid=True), primary_key=True,
                  server_default=sa.text("gen_random_uuid()")),
        sa.Column("jti",           sa.String(64),  nullable=False, unique=True),
        sa.Column("user_id",       postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("tenant_id",     postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("ip_address",    postgresql.INET(), nullable=True),
        sa.Column("user_agent",    sa.Text(),      nullable=True),
        sa.Column("issued_at",     sa.TIMESTAMP(timezone=True), nullable=False,
                  server_default=sa.text("NOW()")),
        sa.Column("expires_at",    sa.TIMESTAMP(timezone=True), nullable=False),
        sa.Column("revoked_at",    sa.TIMESTAMP(timezone=True), nullable=True),
        sa.Column("revoke_reason", sa.String(50),  nullable=True),
        sa.ForeignKeyConstraint(["user_id"],   ["auth.users.user_id"]),
        sa.ForeignKeyConstraint(["tenant_id"], ["ref.covered_entities.ce_id"]),
        schema="auth",
    )
    op.create_index("idx_sessions_jti",      "user_sessions", ["jti"],     schema="auth")
    op.create_index("idx_sessions_user_id",  "user_sessions", ["user_id"], schema="auth")
    op.create_index("idx_sessions_tenant_id","user_sessions", ["tenant_id"],schema="auth")

    # ── auth.password_history ─────────────────────────────────────────────────
    op.create_table(
        "password_history",
        sa.Column("history_id",      postgresql.UUID(as_uuid=True), primary_key=True,
                  server_default=sa.text("gen_random_uuid()")),
        sa.Column("user_id",         postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("hashed_password", sa.Text(), nullable=False),
        sa.Column("set_at",          sa.TIMESTAMP(timezone=True), nullable=False,
                  server_default=sa.text("NOW()")),
        sa.ForeignKeyConstraint(["user_id"], ["auth.users.user_id"], ondelete="CASCADE"),
        schema="auth",
    )
    op.create_index("idx_pwd_history_user", "password_history",
                    ["user_id", sa.text("set_at DESC")], schema="auth")


def downgrade() -> None:
    op.drop_table("password_history", schema="auth")
    op.drop_table("user_sessions",    schema="auth")
    op.drop_table("users",            schema="auth")
    op.execute("DROP SCHEMA IF EXISTS auth CASCADE")
