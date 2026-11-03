"""Configured concrete models available for direct reuse, never credentials."""
import json

from core.session_model import MODEL_SELECTED


def saved_models(database, connections, catalog: list[dict], *, user_id: str, project_id: str) -> list[dict]:
    database.ensure_ready()
    available = {row['id']: row for row in catalog if row.get('configured')}
    result = {}
    with database.read_transaction() as connection:
        rows = connection.execute('''
            SELECT e.data FROM event e JOIN session s ON s.id = e.aggregate_id
            WHERE e.type = ? AND s.user_id = ? AND s.project_id = ?
            ORDER BY e.time_created DESC, e.seq DESC
        ''', (MODEL_SELECTED, user_id, project_id)).fetchall()
    for row in rows:
        selected = json.loads(row['data'])
        key = f"{selected['connection_id']}:{selected['model']}"
        if key in available:
            result.setdefault(key, available[key])
    # Retain already saved model IDs, including profiles from before this menu.
    for key, profile in connections.connections().items():
        for model in profile.get('models', []):
            identifier = f'{key}:{model}'
            if identifier in available:
                result.setdefault(identifier, available[identifier])
    return list(result.values())
