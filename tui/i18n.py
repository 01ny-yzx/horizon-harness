"""UI-only translations and local language preference (never model prompts)."""
from __future__ import annotations

import json
import os
from pathlib import Path
import tempfile

LANGUAGES = (('en', 'English'), ('zh', '中文'), ('ja', '日本語'))

# Chinese source labels also serve as stable internal activity identifiers.
_TEXT = {
    '打开': ('Open', '開く'),
    '删除所选会话': ('Delete selected session', '選択した会話を削除'),
    '删除这个会话？': ('Delete this session?', 'この会話を削除しますか？'),
    '会话和聊天记录将永久删除，无法恢复。': ('The session and its messages will be permanently deleted.', '会話とチャット履歴は完全に削除され、復元できません。'),
    '删除': ('Delete', '削除'),
    '←→ 选择  Enter 确认  Esc 取消': ('←→ Select  Enter Confirm  Esc Cancel', '←→ 選択  Enter 確定  Esc キャンセル'),
    '↑↓ 选择  Enter 打开  Delete 删除  Esc 返回': ('↑↓ Select  Enter Open  Delete Remove  Esc Back', '↑↓ 選択  Enter 開く  Delete 削除  Esc 戻る'),
    '请先中断该会话的任务，再删除会话': ('Interrupt this session before deleting it', '実行中のタスクを中断してから会話を削除してください'),
    '无法删除会话，请重试': ('Could not delete the session; please try again', '会話を削除できませんでした。再試行してください'),
    '会话已删除': ('Session deleted', '会話を削除しました'),
    '默认模型已更新，新会话生效': ('Default model updated for new sessions', '既定のモデルを更新しました。新しい会話に適用されます'),
    '已复制': ('Copied', 'コピーしました'),
    '复制失败，请重试': ('Could not copy; please try again', 'コピーできませんでした。再試行してください'),
    '空输入时按 {key} 可插队，再按 Enter 确认': ('With empty input, {key} steers; Enter confirms', '空欄で {key} を押して割り込み、Enter で確定'),
    '快捷键设置': ('Keyboard shortcuts', 'ショートカット設定'),
    '发送 / 排队': ('Send / queue', '送信 / 待機'),
    '取回排队消息': ('Recall queued message', '待機メッセージを入力に戻す'),
    '恢复暂存草稿': ('Restore held draft', '保留した下書きを復元'),
    '中断任务': ('Interrupt task', 'タスクを中断'),
    '上一个标签页': ('Previous tab', '前のタブ'),
    '下一个标签页': ('Next tab', '次のタブ'),
    '关闭标签页': ('Close tab', 'タブを閉じる'),
    '切换权限': ('Switch access mode', 'アクセスモードを切り替え'),
    '请按下新的组合键，Esc 取消': ('Press a new shortcut. Esc to cancel', '新しいキーの組み合わせを押してください。Esc でキャンセル'),
    '此快捷键已用于：{action}': ('Already used by: {action}', '使用中: {action}'),
    '此按键用于编辑、系统操作或无法识别，请换一个组合键': ('Reserved or unsupported key. Try another shortcut', '編集・システム用、または未対応のキーです。別の組み合わせをお試しください'),
    '无法保存快捷键，原设置未改变': ('Could not save shortcuts; previous settings kept', '保存できませんでした。元の設定を維持します'),
    '↑↓ 选择  Enter 修改  Esc 返回': ('↑↓ Select  Enter Change  Esc Back', '↑↓ 選択  Enter 変更  Esc 戻る'),
    '恢复默认快捷键': ('Restore default shortcuts', '既定のショートカットに戻す'),
    'Shift+Enter 换行 · F1 帮助 · Ctrl+Q 退出': ('Shift+Enter Newline · F1 Help · Ctrl+Q Quit', 'Shift+Enter 改行 · F1 ヘルプ · Ctrl+Q 終了'),

    '引导': ('Steer', '割り込み'), '取回': ('Recall', '入力に戻す'), '移除': ('Remove', '削除'),
    '将这条消息插队？': ('Steer with this message?', 'このメッセージで割り込みますか？'),
    '作为补充要求，优先交给当前任务处理。': ('Deliver as guidance at the next checkpoint.', '次のチェックポイントで現在のタスクへの追加指示として渡します。'),
    'Enter 确认插队': ('Enter to confirm', 'Enter で確定'),
    'Esc 继续排队': ('Esc to keep queued', 'Esc で待機を継続'),
    '排队失败，消息仍在输入框中': ('Could not queue; your message is still in the input', '待機に追加できませんでした。メッセージは入力欄に残っています'),
    '消息状态已变化，请重试': ('Message state changed; please try again', 'メッセージの状態が変わりました。再試行してください'),
    '会话': ('Sessions', '履歴'), '新建': ('New', '新規'), '返回': ('Back', '戻る'),
    '把想法留在这里。一起让它发生。': ('Leave an idea here. Let’s make it happen.', 'アイデアをここに。一緒に形にしよう。'),
    'Ask anything, or start a task…': ('Ask anything, or start a task…', '質問するか、タスクを始めましょう…'),
    '模型加载中 · / 命令': ('Loading model · / commands', 'モデルを読み込み中 · / コマンド'),
    '未选择模型': ('No model selected', 'モデル未選択'), ' 命令': (' commands', ' コマンド'),
    '切换模型': ('Switch model', 'モデルを切り替え'), '历史会话': ('Session history', '会話履歴'),
    '新建会话': ('New session', '新しい会話'), '命令与快捷键': ('Commands & shortcuts', 'コマンドとショートカット'),
    '界面语言': ('Interface language', '表示言語'), '选择语言': ('Language', '言語を選択'),
    '语言已更新': ('Language updated', '言語を変更しました'),
    '无法保存语言设置': ('Could not save language preference', '言語設定を保存できませんでした'),
    '无法保存标签布局': ('Could not save tab layout', 'タブ配置を保存できませんでした'),
    '无法读取标签布局': ('Could not load tab layout', 'タブ配置を読み込めませんでした'),
    '请先按 Esc 中断当前任务': ('Press Esc to interrupt the current task first', '先に Esc で現在のタスクを中断してください'),
    '正在连接': ('Connecting', '接続中'), '正在思考': ('Thinking', '考え中'),
    '正在压缩上下文': ('Compacting context', 'コンテキストを圧縮中'),
    '正在回答': ('Responding', '回答中'), '正在执行': ('Working', '実行中'),
    '正在执行 · ': ('Running · ', '実行中 · '), '工具': ('Tool', 'ツール'),
    '正在处理结果': ('Processing results', '結果を処理中'),
    '执行失败': ('Execution failed', '実行に失敗しました'), '已完成': ('Completed', '完了'),
    '已中断': ('Interrupted', '中断しました'), '已同步会话': ('Session synced', '会話を同期しました'),
    '正在中断': ('Interrupting', '中断中'),
    '中断请求未完成，任务可能仍在执行': ('Interruption not confirmed; the task may still be running', '中断を確認できません。タスクが続行中の可能性があります'),
    '正在打开会话': ('Opening session', '会話を開いています'), '无法打开会话': ('Could not open session', '会話を開けませんでした'),
    '无法读取历史会话': ('Could not load session history', '会話履歴を読み込めませんでした'),
    '无法读取模型列表，请检查本地服务': ('Could not load models; check the local service', 'モデルを読み込めません。ローカルサービスを確認してください'),
    '已配置': ('Configured', '設定済み'), '＋ 自定义服务': ('＋ Custom provider', '＋ カスタムサービス'),
    '选择模型': ('Select model', 'モデルを選択'), '选择模型服务': ('Select provider', 'サービスを選択'),
    '无法打开模型配置': ('Could not open model settings', 'モデル設定を開けませんでした'),
    '正在切换模型': ('Switching model', 'モデルを切り替え中'), '已切换为 ': ('Switched to ', '切り替えました: '),
    '切换模型失败，请检查本地服务': ('Could not switch model; check the local service', 'モデルを切り替えられません。ローカルサービスを確認してください'),
    '消息已发送，正在同步会话': ('Message sent; syncing session', '送信済み。会話を同期中'),
    '发送失败，请检查本地服务': ('Could not send; check the local service', '送信できません。ローカルサービスを確認してください'),
    '连接已断开，正在重试': ('Disconnected; retrying', '接続が切れました。再試行中'),
    '正在重新连接': ('Reconnecting', '再接続中'),
    '…（完整结果保存在会话记录中）': ('… (full result saved in session history)', '…（完全な結果は会話履歴に保存されています）'),
    '本轮模型调用失败': ('Model request failed', 'モデルへのリクエストに失敗しました'),
    '刚刚': ('Just now', 'たった今'), '{n} 分钟前': ('{n}m ago', '{n}分前'),
    '{n} 小时前': ('{n}h ago', '{n}時間前'), '{n} 天前': ('{n}d ago', '{n}日前'),
    '未命名会话': ('Untitled session', '無題の会話'),
    '↑↓ 选择  Enter 确认  Esc 返回': ('↑↓ Select  Enter Confirm  Esc Back', '↑↓ 選択  Enter 決定  Esc 戻る'),
    '  Ctrl+E 配置': ('  Ctrl+E Configure', '  Ctrl+E 設定'),
    '没有匹配项': ('No items', '項目がありません'), '配置自定义服务': ('Custom provider settings', 'カスタムサービス設定'),
    '模型服务': ('Model provider', 'モデルサービス'), '服务标识': ('Provider ID', 'サービス ID'),
    '服务名称': ('Provider name', 'サービス名'), 'OpenAI 兼容 Base URL': ('OpenAI-compatible Base URL', 'OpenAI 互換 Base URL'),
    'API Key（留空保留已保存的密钥）': ('API Key (blank keeps the saved key)', 'API Key（空欄で保存済みのキーを使用）'),
    '模型 ID': ('Model ID', 'モデル ID'),
    '上下文 Token 上限（目录已知模型可留空）': ('Context token limit (optional for catalog models)', 'コンテキスト上限（登録モデルは省略可）'),
    '例如 128000': ('e.g. 128000', '例: 128000'), '保存并使用': ('Save & use', '保存して使用'),
    '取消': ('Cancel', 'キャンセル'), 'Enter 保存并使用  Esc 返回': ('Enter Save & use  Esc Back', 'Enter 保存して使用  Esc 戻る'),
    '请填写 API Key': ('Enter an API Key', 'API Key を入力してください'),
    '请填写服务标识、接口地址和模型 ID': ('Enter a provider ID, endpoint, and model ID', 'サービス ID、URL、モデル ID を入力してください'),
    '上下文上限应为整数': ('Context limit must be an integer', 'コンテキスト上限は整数で入力してください'),
    '保存失败，请检查本地服务': ('Could not save; check the local service', '保存できません。ローカルサービスを確認してください'),
    '服务未能完成请求，请检查模型配置或本地服务': ('Request failed; check the model settings or local service', 'リクエストに失敗しました。モデル設定やローカルサービスを確認してください'),
    '服务请求失败（HTTP {status}）': ('Service request failed (HTTP {status})', 'サービスのリクエストに失敗しました（HTTP {status}）'),
    '事件连接失败（HTTP {status}）': ('Event connection failed (HTTP {status})', 'イベント接続に失敗しました（HTTP {status}）'),
    '正在启动 Horizon…': ('Starting Horizon…', 'Horizon を起動中…'),
    'Horizon 启动失败：': ('Horizon failed to start: ', 'Horizon の起動に失敗しました: '),
    '本地服务启动失败（退出码 {code}）': ('Local service exited with code {code}', 'ローカルサービスが終了しました（コード {code}）'),
    '本地服务启动超时': ('Local service startup timed out', 'ローカルサービスの起動がタイムアウトしました'),
    'Press Enter to start ↵': ('Press Enter to start ↵', 'Enter で開始 ↵'),
}
_TEXT.update({
    '模型加载中': ('Loading model', 'モデルを読み込み中'),
    '版本更新': ('Updates', 'アップデート'),
    '有更新': ('Update available', '更新あり'),
    '正在检查更新…': ('Checking for updates…', '更新を確認中…'),
    '已是最新版本': ('You are up to date', '最新バージョンです'),
    '更新并重启': ('Update & restart', '更新して再起動'),
    '跳过此版本': ('Skip this version', 'このバージョンをスキップ'),
    '重试': ('Retry', '再試行'),
    '无法保存更新设置': ('Could not save update preference', '更新設定を保存できませんでした'),
    '无法检查更新，请检查网络后重试': ('Could not check for updates. Check your network and retry', '更新を確認できません。接続を確認して再試行してください'),
    '当前从源码运行，请通过 Git 更新源码': ('Running from source. Update your checkout with Git', 'ソースから実行中です。Git で更新してください'),
    '无法识别安装方式，请通过原安装渠道更新': ('Installation method unknown. Update through your original installation channel', 'インストール方法を判別できません。元の方法で更新してください'),
    '确认后退出界面并关闭本地服务，在终端显示更新进度；成功后自动重启': ('Closes the interface and local service, shows update progress in the terminal, then restarts on success', '画面とローカルサービスを終了し、ターミナルに更新状況を表示します。完了後に再起動します'),
})
_TEXT.update({
    '搜索配置损坏，请重新填写 Key': ('Search settings are damaged; enter a new Key', '検索設定が破損しています。Key を入力し直してください'),
    '联网搜索': ('Web search', 'ウェブ検索'),
    '搜索 Key 与模型 Key 相互独立': ('Search and model API keys are separate', '検索とモデルの API Key は別です'),
    '请填写搜索 API Key': ('Enter a search API Key', '検索用 API Key を入力してください'),
    '搜索配置已保存': ('Search settings saved', '検索設定を保存しました'),
    '已保存，请检查搜索组件是否可用': ('Saved; check the search dependency', '保存済み。検索コンポーネントを確認してください'),
    '无法保存搜索配置': ('Could not save search settings', '検索設定を保存できませんでした'),
    '无法读取搜索配置': ('Could not load search settings', '検索設定を読み込めませんでした'),
    '搜索 Key 格式无效': ('Invalid search Key format', '検索 Key の形式が無効です'),

    '无法保存权限设置': ('Could not save access mode', 'アクセスモードを保存できませんでした'),
    '＋ 添加模型': ('＋ Add model', '＋ モデルを追加'),
    '已保存的模型': ('Saved models', '保存済みモデル'),
    '空白会话': ('Empty session', '空の会話'),
    '运行器执行出错，请检查本地配置与日志': ('Execution failed; check local settings and logs', '実行に失敗しました。ローカル設定とログを確認してください'),
    '模型调用失败，请检查模型配置、连接和服务状态': ('Model request failed; check settings, connection, and service status', 'モデル呼び出しに失敗しました。設定、接続、サービスの状態を確認してください'),
    '无法加载本地记忆或项目指令，请检查本地数据与文件权限': ('Could not load memory or project instructions; check local data and permissions', 'メモリまたはプロジェクト指示を読み込めません。データと権限を確認してください'),
    '连接已启动的 Horizon 服务；退出时不关闭该服务': ('Attach to a running Horizon service; leave it running on exit', '起動中の Horizon に接続（終了時にサービスは停止しません）'),
    '用户 ID，默认使用服务配置': ('User ID (service default if omitted)', 'ユーザー ID（省略時はサービスの既定値）'),
    '项目 ID，默认使用服务配置': ('Project ID (service default if omitted)', 'プロジェクト ID（省略時はサービスの既定値）'),
    '已有服务的 API Key 请求头': ('API key header for an existing service', '既存サービスの API Key ヘッダー'),
    '请先配置这个服务的 API Key': ('Configure this provider’s API Key first', '先にこのサービスの API Key を設定してください'),
    '模型服务或模型名称无效': ('Invalid provider or model name', 'サービスまたはモデル名が無効です'),
    '模型名称无效': ('Invalid model name', 'モデル名が無効です'),
    '未知模型服务': ('Unknown model provider', '不明なモデルサービス'),
    '无法确定模型上下文上限，请在服务配置中填写上下文 Token 上限': ('Unknown model context limit; set it in custom provider settings', 'コンテキスト上限が不明です。カスタムサービス設定で指定してください'),
    '服务标识须以字母或数字开头，可包含点、下划线和短横线': ('Provider ID must start with a letter or digit; dots, underscores, and hyphens are allowed', 'サービス ID は英数字で始めてください。ドット、下線、ハイフンも使用できます'),
    '请填写有效的 OpenAI 兼容接口地址，地址不能包含密钥或查询参数': ('Enter a valid OpenAI-compatible URL without credentials or query parameters', '認証情報やクエリを含まない有効な OpenAI 互換 URL を入力してください'),
    '上下文 Token 上限须在 1024 到 100000000 之间': ('Context token limit must be between 1024 and 100000000', 'コンテキスト上限は 1024〜100000000 にしてください'),
})


def translate(source: str, language: str = 'en', **values) -> str:
    if source == 'Horizon terminal interface (starts a local service automatically)':
        result = {'en': source, 'zh': 'Horizon 终端交互界面（自动启动本地服务）', 'ja': 'Horizon ターミナル（ローカルサービスを自動起動）'}.get(language, source)
    elif source == 'Ask anything, or start a task…' and language == 'zh':
        result = '提出问题，或开始一项任务…'
    elif source == 'Press Enter to start ↵' and language == 'zh':
        result = '按 Enter 开始 ↵'
    elif language == 'zh':
        result = source
    else:
        index = 1 if language == 'ja' else 0
        result = _TEXT.get(source, (source, source))[index]
        if source not in _TEXT:
            for prefix in ('正在执行 · ', '已切换为 ', '执行失败', 'Horizon 启动失败：'):
                if source.startswith(prefix):
                    result = _TEXT[prefix][index] + source[len(prefix):]
                    break
    return result.format(**values) if values else result


def help_text(language: str, shortcuts=None) -> str:
    tr = lambda text: translate(text, language)
    rows = [(' /models', '切换模型'), (' /session', '历史会话'), (' /new', '新建会话'),
            (' /language', '界面语言'), (' /search', '联网搜索'), (' /update', '版本更新'), (' /help', '命令与快捷键')]
    from tui.keybindings import KeyBindings, LABELS, display_key
    mapping = shortcuts or KeyBindings()
    lines = [f'{display_key(mapping.key(action)):<20}{tr(label)}' for action, label in LABELS.items()]
    lines.append(translate('空输入时按 {key} 可插队，再按 Enter 确认', language, key=display_key(mapping.key('send'))))
    lines.append(tr('Shift+Enter 换行 · F1 帮助 · Ctrl+Q 退出'))
    return 'Horizon\n\n' + '\n'.join(f'{command:<15}{tr(description)}' for command, description in rows) + '\n\n' + '\n'.join(lines)



class Preferences:
    def __init__(self, path: Path | None = None):
        if path is None:
            from core.path_grounding import build_path_context
            path = build_path_context().user_data_root / 'tui' / 'preferences.json'
        self.path = Path(path)
        self.language = 'en'
        self.access_mode = None
        try:
            data = json.loads(self.path.read_text(encoding="utf-8"))
            if isinstance(data.get('access_mode'), str) and data['access_mode'] in {'read_only', 'full_access'}:
                self.access_mode = data['access_mode']
            value = data.get('language')
            if isinstance(value, str) and value in dict(LANGUAGES):
                self.language = value
        except (OSError, ValueError, AttributeError):
            pass

    def save(self, language: str):
        self._write(language, self.access_mode)

    def save_access_mode(self, mode: str):
        if mode not in {'read_only', 'full_access'}:
            raise ValueError('Unsupported access mode')
        self._write(self.language, mode)

    def _write(self, language: str, mode: str | None):
        if language not in dict(LANGUAGES):
            raise ValueError('Unsupported language')
        self.path.parent.mkdir(parents=True, exist_ok=True)
        fd, temp = tempfile.mkstemp(dir=self.path.parent, prefix='.preferences-')
        try:
            with os.fdopen(fd, 'w', encoding='utf-8') as output:
                json.dump({'language': language, **({'access_mode': mode} if mode else {})}, output)
                output.flush()
                os.fsync(output.fileno())
            os.replace(temp, self.path)
        finally:
            if os.path.exists(temp):
                os.unlink(temp)
        self.language = language
        self.access_mode = mode
