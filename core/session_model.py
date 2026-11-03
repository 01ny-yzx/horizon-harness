"""Durable per-session model choice. Events contain identifiers, never secrets."""
from __future__ import annotations

import json

from core.model_connections import ModelConnections

MODEL_SELECTED = 'session.model.selected'


class SessionModelSelection:
    def __init__(self, database, connections: ModelConnections | None = None):
        self.database = database
        self.connections = connections or ModelConnections()

    def get(self, session_id: str) -> dict:
        with self.database.read_transaction() as connection:
            row = connection.execute('SELECT type, data FROM event WHERE aggregate_id = ? AND type IN (?, ?) ORDER BY seq DESC LIMIT 1',
                                     (session_id, MODEL_SELECTED, 'SessionCreated')).fetchone()
        if row:
            data = json.loads(row['data'])
            choice = data if row['type'] == MODEL_SELECTED else data.get('model_selection')
            if choice is not None:
                return {'connection_id': choice['connection_id'], 'model': choice['model']}
        return {'connection_id': self.connections.default_id, 'model': self.connections.settings.llm_model}

    def select(self, session_id: str, connection_id: str, model: str) -> dict:
        from core.session_event import SessionEventPublisher
        self.connections.validate_choice(connection_id, model)
        SessionEventPublisher(database=self.database).publish(
            aggregate_id=session_id, event_type=MODEL_SELECTED,
            data={'session_id': session_id, 'connection_id': connection_id, 'model': model})
        return self.get(session_id)


def project_model_selected(connection, event) -> None:
    data = event.data
    if data.get('session_id') != event.aggregate_id or not data.get('connection_id') or not data.get('model'):
        raise ValueError('Invalid session model selection')
    cursor = connection.execute('UPDATE session SET time_updated = ? WHERE id = ?', (event.time_created, event.aggregate_id))
    if cursor.rowcount != 1:
        raise ValueError('Session does not exist')
