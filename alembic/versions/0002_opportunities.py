"""Opportunity schema and independent source groups.

Revision ID: 0002
Revises: 0001
"""

import sqlalchemy as sa
from pgvector.sqlalchemy import Vector

from alembic import op

revision = "0002"
down_revision = "0001"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("sources", sa.Column("source_group_key", sa.String(180), nullable=True))
    # Frozen mapping includes current sources and legacy platform collectors.
    op.execute("""
        UPDATE sources SET source_group_key = CASE
            WHEN name = 'Hacker News / Ask HN' THEN 'hackernews'
            WHEN name = 'Home Assistant / Feature Requests' THEN 'homeassistant'
            WHEN name LIKE 'Lemmy / %' THEN 'lemmy'
            WHEN name LIKE 'Stack Exchange / %' THEN 'stackexchange'
            WHEN name = 'TrueNAS Community' THEN 'truenas'
            WHEN name = 'Nextcloud Community' THEN 'nextcloud'
            WHEN name = 'Proxmox Support Forum' THEN 'proxmox'
            WHEN kind IN ('lemmy', 'hackernews', 'stackexchange', 'reddit', 'youtube', 'appstore')
                THEN kind
            ELSE COALESCE(NULLIF(regexp_replace(lower(substring(
                COALESCE(config->>'feed_url', config->>'base_url', config->>'url')
                FROM '^https?://([^/:]+)'
            )), '^www\\.', ''), ''), kind)
        END
    """)
    op.execute("""
        UPDATE sources SET source_group_key = CASE source_group_key
            WHEN 'community.home-assistant.io' THEN 'homeassistant'
            WHEN 'forums.truenas.com' THEN 'truenas'
            WHEN 'help.nextcloud.com' THEN 'nextcloud'
            WHEN 'forum.proxmox.com' THEN 'proxmox'
            ELSE source_group_key END
    """)
    op.alter_column("sources", "source_group_key", nullable=False)
    op.create_table(
        "opportunities",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("title", sa.Text(), nullable=False),
        sa.Column("underlying_pain", sa.Text(), nullable=False),
        sa.Column("job_to_be_done", sa.Text(), nullable=False),
        sa.Column("desired_outcome", sa.Text(), nullable=False),
        sa.Column("audience", sa.Text(), nullable=False),
        sa.Column("context", sa.Text(), nullable=False),
        sa.Column("description", sa.Text(), nullable=False),
        sa.Column("scope", sa.Text(), nullable=False),
        sa.Column("exclusions", sa.Text(), nullable=False),
        sa.Column("representative_cluster_id", sa.Integer(),
                  sa.ForeignKey("clusters.id"), nullable=False),
        sa.Column("embedding", Vector(1536)),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
    )
    op.create_table(
        "opportunity_clusters",
        sa.Column("opportunity_id", sa.Integer(),
                  sa.ForeignKey("opportunities.id"), primary_key=True),
        sa.Column("cluster_id", sa.Integer(), sa.ForeignKey("clusters.id"), primary_key=True),
        sa.Column("confidence", sa.Float(), nullable=False),
        sa.Column("reason", sa.Text(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.UniqueConstraint("cluster_id", name="uq_opportunity_clusters_cluster"),
    )
    op.create_table(
        "cluster_opportunity_profiles",
        sa.Column("cluster_id", sa.Integer(), sa.ForeignKey("clusters.id"), primary_key=True),
        sa.Column("underlying_pain", sa.Text(), nullable=False),
        sa.Column("job_to_be_done", sa.Text(), nullable=False),
        sa.Column("desired_outcome", sa.Text(), nullable=False),
        sa.Column("audience", sa.Text(), nullable=False),
        sa.Column("context", sa.Text(), nullable=False),
        sa.Column("embedding", Vector(1536)),
        sa.Column("model_name", sa.String(100), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
    )


def downgrade() -> None:
    op.drop_table("cluster_opportunity_profiles")
    op.drop_table("opportunity_clusters")
    op.drop_table("opportunities")
    op.drop_column("sources", "source_group_key")
