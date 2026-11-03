"""A small provider menu over the full compatible-model catalog."""

# Regional endpoints share a brand; prefer an existing selection/configuration.
FEATURED_GROUPS = (
    ('OpenAI', ('openai',)),
    ('DeepSeek', ('deepseek',)),
    ('Qwen', ('alibaba-cn', 'alibaba')),
    ('Kimi', ('moonshotai-cn', 'moonshotai')),
    ('GLM', ('zhipuai', 'zai')),
    ('Doubao', ('volcengine',)),
    ('StepFun', ('stepfun', 'stepfun-ai')),
    ('MiMo', ('xiaomi',)),
)
PROVIDER_NAMES = {key: brand for brand, keys in FEATURED_GROUPS for key in keys}
PROVIDER_ORDER = {key: rank for rank, (_, keys) in enumerate(FEATURED_GROUPS) for key in keys}


def visible_provider_ids(groups: dict[str, list[dict]], current_id: str) -> set[str]:
    visible = {key for key, rows in groups.items() if key == current_id or any(row.get('configured') for row in rows)}
    for _, keys in FEATURED_GROUPS:
        available = [key for key in keys if key in groups]
        if not available or any(key in visible for key in available):
            continue
        visible.add(available[0])
    return visible
