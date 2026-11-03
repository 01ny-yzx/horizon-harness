"""Hide known runtime diagnostics while retaining a single concise outcome."""

# Exact runtime-generated notices only: ordinary user/model text is never matched.
_DIAGNOSTICS = frozenset({
    '模型服务连续调用失败，任务未执行。',
    '模型服务调用失败，任务未执行。',
    '模型未能返回可执行的结构化结果，任务未执行。',
    '模型服务当前请求受限，请稍后重新执行任务。',
    '模型服务调用超时，请稍后重新执行任务。',
    '无法连接模型服务，请稍后重新执行任务。',
    '模型服务暂时不可用，请稍后重新执行任务。',
    '模型服务认证失败，请检查 Provider 配置。',
    '模型服务配置不可用，任务未执行。',
    '模型服务连续调用超时，任务未执行。',
    '无法连接模型服务，任务未执行。',
    '模型服务请求受限，任务未执行。',
    '当前工具面不可用，任务未执行。',
    '当前模型服务不支持所需能力，任务未执行。',
})


def presentation_messages(messages: list[dict]) -> list[dict]:
    result: list[dict] = []
    turn: list[dict] = []

    def flush() -> None:
        hidden = False
        visible = []
        for message in turn:
            if message.get('type') == 'synthetic' and message.get('text') in _DIAGNOSTICS:
                hidden = True
            else:
                visible.append(message)
        has_outcome = any(m.get('type') == 'run_status' for m in visible)
        last_assistant = next((m for m in reversed(visible) if m.get('type') == 'assistant'), {})
        if not has_outcome and (hidden or last_assistant.get('error')):
            visible.append({'type': 'run_status', 'status': 'failed'})
        result.extend(visible)

    for message in messages:
        if message.get('type') == 'user' and turn:
            flush()
            turn = []
        turn.append(message)
    if turn:
        flush()
    return result
