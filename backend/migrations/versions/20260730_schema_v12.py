"""HealthDoc schema v12: acceptance workflow and linked-account upgrade.

Revision ID: 20260730_schema_v12
Revises: 20260729_schema_v11
"""

from alembic import op
import sqlalchemy as sa


revision = "20260730_schema_v12"
down_revision = "20260729_schema_v11"
branch_labels = None
depends_on = None


def _add_column_if_missing(table_name, column):
    columns = {
        item["name"]
        for item in sa.inspect(op.get_bind()).get_columns(table_name)
    }
    if column.name not in columns:
        op.add_column(table_name, column)


def _remap_friend_relation_references(
    connection,
    *,
    primary_id: int,
    duplicate_ids: list[int],
) -> None:
    """Preserve historical authorization evidence before pair deduplication.

    A normal v11 database does not yet have these v12 foreign-key columns, but
    development snapshots and cross-database imports can.  Detect them rather
    than assuming either schema shape so deleting a reverse duplicate never
    lets ``ON DELETE SET NULL`` erase the relation used for an old booking or
    waitlist participant.
    """

    if not duplicate_ids:
        return
    inspector = sa.inspect(connection)
    tables = set(inspector.get_table_names())
    for table_name in (
        "booking_participant_authorizations",
        "waitlist_subscription_participants",
    ):
        if table_name not in tables:
            continue
        columns = {
            column["name"] for column in inspector.get_columns(table_name)
        }
        if "friend_relation_id" not in columns:
            continue
        connection.execute(
            sa.text(
                f"UPDATE {table_name} "
                "SET friend_relation_id=:primary_id "
                "WHERE friend_relation_id IN :duplicate_ids"
            ).bindparams(
                sa.bindparam("duplicate_ids", expanding=True)
            ),
            {
                "primary_id": primary_id,
                "duplicate_ids": duplicate_ids,
            },
        )


def _merge_friend_pairs():
    connection = op.get_bind()
    rows = connection.execute(
        sa.text(
            "SELECT id, user_id, friend_user_id, relation_name, auth_status, "
            "booking_auth_status, booking_authorized_at, created_at "
            "FROM friend_relations ORDER BY id"
        )
    ).mappings().all()
    grouped = {}
    for row in rows:
        pair = tuple(sorted((int(row["user_id"]), int(row["friend_user_id"]))))
        grouped.setdefault(pair, []).append(row)

    for (low, high), pair_rows in grouped.items():
        primary = pair_rows[0]
        # v11 stored health-view and appointment authorization separately.
        # Either flag represented an accepted relationship; v12 promotes that
        # relationship to one bidirectional link and stops consulting the old
        # per-capability flags at runtime.
        active = any(
            bool(row["auth_status"]) or bool(row["booking_auth_status"])
            for row in pair_rows
        )
        forward_name = None
        reverse_name = None
        authorized_at = None
        for row in pair_rows:
            if (
                int(row["user_id"]) == int(primary["user_id"])
                and int(row["friend_user_id"]) == int(primary["friend_user_id"])
            ):
                forward_name = forward_name or row["relation_name"]
            else:
                reverse_name = reverse_name or row["relation_name"]
            candidate = row["booking_authorized_at"] or row["created_at"]
            if authorized_at is None or (candidate and candidate < authorized_at):
                authorized_at = candidate
        connection.execute(
            sa.text(
                "UPDATE friend_relations "
                "SET pair_key=:pair_key, relation_name=:forward_name, "
                "friend_relation_name=:reverse_name, auth_status=:active, "
                "reverse_auth_status=:active, authorization_version=0, "
                "status=:status, accepted_at=:accepted_at, revoked_at=NULL, "
                "booking_auth_status=:active, "
                "reverse_booking_auth_status=:active, "
                "booking_authorized_at=:authorized_at, "
                "reverse_booking_authorized_at=:authorized_at, "
                "booking_authorization_version=0 "
                "WHERE id=:row_id"
            ),
            {
                "pair_key": f"{low}:{high}",
                "forward_name": forward_name or "亲友",
                "reverse_name": reverse_name or "亲友",
                "active": active,
                "status": "active" if active else "pending",
                "accepted_at": authorized_at if active else None,
                "authorized_at": authorized_at if active else None,
                "row_id": primary["id"],
            },
        )
        duplicate_ids = [row["id"] for row in pair_rows[1:]]
        if duplicate_ids:
            _remap_friend_relation_references(
                connection,
                primary_id=int(primary["id"]),
                duplicate_ids=[int(row_id) for row_id in duplicate_ids],
            )
            connection.execute(
                sa.text(
                    "DELETE FROM friend_relations WHERE id IN :duplicate_ids"
                ).bindparams(
                    sa.bindparam("duplicate_ids", expanding=True)
                ),
                {"duplicate_ids": duplicate_ids},
            )


def upgrade():
    # ``identity_completed_at`` is the public v12 contract.  A few development
    # snapshots were produced while the column was temporarily named
    # ``profile_completed_at``; normalize those snapshots in place so the
    # final physical schema has one canonical column.
    user_columns = {
        column["name"]
        for column in sa.inspect(op.get_bind()).get_columns("users")
    }
    if "identity_completed_at" not in user_columns:
        if "profile_completed_at" in user_columns:
            op.execute(
                "ALTER TABLE users RENAME COLUMN profile_completed_at "
                "TO identity_completed_at"
            )
        else:
            op.add_column(
                "users",
                sa.Column("identity_completed_at", sa.DateTime(timezone=True)),
            )
    elif "profile_completed_at" in user_columns:
        op.execute(
            "UPDATE users SET identity_completed_at="
            "COALESCE(identity_completed_at, profile_completed_at)"
        )
        op.drop_column("users", "profile_completed_at")
    op.add_column(
        "users",
        sa.Column(
            "allow_health_id_proxy_booking",
            sa.Boolean(),
            nullable=False,
            server_default=sa.true(),
        ),
    )
    op.add_column(
        "users",
        sa.Column(
            "booking_authorization_version",
            sa.Integer(),
            nullable=False,
            server_default="0",
        ),
    )
    op.add_column(
        "users",
        sa.Column(
            "must_change_initial_password",
            sa.Boolean(),
            nullable=False,
            server_default=sa.false(),
        ),
    )
    op.execute(
        """
        UPDATE users
        SET identity_completed_at = COALESCE(created_at, CURRENT_TIMESTAMP)
        WHERE role='user'
          AND real_name IS NOT NULL
          AND length(trim(real_name)) > 0
          AND birth_date IS NOT NULL
          AND gender IN ('male','female','other','undisclosed')
        """
    )
    op.add_column(
        "notification_outbox",
        sa.Column(
            "sensitive_payload_cleared_at",
            sa.DateTime(timezone=True),
        ),
    )
    _add_column_if_missing(
        "password_verification_challenges",
        sa.Column(
            "token_version_snapshot",
            sa.Integer(),
            nullable=False,
            server_default="0",
        ),
    )
    # Challenges issued by a pre-v12 runtime have no trustworthy account
    # security epoch. Retain them for audit/rate-limit history but make every
    # unconsumed code terminally unusable during the upgrade.
    op.execute(
        "UPDATE password_verification_challenges "
        "SET token_version_snapshot=COALESCE(("
        "SELECT users.token_version FROM users "
        "WHERE users.id=password_verification_challenges.user_id"
        "), 0), consumed_at=COALESCE(consumed_at, CURRENT_TIMESTAMP)"
    )
    _add_column_if_missing(
        "oauth_clients",
        sa.Column(
            "approval_version",
            sa.Integer(),
            nullable=False,
            server_default="0",
        ),
    )
    for table_name in (
        "oauth_authorization_codes",
        "oauth_access_tokens",
        "oauth_refresh_tokens",
    ):
        _add_column_if_missing(
            table_name,
            sa.Column(
                "user_token_version_snapshot",
                sa.Integer(),
                nullable=False,
                server_default="0",
            ),
        )
        _add_column_if_missing(
            table_name,
            sa.Column(
                "client_approval_version_snapshot",
                sa.Integer(),
                nullable=False,
                server_default="0",
            ),
        )
    # Pre-v12 OAuth grants have no trustworthy user/client epoch. Preserve
    # the rows for audit, but make every outstanding credential terminal.
    op.execute(
        "UPDATE oauth_authorization_codes "
        "SET consumed_at=COALESCE(consumed_at, CURRENT_TIMESTAMP)"
    )
    op.execute(
        "UPDATE oauth_access_tokens "
        "SET revoked_at=COALESCE(revoked_at, CURRENT_TIMESTAMP)"
    )
    op.execute(
        "UPDATE oauth_refresh_tokens "
        "SET revoked_at=COALESCE(revoked_at, CURRENT_TIMESTAMP)"
    )

    # A branch has exactly one live institution account. Historical duplicate
    # rows are disabled and detached before the database constraint is added.
    op.drop_constraint(
        "ck_users_role_institution_binding",
        "users",
        type_="check",
    )
    op.create_check_constraint(
        "ck_users_role_institution_binding",
        "users",
        "(role = 'institution_admin' and "
        "(managed_institution_id is not null or is_active = false)) "
        "or (role in ('user', 'admin') and managed_institution_id is null)",
    )
    op.execute(
        """
        UPDATE users
        SET managed_institution_id = NULL,
            is_active = FALSE,
            token_version = token_version + 1
        WHERE id IN (
            SELECT id
            FROM (
                SELECT id,
                       ROW_NUMBER() OVER (
                           PARTITION BY managed_institution_id
                           ORDER BY CASE WHEN is_active THEN 0 ELSE 1 END, id
                       ) AS account_position
                FROM users
                WHERE managed_institution_id IS NOT NULL
            ) AS ranked_accounts
            WHERE account_position > 1
        )
        """
    )
    op.create_unique_constraint(
        "uq_users_managed_institution",
        "users",
        ["managed_institution_id"],
    )

    op.add_column(
        "friend_relations",
        sa.Column("pair_key", sa.String(50)),
    )
    op.add_column(
        "friend_relations",
        sa.Column("friend_relation_name", sa.String(80)),
    )
    op.add_column(
        "friend_relations",
        sa.Column(
            "status",
            sa.String(20),
            nullable=False,
            server_default="pending",
        ),
    )
    op.add_column(
        "friend_relations",
        sa.Column(
            "reverse_auth_status",
            sa.Boolean(),
            nullable=False,
            server_default=sa.false(),
        ),
    )
    op.add_column(
        "friend_relations",
        sa.Column(
            "authorization_version",
            sa.Integer(),
            nullable=False,
            server_default="0",
        ),
    )
    op.add_column(
        "friend_relations",
        sa.Column(
            "reverse_booking_auth_status",
            sa.Boolean(),
            nullable=False,
            server_default=sa.false(),
        ),
    )
    op.add_column(
        "friend_relations",
        sa.Column(
            "reverse_booking_authorized_at",
            sa.DateTime(timezone=True),
        ),
    )
    op.add_column(
        "friend_relations",
        sa.Column(
            "booking_authorization_version",
            sa.Integer(),
            nullable=False,
            server_default="0",
        ),
    )
    op.add_column(
        "friend_relations",
        sa.Column("accepted_at", sa.DateTime(timezone=True)),
    )
    op.add_column(
        "friend_relations",
        sa.Column("revoked_at", sa.DateTime(timezone=True)),
    )
    op.create_check_constraint(
        "ck_friend_reverse_relation_name_not_blank",
        "friend_relations",
        "friend_relation_name is null or "
        "length(trim(friend_relation_name)) > 0",
    )
    op.create_check_constraint(
        "ck_friend_relations_status",
        "friend_relations",
        "status in ('pending','active','revoked')",
    )
    _merge_friend_pairs()
    op.create_unique_constraint(
        "uq_friend_relations_pair_key",
        "friend_relations",
        ["pair_key"],
    )
    op.create_index(
        "ix_friend_relations_pair_key",
        "friend_relations",
        ["pair_key"],
    )
    op.create_index(
        "ix_friend_relations_status",
        "friend_relations",
        ["status"],
    )

    op.create_table(
        "delegation_session_audits",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column(
            "actor_user_id",
            sa.Integer(),
            sa.ForeignKey("users.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column(
            "subject_user_id",
            sa.Integer(),
            sa.ForeignKey("users.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column(
            "parent_session_id",
            sa.String(36),
            sa.ForeignKey("delegation_session_audits.id", ondelete="SET NULL"),
        ),
        sa.Column("chain_user_ids", sa.JSON(), nullable=False),
        sa.Column("relation_chain", sa.JSON(), nullable=False),
        sa.Column("token_version_snapshot", sa.JSON(), nullable=False),
        sa.Column("depth", sa.Integer(), nullable=False),
        sa.Column("status", sa.String(20), nullable=False, server_default="active"),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.func.now(),
        ),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("ended_at", sa.DateTime(timezone=True)),
        sa.Column("end_reason", sa.String(120)),
        sa.CheckConstraint(
            "status in ('active','exited','revoked','expired')",
            name="ck_delegation_session_status",
        ),
        sa.CheckConstraint(
            "depth between 1 and 3",
            name="ck_delegation_session_depth",
        ),
    )
    op.create_index(
        "ix_delegation_session_audits_actor_user_id",
        "delegation_session_audits",
        ["actor_user_id"],
    )
    op.create_index(
        "ix_delegation_session_audits_subject_user_id",
        "delegation_session_audits",
        ["subject_user_id"],
    )
    op.create_index(
        "ix_delegation_session_audits_status",
        "delegation_session_audits",
        ["status"],
    )
    op.create_index(
        "ix_delegation_session_audits_expires_at",
        "delegation_session_audits",
        ["expires_at"],
    )
    op.create_table(
        "delegated_action_audits",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column(
            "session_id",
            sa.String(36),
            sa.ForeignKey("delegation_session_audits.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("actor_user_id", sa.Integer(), nullable=False),
        sa.Column("subject_user_id", sa.Integer(), nullable=False),
        sa.Column("chain_user_ids", sa.JSON(), nullable=False),
        sa.Column("method", sa.String(10), nullable=False),
        sa.Column("path", sa.String(255), nullable=False),
        sa.Column("action", sa.String(120), nullable=False),
        sa.Column("outcome", sa.String(20), nullable=False),
        sa.Column("status_code", sa.Integer(), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.func.now(),
        ),
        sa.CheckConstraint(
            "outcome in ('success','denied','error')",
            name="ck_delegated_action_outcome",
        ),
    )
    op.create_index(
        "ix_delegated_action_audits_session_id",
        "delegated_action_audits",
        ["session_id"],
    )
    op.create_index(
        "ix_delegated_action_audits_actor_user_id",
        "delegated_action_audits",
        ["actor_user_id"],
    )
    op.create_index(
        "ix_delegated_action_audits_subject_user_id",
        "delegated_action_audits",
        ["subject_user_id"],
    )
    op.create_index(
        "ix_delegated_action_audits_created_at",
        "delegated_action_audits",
        ["created_at"],
    )

    op.create_table(
        "booking_participant_tokens",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("token_hash", sa.String(64), nullable=False),
        sa.Column(
            "booker_user_id",
            sa.Integer(),
            sa.ForeignKey("users.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column(
            "subject_user_id",
            sa.Integer(),
            sa.ForeignKey("users.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("authorization_version", sa.Integer(), nullable=False),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("consumed_at", sa.DateTime(timezone=True)),
        sa.Column("revoked_at", sa.DateTime(timezone=True)),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.func.now(),
        ),
        sa.UniqueConstraint(
            "token_hash",
            name="uq_booking_participant_tokens_hash",
        ),
        sa.CheckConstraint(
            "booker_user_id <> subject_user_id",
            name="ck_booking_participant_token_not_self",
        ),
    )
    op.create_index(
        "ix_booking_participant_tokens_token_hash",
        "booking_participant_tokens",
        ["token_hash"],
    )
    op.create_index(
        "ix_booking_participant_tokens_booker_user_id",
        "booking_participant_tokens",
        ["booker_user_id"],
    )
    op.create_index(
        "ix_booking_participant_tokens_subject_user_id",
        "booking_participant_tokens",
        ["subject_user_id"],
    )
    op.create_index(
        "ix_booking_participant_tokens_expires_at",
        "booking_participant_tokens",
        ["expires_at"],
    )

    op.create_table(
        "booking_participant_authorizations",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column(
            "appointment_id",
            sa.Integer(),
            sa.ForeignKey("appointments.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column(
            "booker_user_id",
            sa.Integer(),
            sa.ForeignKey("users.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column(
            "subject_user_id",
            sa.Integer(),
            sa.ForeignKey("users.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("participant_type", sa.String(20), nullable=False),
        sa.Column(
            "friend_relation_id",
            sa.Integer(),
            sa.ForeignKey("friend_relations.id", ondelete="SET NULL"),
        ),
        sa.Column(
            "authorization_version",
            sa.Integer(),
            nullable=False,
            server_default="0",
        ),
        sa.Column(
            "participant_token_id",
            sa.Integer(),
            sa.ForeignKey("booking_participant_tokens.id", ondelete="SET NULL"),
        ),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.func.now(),
        ),
        sa.UniqueConstraint(
            "appointment_id",
            name="uq_booking_participant_authorization_appointment",
        ),
        sa.CheckConstraint(
            "participant_type in "
            "('self','linked_account','health_code_token')",
            name="ck_booking_participant_authorization_type",
        ),
    )
    op.create_index(
        "ix_booking_participant_authorizations_appointment_id",
        "booking_participant_authorizations",
        ["appointment_id"],
    )
    op.create_index(
        "ix_booking_participant_authorizations_booker_user_id",
        "booking_participant_authorizations",
        ["booker_user_id"],
    )
    op.create_index(
        "ix_booking_participant_authorizations_subject_user_id",
        "booking_participant_authorizations",
        ["subject_user_id"],
    )
    op.execute(
        """
        INSERT INTO booking_participant_authorizations (
            appointment_id,
            booker_user_id,
            subject_user_id,
            participant_type,
            friend_relation_id,
            authorization_version,
            participant_token_id,
            created_at
        )
        SELECT appointment.id,
               COALESCE(appointment.booked_by_user_id, appointment.user_id),
               appointment.user_id,
               CASE
                   WHEN COALESCE(
                       appointment.booked_by_user_id,
                       appointment.user_id
                   ) = appointment.user_id THEN 'self'
                   ELSE 'linked_account'
               END,
               relation.id,
               COALESCE(relation.booking_authorization_version, 0),
               NULL,
               appointment.created_at
        FROM appointments AS appointment
        LEFT JOIN friend_relations AS relation
          ON relation.pair_key = CASE
              WHEN COALESCE(
                  appointment.booked_by_user_id,
                  appointment.user_id
              ) < appointment.user_id
              THEN CAST(COALESCE(
                       appointment.booked_by_user_id,
                       appointment.user_id
                   ) AS VARCHAR)
                   || ':' || CAST(appointment.user_id AS VARCHAR)
              ELSE CAST(appointment.user_id AS VARCHAR)
                   || ':' || CAST(COALESCE(
                       appointment.booked_by_user_id,
                       appointment.user_id
                   ) AS VARCHAR)
          END
        """
    )

    op.add_column(
        "waitlist_subscription_participants",
        sa.Column(
            "participant_type",
            sa.String(20),
            nullable=False,
            server_default="linked_account",
        ),
    )
    op.add_column(
        "waitlist_subscription_participants",
        sa.Column("friend_relation_id", sa.Integer()),
    )
    op.add_column(
        "waitlist_subscription_participants",
        sa.Column(
            "authorization_version",
            sa.Integer(),
            nullable=False,
            server_default="0",
        ),
    )
    op.execute(
        """
        UPDATE waitlist_subscription_participants AS participant
        SET participant_type = CASE WHEN participant.subject_user_id = (
                SELECT subscription.subscriber_user_id
                FROM waitlist_subscriptions AS subscription
                WHERE subscription.id = participant.subscription_id
            ) THEN 'self' ELSE 'linked_account' END,
            friend_relation_id = (
                SELECT relation.id
                FROM friend_relations AS relation
                JOIN waitlist_subscriptions AS subscription
                  ON subscription.id = participant.subscription_id
                WHERE relation.pair_key = CASE
                    WHEN subscription.subscriber_user_id
                         < participant.subject_user_id
                    THEN CAST(subscription.subscriber_user_id AS VARCHAR)
                         || ':' ||
                         CAST(participant.subject_user_id AS VARCHAR)
                    ELSE CAST(participant.subject_user_id AS VARCHAR)
                         || ':' ||
                         CAST(subscription.subscriber_user_id AS VARCHAR)
                END
                ORDER BY relation.id
                LIMIT 1
            ),
            authorization_version = COALESCE((
                SELECT relation.booking_authorization_version
                FROM friend_relations AS relation
                JOIN waitlist_subscriptions AS subscription
                  ON subscription.id = participant.subscription_id
                WHERE relation.pair_key = CASE
                    WHEN subscription.subscriber_user_id
                         < participant.subject_user_id
                    THEN CAST(subscription.subscriber_user_id AS VARCHAR)
                         || ':' ||
                         CAST(participant.subject_user_id AS VARCHAR)
                    ELSE CAST(participant.subject_user_id AS VARCHAR)
                         || ':' ||
                         CAST(subscription.subscriber_user_id AS VARCHAR)
                END
                ORDER BY relation.id
                LIMIT 1
            ), 0)
        """
    )
    op.create_foreign_key(
        "fk_waitlist_participants_friend_relation",
        "waitlist_subscription_participants",
        "friend_relations",
        ["friend_relation_id"],
        ["id"],
        ondelete="SET NULL",
    )
    op.create_check_constraint(
        "ck_waitlist_subscription_participants_type",
        "waitlist_subscription_participants",
        "participant_type in "
        "('self','linked_account','health_code_token')",
    )

    op.add_column(
        "institutions",
        sa.Column("account_deactivated_at", sa.DateTime(timezone=True)),
    )

    op.drop_constraint(
        "ck_institution_reports_status",
        "institution_reports",
        type_="check",
    )
    op.execute(
        "UPDATE institution_reports SET status='pending_review' WHERE status='locked'"
    )
    op.create_check_constraint(
        "ck_institution_reports_status",
        "institution_reports",
        "status in ('draft', 'pending_review', 'published')",
    )
    op.add_column(
        "institution_reports",
        sa.Column("upload_doctor_name", sa.String(80)),
    )
    op.add_column(
        "institution_reports",
        sa.Column("review_doctor_name", sa.String(80)),
    )
    op.add_column(
        "institution_reports",
        sa.Column("submitted_for_review_at", sa.DateTime(timezone=True)),
    )
    op.add_column(
        "institution_reports",
        sa.Column("reviewed_by_user_id", sa.Integer()),
    )
    op.add_column(
        "institution_reports",
        sa.Column("reviewed_by_username_snapshot", sa.String(80)),
    )
    op.add_column(
        "institution_reports",
        sa.Column("reviewed_at", sa.DateTime(timezone=True)),
    )
    op.create_foreign_key(
        "fk_institution_reports_reviewed_by_user",
        "institution_reports",
        "users",
        ["reviewed_by_user_id"],
        ["id"],
        ondelete="SET NULL",
    )
    op.create_index(
        "ix_institution_reports_reviewed_by_user_id",
        "institution_reports",
        ["reviewed_by_user_id"],
    )
    # Preserve truthful history without inventing doctor names.  Older
    # reports did not capture those names, so the nullable columns stay NULL
    # and the read model renders the explicit "历史报告未记录" fallback.
    op.execute(
        """
        UPDATE institution_reports
        SET submitted_for_review_at = COALESCE(locked_at, created_at)
        WHERE status IN ('pending_review', 'published')
          AND submitted_for_review_at IS NULL
        """
    )
    op.execute(
        """
        UPDATE institution_reports
        SET reviewed_at = COALESCE(published_at, submitted_at, locked_at, created_at)
        WHERE status = 'published'
          AND reviewed_at IS NULL
        """
    )

    op.add_column("comments", sa.Column("hidden_reason", sa.String(500)))
    op.add_column("comments", sa.Column("moderated_by_user_id", sa.Integer()))
    op.add_column(
        "comments",
        sa.Column("moderated_at", sa.DateTime(timezone=True)),
    )
    op.create_foreign_key(
        "fk_comments_moderated_by_user",
        "comments",
        "users",
        ["moderated_by_user_id"],
        ["id"],
        ondelete="SET NULL",
    )

    op.create_table(
        "appointment_complaints",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column(
            "appointment_id",
            sa.Integer(),
            sa.ForeignKey("appointments.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column(
            "institution_id",
            sa.Integer(),
            sa.ForeignKey("institutions.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column(
            "complainant_user_id",
            sa.Integer(),
            sa.ForeignKey("users.id", ondelete="SET NULL"),
        ),
        sa.Column("complainant_username_snapshot", sa.String(80), nullable=False),
        sa.Column("category", sa.String(50), nullable=False, server_default="service"),
        sa.Column("content", sa.Text(), nullable=False),
        sa.Column(
            "status",
            sa.String(40),
            nullable=False,
            server_default="institution_pending",
        ),
        sa.Column("institution_reply", sa.Text()),
        sa.Column(
            "institution_replied_by_user_id",
            sa.Integer(),
            sa.ForeignKey("users.id", ondelete="SET NULL"),
        ),
        sa.Column("institution_replied_at", sa.DateTime(timezone=True)),
        sa.Column("escalation_reason", sa.Text()),
        sa.Column("escalated_at", sa.DateTime(timezone=True)),
        sa.Column("admin_reply", sa.Text()),
        sa.Column(
            "handled_by_admin_id",
            sa.Integer(),
            sa.ForeignKey("users.id", ondelete="SET NULL"),
        ),
        sa.Column("handled_at", sa.DateTime(timezone=True)),
        sa.Column("resolved_at", sa.DateTime(timezone=True)),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.func.now(),
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.func.now(),
        ),
        sa.UniqueConstraint(
            "appointment_id",
            name="uq_appointment_complaints_appointment",
        ),
        sa.CheckConstraint(
            "status in "
            "('institution_pending','user_confirmation','platform_pending',"
            "'platform_processing','resolved')",
            name="ck_appointment_complaints_status",
        ),
        sa.CheckConstraint(
            "length(trim(content)) > 0",
            name="ck_appointment_complaints_content",
        ),
    )
    op.create_index(
        "ix_appointment_complaints_appointment_id",
        "appointment_complaints",
        ["appointment_id"],
    )
    op.create_index(
        "ix_appointment_complaints_institution_id",
        "appointment_complaints",
        ["institution_id"],
    )
    op.create_index(
        "ix_appointment_complaints_complainant_user_id",
        "appointment_complaints",
        ["complainant_user_id"],
    )
    op.create_index(
        "ix_appointment_complaints_status",
        "appointment_complaints",
        ["status"],
    )

    op.create_table(
        "complaint_events",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column(
            "complaint_id",
            sa.Integer(),
            sa.ForeignKey("appointment_complaints.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("event_type", sa.String(40), nullable=False),
        sa.Column(
            "actor_user_id",
            sa.Integer(),
            sa.ForeignKey("users.id", ondelete="SET NULL"),
        ),
        sa.Column("actor_role", sa.String(30), nullable=False),
        sa.Column("content", sa.Text()),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.func.now(),
        ),
        sa.CheckConstraint(
            "event_type in "
            "('created','institution_replied','user_confirmed','escalated',"
            "'admin_started','admin_replied','admin_resolved')",
            name="ck_complaint_events_type",
        ),
    )
    op.create_index(
        "ix_complaint_events_complaint_id",
        "complaint_events",
        ["complaint_id"],
    )
    op.create_index(
        "ix_complaint_events_event_type",
        "complaint_events",
        ["event_type"],
    )

    op.create_table(
        "complaint_messages",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column(
            "complaint_id",
            sa.Integer(),
            sa.ForeignKey("appointment_complaints.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column(
            "sender_user_id",
            sa.Integer(),
            sa.ForeignKey("users.id", ondelete="SET NULL"),
        ),
        sa.Column("sender_role", sa.String(30), nullable=False),
        sa.Column("content", sa.Text(), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.func.now(),
        ),
        sa.CheckConstraint(
            "sender_role in ('user','institution_admin','admin')",
            name="ck_complaint_messages_sender_role",
        ),
        sa.CheckConstraint(
            "length(trim(content)) > 0",
            name="ck_complaint_messages_content",
        ),
    )
    op.create_index(
        "ix_complaint_messages_complaint_id",
        "complaint_messages",
        ["complaint_id"],
    )
    op.create_index(
        "ix_complaint_messages_sender_user_id",
        "complaint_messages",
        ["sender_user_id"],
    )
    op.create_index(
        "ix_complaint_messages_sender_role",
        "complaint_messages",
        ["sender_role"],
    )

    op.create_table(
        "comment_sanctions",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column(
            "user_id",
            sa.Integer(),
            sa.ForeignKey("users.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column(
            "source_comment_id",
            sa.Integer(),
            sa.ForeignKey("comments.id", ondelete="SET NULL"),
        ),
        sa.Column("reason", sa.String(500), nullable=False),
        sa.Column("duration_days", sa.Integer()),
        sa.Column("status", sa.String(20), nullable=False, server_default="active"),
        sa.Column(
            "starts_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.func.now(),
        ),
        sa.Column("expires_at", sa.DateTime(timezone=True)),
        sa.Column(
            "created_by_admin_id",
            sa.Integer(),
            sa.ForeignKey("users.id", ondelete="SET NULL"),
        ),
        sa.Column(
            "lifted_by_admin_id",
            sa.Integer(),
            sa.ForeignKey("users.id", ondelete="SET NULL"),
        ),
        sa.Column("lifted_at", sa.DateTime(timezone=True)),
        sa.Column("lift_reason", sa.String(500)),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.func.now(),
        ),
        sa.CheckConstraint(
            "status in ('active','lifted','expired')",
            name="ck_comment_sanctions_status",
        ),
        sa.CheckConstraint(
            "duration_days is null or duration_days in (7,30)",
            name="ck_comment_sanctions_duration",
        ),
        sa.CheckConstraint(
            "length(trim(reason)) > 0",
            name="ck_comment_sanctions_reason",
        ),
    )
    op.create_index(
        "ix_comment_sanctions_user_id",
        "comment_sanctions",
        ["user_id"],
    )
    op.create_index(
        "ix_comment_sanctions_source_comment_id",
        "comment_sanctions",
        ["source_comment_id"],
    )
    op.create_index(
        "ix_comment_sanctions_status",
        "comment_sanctions",
        ["status"],
    )

    op.create_table(
        "comment_appeals",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column(
            "sanction_id",
            sa.Integer(),
            sa.ForeignKey("comment_sanctions.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column(
            "user_id",
            sa.Integer(),
            sa.ForeignKey("users.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("content", sa.Text(), nullable=False),
        sa.Column("status", sa.String(20), nullable=False, server_default="pending"),
        sa.Column("review_note", sa.String(500)),
        sa.Column(
            "reviewed_by_admin_id",
            sa.Integer(),
            sa.ForeignKey("users.id", ondelete="SET NULL"),
        ),
        sa.Column(
            "submitted_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.func.now(),
        ),
        sa.Column("reviewed_at", sa.DateTime(timezone=True)),
        sa.UniqueConstraint("sanction_id", name="uq_comment_appeals_sanction"),
        sa.CheckConstraint(
            "status in ('pending','approved','rejected')",
            name="ck_comment_appeals_status",
        ),
        sa.CheckConstraint(
            "length(trim(content)) > 0",
            name="ck_comment_appeals_content",
        ),
    )
    op.create_index(
        "ix_comment_appeals_sanction_id",
        "comment_appeals",
        ["sanction_id"],
    )
    op.create_index(
        "ix_comment_appeals_user_id",
        "comment_appeals",
        ["user_id"],
    )
    op.create_index(
        "ix_comment_appeals_status",
        "comment_appeals",
        ["status"],
    )

    op.create_table(
        "institution_audience_insight_cache",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("scope_type", sa.String(20), nullable=False),
        sa.Column("scope_id", sa.Integer(), nullable=False),
        sa.Column("period_key", sa.String(30), nullable=False),
        sa.Column("data_digest", sa.String(64), nullable=False),
        sa.Column("aggregate_payload", sa.JSON(), nullable=False),
        sa.Column("analysis_text", sa.Text(), nullable=False),
        sa.Column("model_name", sa.String(100)),
        sa.Column(
            "source",
            sa.String(30),
            nullable=False,
            server_default="deterministic",
        ),
        sa.Column(
            "generated_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.func.now(),
        ),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=False),
        sa.UniqueConstraint(
            "scope_type",
            "scope_id",
            "period_key",
            name="uq_institution_audience_cache_scope",
        ),
        sa.CheckConstraint(
            "scope_type in ('branch','organization')",
            name="ck_institution_audience_cache_scope",
        ),
    )
    op.create_index(
        "ix_institution_audience_insight_cache_expires_at",
        "institution_audience_insight_cache",
        ["expires_at"],
    )


def downgrade():
    op.drop_index(
        "ix_institution_audience_insight_cache_expires_at",
        table_name="institution_audience_insight_cache",
    )
    op.drop_table("institution_audience_insight_cache")

    op.drop_index("ix_comment_appeals_status", table_name="comment_appeals")
    op.drop_index("ix_comment_appeals_user_id", table_name="comment_appeals")
    op.drop_index("ix_comment_appeals_sanction_id", table_name="comment_appeals")
    op.drop_table("comment_appeals")
    op.drop_index("ix_comment_sanctions_status", table_name="comment_sanctions")
    op.drop_index(
        "ix_comment_sanctions_source_comment_id",
        table_name="comment_sanctions",
    )
    op.drop_index("ix_comment_sanctions_user_id", table_name="comment_sanctions")
    op.drop_table("comment_sanctions")

    op.drop_index(
        "ix_complaint_messages_sender_role",
        table_name="complaint_messages",
    )
    op.drop_index(
        "ix_complaint_messages_sender_user_id",
        table_name="complaint_messages",
    )
    op.drop_index(
        "ix_complaint_messages_complaint_id",
        table_name="complaint_messages",
    )
    op.drop_table("complaint_messages")
    op.drop_index("ix_complaint_events_event_type", table_name="complaint_events")
    op.drop_index("ix_complaint_events_complaint_id", table_name="complaint_events")
    op.drop_table("complaint_events")
    op.drop_index(
        "ix_appointment_complaints_status",
        table_name="appointment_complaints",
    )
    op.drop_index(
        "ix_appointment_complaints_complainant_user_id",
        table_name="appointment_complaints",
    )
    op.drop_index(
        "ix_appointment_complaints_institution_id",
        table_name="appointment_complaints",
    )
    op.drop_index(
        "ix_appointment_complaints_appointment_id",
        table_name="appointment_complaints",
    )
    op.drop_table("appointment_complaints")

    op.drop_constraint(
        "fk_comments_moderated_by_user",
        "comments",
        type_="foreignkey",
    )
    op.drop_column("comments", "moderated_at")
    op.drop_column("comments", "moderated_by_user_id")
    op.drop_column("comments", "hidden_reason")

    op.drop_index(
        "ix_institution_reports_reviewed_by_user_id",
        table_name="institution_reports",
    )
    op.drop_constraint(
        "fk_institution_reports_reviewed_by_user",
        "institution_reports",
        type_="foreignkey",
    )
    op.drop_column("institution_reports", "reviewed_at")
    op.drop_column("institution_reports", "reviewed_by_username_snapshot")
    op.drop_column("institution_reports", "reviewed_by_user_id")
    op.drop_column("institution_reports", "submitted_for_review_at")
    op.drop_column("institution_reports", "review_doctor_name")
    op.drop_column("institution_reports", "upload_doctor_name")
    op.drop_constraint(
        "ck_institution_reports_status",
        "institution_reports",
        type_="check",
    )
    op.execute(
        "UPDATE institution_reports SET status='locked' WHERE status='pending_review'"
    )
    op.create_check_constraint(
        "ck_institution_reports_status",
        "institution_reports",
        "status in ('draft', 'locked', 'published')",
    )

    op.drop_column("institutions", "account_deactivated_at")

    op.drop_constraint(
        "ck_waitlist_subscription_participants_type",
        "waitlist_subscription_participants",
        type_="check",
    )
    op.drop_constraint(
        "fk_waitlist_participants_friend_relation",
        "waitlist_subscription_participants",
        type_="foreignkey",
    )
    op.drop_column(
        "waitlist_subscription_participants",
        "authorization_version",
    )
    op.drop_column(
        "waitlist_subscription_participants",
        "friend_relation_id",
    )
    op.drop_column(
        "waitlist_subscription_participants",
        "participant_type",
    )

    op.drop_index(
        "ix_booking_participant_authorizations_subject_user_id",
        table_name="booking_participant_authorizations",
    )
    op.drop_index(
        "ix_booking_participant_authorizations_booker_user_id",
        table_name="booking_participant_authorizations",
    )
    op.drop_index(
        "ix_booking_participant_authorizations_appointment_id",
        table_name="booking_participant_authorizations",
    )
    op.drop_table("booking_participant_authorizations")

    op.drop_index(
        "ix_booking_participant_tokens_expires_at",
        table_name="booking_participant_tokens",
    )
    op.drop_index(
        "ix_booking_participant_tokens_subject_user_id",
        table_name="booking_participant_tokens",
    )
    op.drop_index(
        "ix_booking_participant_tokens_booker_user_id",
        table_name="booking_participant_tokens",
    )
    op.drop_index(
        "ix_booking_participant_tokens_token_hash",
        table_name="booking_participant_tokens",
    )
    op.drop_table("booking_participant_tokens")

    op.drop_index(
        "ix_delegated_action_audits_created_at",
        table_name="delegated_action_audits",
    )
    op.drop_index(
        "ix_delegated_action_audits_subject_user_id",
        table_name="delegated_action_audits",
    )
    op.drop_index(
        "ix_delegated_action_audits_actor_user_id",
        table_name="delegated_action_audits",
    )
    op.drop_index(
        "ix_delegated_action_audits_session_id",
        table_name="delegated_action_audits",
    )
    op.drop_table("delegated_action_audits")

    op.drop_index(
        "ix_delegation_session_audits_expires_at",
        table_name="delegation_session_audits",
    )
    op.drop_index(
        "ix_delegation_session_audits_status",
        table_name="delegation_session_audits",
    )
    op.drop_index(
        "ix_delegation_session_audits_subject_user_id",
        table_name="delegation_session_audits",
    )
    op.drop_index(
        "ix_delegation_session_audits_actor_user_id",
        table_name="delegation_session_audits",
    )
    op.drop_table("delegation_session_audits")

    op.drop_index(
        "ix_friend_relations_status",
        table_name="friend_relations",
    )
    op.drop_index(
        "ix_friend_relations_pair_key",
        table_name="friend_relations",
    )
    op.drop_constraint(
        "uq_friend_relations_pair_key",
        "friend_relations",
        type_="unique",
    )
    op.drop_constraint(
        "ck_friend_reverse_relation_name_not_blank",
        "friend_relations",
        type_="check",
    )
    op.drop_constraint(
        "ck_friend_relations_status",
        "friend_relations",
        type_="check",
    )
    op.drop_column("friend_relations", "revoked_at")
    op.drop_column("friend_relations", "accepted_at")
    op.drop_column("friend_relations", "booking_authorization_version")
    op.drop_column("friend_relations", "reverse_booking_authorized_at")
    op.drop_column("friend_relations", "reverse_booking_auth_status")
    op.drop_column("friend_relations", "authorization_version")
    op.drop_column("friend_relations", "reverse_auth_status")
    op.drop_column("friend_relations", "status")
    op.drop_column("friend_relations", "friend_relation_name")
    op.drop_column("friend_relations", "pair_key")

    op.drop_constraint(
        "uq_users_managed_institution",
        "users",
        type_="unique",
    )
    op.drop_constraint(
        "ck_users_role_institution_binding",
        "users",
        type_="check",
    )
    op.create_check_constraint(
        "ck_users_role_institution_binding",
        "users",
        "(role = 'institution_admin' and managed_institution_id is not null) "
        "or (role in ('user', 'admin') and managed_institution_id is null)",
    )
    op.drop_column(
        "password_verification_challenges",
        "token_version_snapshot",
    )
    op.drop_column(
        "oauth_refresh_tokens",
        "client_approval_version_snapshot",
    )
    op.drop_column(
        "oauth_refresh_tokens",
        "user_token_version_snapshot",
    )
    op.drop_column(
        "oauth_access_tokens",
        "client_approval_version_snapshot",
    )
    op.drop_column(
        "oauth_access_tokens",
        "user_token_version_snapshot",
    )
    op.drop_column(
        "oauth_authorization_codes",
        "client_approval_version_snapshot",
    )
    op.drop_column(
        "oauth_authorization_codes",
        "user_token_version_snapshot",
    )
    op.drop_column("oauth_clients", "approval_version")
    op.drop_column("notification_outbox", "sensitive_payload_cleared_at")
    op.drop_column("users", "must_change_initial_password")
    op.drop_column("users", "booking_authorization_version")
    op.drop_column("users", "allow_health_id_proxy_booking")
    op.drop_column("users", "identity_completed_at")
