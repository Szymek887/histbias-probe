"""initial schema: all tables from docs/03-data-model.md (human_rating comes after MVP)

Revision ID: 0001
Revises: 
Create Date: 2026-11-24 20:35:35.423085

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
import sqlmodel


# revision identifiers, used by Alembic.
revision: str = '0001'
down_revision: Union[str, None] = None
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table('country',
    sa.Column('code', sqlmodel.sql.sqltypes.AutoString(), nullable=False),
    sa.Column('name', sqlmodel.sql.sqltypes.AutoString(), nullable=False),
    sa.Column('native_language', sqlmodel.sql.sqltypes.AutoString(), nullable=False),
    sa.PrimaryKeyConstraint('code', name=op.f('pk_country'))
    )
    op.create_table('provider',
    sa.Column('id', sa.Integer(), nullable=False),
    sa.Column('name', sqlmodel.sql.sqltypes.AutoString(), nullable=False),
    sa.Column('kind', sqlmodel.sql.sqltypes.AutoString(), nullable=False),
    sa.Column('base_url', sqlmodel.sql.sqltypes.AutoString(), nullable=False),
    sa.Column('extra', sa.JSON(), nullable=False),
    sa.Column('origin', sqlmodel.sql.sqltypes.AutoString(), nullable=False),
    sa.Column('created_at', sqlmodel.sql.sqltypes.UTCDateTime(), nullable=False),
    sa.Column('updated_at', sqlmodel.sql.sqltypes.UTCDateTime(), nullable=False),
    sa.PrimaryKeyConstraint('id', name=op.f('pk_provider')),
    sa.UniqueConstraint('name', name=op.f('uq_provider_name'))
    )
    op.create_table('topic',
    sa.Column('id', sa.Integer(), nullable=False),
    sa.Column('slug', sqlmodel.sql.sqltypes.AutoString(), nullable=False),
    sa.Column('title', sqlmodel.sql.sqltypes.AutoString(), nullable=False),
    sa.Column('type', sqlmodel.sql.sqltypes.AutoString(), nullable=False),
    sa.Column('era', sqlmodel.sql.sqltypes.AutoString(), nullable=False),
    sa.Column('context', sa.Text(), nullable=True),
    sa.Column('origin', sqlmodel.sql.sqltypes.AutoString(), nullable=False),
    sa.Column('archived', sa.Boolean(), nullable=False),
    sa.Column('created_at', sqlmodel.sql.sqltypes.UTCDateTime(), nullable=False),
    sa.Column('updated_at', sqlmodel.sql.sqltypes.UTCDateTime(), nullable=False),
    sa.PrimaryKeyConstraint('id', name=op.f('pk_topic'))
    )
    with op.batch_alter_table('topic', schema=None) as batch_op:
        batch_op.create_index(batch_op.f('ix_topic_slug'), ['slug'], unique=True)

    op.create_table('credential',
    sa.Column('id', sa.Integer(), nullable=False),
    sa.Column('provider_id', sa.Integer(), nullable=False),
    sa.Column('secret_enc', sa.LargeBinary(), nullable=False),
    sa.Column('extra_enc', sa.LargeBinary(), nullable=True),
    sa.Column('hint', sqlmodel.sql.sqltypes.AutoString(), nullable=False),
    sa.Column('last_test_at', sqlmodel.sql.sqltypes.UTCDateTime(), nullable=True),
    sa.Column('last_test_ok', sa.Boolean(), nullable=True),
    sa.Column('last_test_message', sqlmodel.sql.sqltypes.AutoString(), nullable=True),
    sa.Column('created_at', sqlmodel.sql.sqltypes.UTCDateTime(), nullable=False),
    sa.Column('updated_at', sqlmodel.sql.sqltypes.UTCDateTime(), nullable=False),
    sa.ForeignKeyConstraint(['provider_id'], ['provider.id'], name=op.f('fk_credential_provider_id_provider'), ondelete='CASCADE'),
    sa.PrimaryKeyConstraint('id', name=op.f('pk_credential')),
    sa.UniqueConstraint('provider_id', name=op.f('uq_credential_provider_id'))
    )
    op.create_table('llm_model',
    sa.Column('id', sa.Integer(), nullable=False),
    sa.Column('provider_id', sa.Integer(), nullable=False),
    sa.Column('display_name', sqlmodel.sql.sqltypes.AutoString(), nullable=False),
    sa.Column('model_id', sqlmodel.sql.sqltypes.AutoString(), nullable=False),
    sa.Column('country_code', sqlmodel.sql.sqltypes.AutoString(), nullable=True),
    sa.Column('temperature', sa.Float(), nullable=True),
    sa.Column('max_tokens', sa.Integer(), nullable=True),
    sa.Column('system_prompt', sa.Text(), nullable=True),
    sa.Column('enabled', sa.Boolean(), nullable=False),
    sa.Column('notes', sa.Text(), nullable=True),
    sa.Column('created_at', sqlmodel.sql.sqltypes.UTCDateTime(), nullable=False),
    sa.Column('updated_at', sqlmodel.sql.sqltypes.UTCDateTime(), nullable=False),
    sa.ForeignKeyConstraint(['country_code'], ['country.code'], name=op.f('fk_llm_model_country_code_country')),
    sa.ForeignKeyConstraint(['provider_id'], ['provider.id'], name=op.f('fk_llm_model_provider_id_provider')),
    sa.PrimaryKeyConstraint('id', name=op.f('pk_llm_model'))
    )
    with op.batch_alter_table('llm_model', schema=None) as batch_op:
        batch_op.create_index(batch_op.f('ix_llm_model_provider_id'), ['provider_id'], unique=False)

    op.create_table('prompt',
    sa.Column('id', sa.Integer(), nullable=False),
    sa.Column('topic_id', sa.Integer(), nullable=False),
    sa.Column('language', sqlmodel.sql.sqltypes.AutoString(), nullable=False),
    sa.Column('text', sa.Text(), nullable=False),
    sa.Column('follow_up_text', sa.Text(), nullable=True),
    sa.Column('is_default', sa.Boolean(), nullable=False),
    sa.Column('created_at', sqlmodel.sql.sqltypes.UTCDateTime(), nullable=False),
    sa.Column('updated_at', sqlmodel.sql.sqltypes.UTCDateTime(), nullable=False),
    sa.ForeignKeyConstraint(['topic_id'], ['topic.id'], name=op.f('fk_prompt_topic_id_topic'), ondelete='CASCADE'),
    sa.PrimaryKeyConstraint('id', name=op.f('pk_prompt')),
    sa.UniqueConstraint('topic_id', 'language', name=op.f('uq_prompt_topic_id_language'))
    )
    with op.batch_alter_table('prompt', schema=None) as batch_op:
        batch_op.create_index(batch_op.f('ix_prompt_topic_id'), ['topic_id'], unique=False)

    op.create_table('topic_country',
    sa.Column('id', sa.Integer(), nullable=False),
    sa.Column('topic_id', sa.Integer(), nullable=False),
    sa.Column('country_code', sqlmodel.sql.sqltypes.AutoString(), nullable=False),
    sa.Column('position', sa.Integer(), nullable=False),
    sa.ForeignKeyConstraint(['country_code'], ['country.code'], name=op.f('fk_topic_country_country_code_country')),
    sa.ForeignKeyConstraint(['topic_id'], ['topic.id'], name=op.f('fk_topic_country_topic_id_topic'), ondelete='CASCADE'),
    sa.PrimaryKeyConstraint('id', name=op.f('pk_topic_country')),
    sa.UniqueConstraint('topic_id', 'country_code', name=op.f('uq_topic_country_topic_id_country_code')),
    sa.UniqueConstraint('topic_id', 'position', name=op.f('uq_topic_country_topic_id_position'))
    )
    with op.batch_alter_table('topic_country', schema=None) as batch_op:
        batch_op.create_index(batch_op.f('ix_topic_country_topic_id'), ['topic_id'], unique=False)

    op.create_table('topic_source',
    sa.Column('id', sa.Integer(), nullable=False),
    sa.Column('topic_id', sa.Integer(), nullable=False),
    sa.Column('label', sqlmodel.sql.sqltypes.AutoString(), nullable=False),
    sa.Column('url', sqlmodel.sql.sqltypes.AutoString(), nullable=False),
    sa.Column('position', sa.Integer(), nullable=False),
    sa.ForeignKeyConstraint(['topic_id'], ['topic.id'], name=op.f('fk_topic_source_topic_id_topic'), ondelete='CASCADE'),
    sa.PrimaryKeyConstraint('id', name=op.f('pk_topic_source'))
    )
    with op.batch_alter_table('topic_source', schema=None) as batch_op:
        batch_op.create_index(batch_op.f('ix_topic_source_topic_id'), ['topic_id'], unique=False)

    op.create_table('run',
    sa.Column('id', sa.Integer(), nullable=False),
    sa.Column('name', sqlmodel.sql.sqltypes.AutoString(), nullable=False),
    sa.Column('status', sqlmodel.sql.sqltypes.AutoString(), nullable=False),
    sa.Column('judge_model_id', sa.Integer(), nullable=True),
    sa.Column('config', sa.JSON(), nullable=False),
    sa.Column('started_at', sqlmodel.sql.sqltypes.UTCDateTime(), nullable=True),
    sa.Column('finished_at', sqlmodel.sql.sqltypes.UTCDateTime(), nullable=True),
    sa.Column('notes', sa.Text(), nullable=True),
    sa.Column('created_at', sqlmodel.sql.sqltypes.UTCDateTime(), nullable=False),
    sa.Column('updated_at', sqlmodel.sql.sqltypes.UTCDateTime(), nullable=False),
    sa.ForeignKeyConstraint(['judge_model_id'], ['llm_model.id'], name=op.f('fk_run_judge_model_id_llm_model')),
    sa.PrimaryKeyConstraint('id', name=op.f('pk_run'))
    )
    with op.batch_alter_table('run', schema=None) as batch_op:
        batch_op.create_index(batch_op.f('ix_run_status'), ['status'], unique=False)

    op.create_table('run_item',
    sa.Column('id', sa.Integer(), nullable=False),
    sa.Column('run_id', sa.Integer(), nullable=False),
    sa.Column('topic_id', sa.Integer(), nullable=False),
    sa.Column('mode', sqlmodel.sql.sqltypes.AutoString(), nullable=False),
    sa.Column('language', sqlmodel.sql.sqltypes.AutoString(), nullable=False),
    sa.Column('repetition', sa.Integer(), nullable=False),
    sa.Column('model_a_id', sa.Integer(), nullable=False),
    sa.Column('model_b_id', sa.Integer(), nullable=True),
    sa.Column('prompt_text', sa.Text(), nullable=False),
    sa.Column('follow_up_text', sa.Text(), nullable=True),
    sa.Column('status', sqlmodel.sql.sqltypes.AutoString(), nullable=False),
    sa.Column('error', sa.Text(), nullable=True),
    sa.Column('attempts', sa.Integer(), nullable=False),
    sa.Column('created_at', sqlmodel.sql.sqltypes.UTCDateTime(), nullable=False),
    sa.Column('updated_at', sqlmodel.sql.sqltypes.UTCDateTime(), nullable=False),
    sa.ForeignKeyConstraint(['model_a_id'], ['llm_model.id'], name=op.f('fk_run_item_model_a_id_llm_model')),
    sa.ForeignKeyConstraint(['model_b_id'], ['llm_model.id'], name=op.f('fk_run_item_model_b_id_llm_model')),
    sa.ForeignKeyConstraint(['run_id'], ['run.id'], name=op.f('fk_run_item_run_id_run'), ondelete='CASCADE'),
    sa.ForeignKeyConstraint(['topic_id'], ['topic.id'], name=op.f('fk_run_item_topic_id_topic')),
    sa.PrimaryKeyConstraint('id', name=op.f('pk_run_item'))
    )
    with op.batch_alter_table('run_item', schema=None) as batch_op:
        batch_op.create_index(batch_op.f('ix_run_item_run_id'), ['run_id'], unique=False)
        batch_op.create_index(batch_op.f('ix_run_item_status'), ['status'], unique=False)

    op.create_table('run_model',
    sa.Column('run_id', sa.Integer(), nullable=False),
    sa.Column('llm_model_id', sa.Integer(), nullable=False),
    sa.ForeignKeyConstraint(['llm_model_id'], ['llm_model.id'], name=op.f('fk_run_model_llm_model_id_llm_model')),
    sa.ForeignKeyConstraint(['run_id'], ['run.id'], name=op.f('fk_run_model_run_id_run'), ondelete='CASCADE'),
    sa.PrimaryKeyConstraint('run_id', 'llm_model_id', name=op.f('pk_run_model'))
    )
    op.create_table('run_topic',
    sa.Column('run_id', sa.Integer(), nullable=False),
    sa.Column('topic_id', sa.Integer(), nullable=False),
    sa.ForeignKeyConstraint(['run_id'], ['run.id'], name=op.f('fk_run_topic_run_id_run'), ondelete='CASCADE'),
    sa.ForeignKeyConstraint(['topic_id'], ['topic.id'], name=op.f('fk_run_topic_topic_id_topic')),
    sa.PrimaryKeyConstraint('run_id', 'topic_id', name=op.f('pk_run_topic'))
    )
    op.create_table('response',
    sa.Column('id', sa.Integer(), nullable=False),
    sa.Column('run_item_id', sa.Integer(), nullable=False),
    sa.Column('llm_model_id', sa.Integer(), nullable=False),
    sa.Column('model_id_snapshot', sqlmodel.sql.sqltypes.AutoString(), nullable=False),
    sa.Column('display_name_snapshot', sqlmodel.sql.sqltypes.AutoString(), nullable=False),
    sa.Column('turn', sa.Integer(), nullable=False),
    sa.Column('messages', sa.JSON(), nullable=False),
    sa.Column('text', sa.Text(), nullable=False),
    sa.Column('finish_reason', sqlmodel.sql.sqltypes.AutoString(), nullable=True),
    sa.Column('refusal_detected', sa.Boolean(), nullable=False),
    sa.Column('error', sa.Text(), nullable=True),
    sa.Column('input_tokens', sa.Integer(), nullable=True),
    sa.Column('output_tokens', sa.Integer(), nullable=True),
    sa.Column('latency_ms', sa.Integer(), nullable=False),
    sa.Column('raw', sa.JSON(), nullable=False),
    sa.Column('created_at', sqlmodel.sql.sqltypes.UTCDateTime(), nullable=False),
    sa.ForeignKeyConstraint(['llm_model_id'], ['llm_model.id'], name=op.f('fk_response_llm_model_id_llm_model')),
    sa.ForeignKeyConstraint(['run_item_id'], ['run_item.id'], name=op.f('fk_response_run_item_id_run_item'), ondelete='CASCADE'),
    sa.PrimaryKeyConstraint('id', name=op.f('pk_response'))
    )
    with op.batch_alter_table('response', schema=None) as batch_op:
        batch_op.create_index(batch_op.f('ix_response_run_item_id'), ['run_item_id'], unique=False)

    op.create_table('judgement',
    sa.Column('id', sa.Integer(), nullable=False),
    sa.Column('response_id', sa.Integer(), nullable=False),
    sa.Column('judge_model_id', sa.Integer(), nullable=False),
    sa.Column('rubric_version', sqlmodel.sql.sqltypes.AutoString(), nullable=False),
    sa.Column('overall_score', sa.Integer(), nullable=False),
    sa.Column('censorship', sqlmodel.sql.sqltypes.AutoString(), nullable=False),
    sa.Column('refusal', sa.Boolean(), nullable=False),
    sa.Column('balance_score', sa.Integer(), nullable=False),
    sa.Column('depth_score', sa.Integer(), nullable=False),
    sa.Column('factual_issues', sa.JSON(), nullable=False),
    sa.Column('omissions', sa.JSON(), nullable=False),
    sa.Column('narrative_alignment', sqlmodel.sql.sqltypes.AutoString(), nullable=True),
    sa.Column('rationale', sa.Text(), nullable=False),
    sa.Column('raw_output', sa.Text(), nullable=False),
    sa.Column('judge_prompt', sa.Text(), nullable=False),
    sa.Column('created_at', sqlmodel.sql.sqltypes.UTCDateTime(), nullable=False),
    sa.ForeignKeyConstraint(['judge_model_id'], ['llm_model.id'], name=op.f('fk_judgement_judge_model_id_llm_model')),
    sa.ForeignKeyConstraint(['response_id'], ['response.id'], name=op.f('fk_judgement_response_id_response'), ondelete='CASCADE'),
    sa.PrimaryKeyConstraint('id', name=op.f('pk_judgement')),
    sa.UniqueConstraint('response_id', 'judge_model_id', 'rubric_version', name=op.f('uq_judgement_response_id_judge_model_id_rubric_version'))
    )
    with op.batch_alter_table('judgement', schema=None) as batch_op:
        batch_op.create_index(batch_op.f('ix_judgement_response_id'), ['response_id'], unique=False)



def downgrade() -> None:
    with op.batch_alter_table('judgement', schema=None) as batch_op:
        batch_op.drop_index(batch_op.f('ix_judgement_response_id'))

    op.drop_table('judgement')
    with op.batch_alter_table('response', schema=None) as batch_op:
        batch_op.drop_index(batch_op.f('ix_response_run_item_id'))

    op.drop_table('response')
    op.drop_table('run_topic')
    op.drop_table('run_model')
    with op.batch_alter_table('run_item', schema=None) as batch_op:
        batch_op.drop_index(batch_op.f('ix_run_item_status'))
        batch_op.drop_index(batch_op.f('ix_run_item_run_id'))

    op.drop_table('run_item')
    with op.batch_alter_table('run', schema=None) as batch_op:
        batch_op.drop_index(batch_op.f('ix_run_status'))

    op.drop_table('run')
    with op.batch_alter_table('topic_source', schema=None) as batch_op:
        batch_op.drop_index(batch_op.f('ix_topic_source_topic_id'))

    op.drop_table('topic_source')
    with op.batch_alter_table('topic_country', schema=None) as batch_op:
        batch_op.drop_index(batch_op.f('ix_topic_country_topic_id'))

    op.drop_table('topic_country')
    with op.batch_alter_table('prompt', schema=None) as batch_op:
        batch_op.drop_index(batch_op.f('ix_prompt_topic_id'))

    op.drop_table('prompt')
    with op.batch_alter_table('llm_model', schema=None) as batch_op:
        batch_op.drop_index(batch_op.f('ix_llm_model_provider_id'))

    op.drop_table('llm_model')
    op.drop_table('credential')
    with op.batch_alter_table('topic', schema=None) as batch_op:
        batch_op.drop_index(batch_op.f('ix_topic_slug'))

    op.drop_table('topic')
    op.drop_table('provider')
    op.drop_table('country')
