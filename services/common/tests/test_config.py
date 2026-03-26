from common.config import Settings, get_settings


def test_defaults_match_docs() -> None:
    settings = Settings()
    assert settings.aws_region == "us-east-1"
    assert settings.sonnet_model_id == "us.anthropic.claude-sonnet-4-6"
    assert settings.haiku_model_id == "us.anthropic.claude-haiku-4-5-20251001-v1:0"
    assert settings.nova_model_id == "amazon.nova-2-multimodal-embeddings-v1:0"
    assert settings.embed_dim in (384, 1024, 3072)


def test_derived_names_include_env_and_account() -> None:
    settings = Settings(env="dev")
    assert settings.table_name == "cwd-dev"
    assert settings.documents_bucket_name("111122223333") == "cwd-documents-dev-111122223333"
    assert settings.vector_bucket_name("111122223333") == "cwd-vectors-dev-111122223333"
    assert settings.vector_index_name("proj123") == "proj-proj123"


def test_get_settings_is_cached() -> None:
    assert get_settings() is get_settings()
