"""HealthDoc schema v9: account security and institution comment replies.

Revision ID: 20260722_schema_v9
Revises: 20260720_schema_v8
"""

from alembic import op
import sqlalchemy as sa


revision = "20260722_schema_v9"
down_revision = "20260720_schema_v8"
branch_labels = None
depends_on = None


def upgrade():
    op.add_column(
        "users",
        sa.Column("token_version", sa.Integer(), nullable=False, server_default="0"),
    )
    op.create_table(
        "password_verification_challenges",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("public_id", sa.String(36), nullable=False, unique=True, index=True),
        sa.Column("user_id", sa.Integer(), sa.ForeignKey("users.id", ondelete="CASCADE"), nullable=False, index=True),
        sa.Column("purpose", sa.String(16), nullable=False, index=True),
        sa.Column("email_snapshot", sa.String(120), nullable=False),
        sa.Column("code_hash", sa.String(255), nullable=False),
        sa.Column("request_ip_hash", sa.String(64), nullable=True, index=True),
        sa.Column("attempt_count", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=False, index=True),
        sa.Column("consumed_at", sa.DateTime(timezone=True)),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now(), index=True),
        sa.CheckConstraint("purpose in ('reset','change')", name="ck_password_challenge_purpose"),
        sa.CheckConstraint("attempt_count between 0 and 5", name="ck_password_challenge_attempts"),
    )
    op.create_table(
        "comment_replies",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("comment_id", sa.Integer(), sa.ForeignKey("comments.id", ondelete="CASCADE"), nullable=False, index=True),
        sa.Column("institution_id", sa.Integer(), sa.ForeignKey("institutions.id", ondelete="CASCADE"), nullable=False, index=True),
        sa.Column("content", sa.Text(), nullable=False),
        sa.Column("status", sa.String(20), nullable=False, server_default="pending", index=True),
        sa.Column("review_note", sa.String(500)),
        sa.Column("submitted_by_user_id", sa.Integer(), sa.ForeignKey("users.id", ondelete="SET NULL"), index=True),
        sa.Column("reviewed_by_user_id", sa.Integer(), sa.ForeignKey("users.id", ondelete="SET NULL"), index=True),
        sa.Column("submitted_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.Column("reviewed_at", sa.DateTime(timezone=True)),
        sa.Column("user_read_at", sa.DateTime(timezone=True)),
        sa.CheckConstraint("length(trim(content)) > 0", name="ck_comment_replies_content_not_blank"),
        sa.CheckConstraint("status in ('pending', 'approved', 'rejected')", name="ck_comment_replies_status"),
        sa.UniqueConstraint("comment_id", name="uq_comment_replies_comment"),
    )


def downgrade():
    raise RuntimeError(
        "schema v9 contains security challenges and audited comment replies; "
        "restore a complete backup instead of downgrading in place"
    )
