"""Safe durable execution outcomes for the presentation transcript."""
RUN_FINISHED = 'session.run.finished'


def project_run_finished(connection, event):
    if event.data.get('session_id') != event.aggregate_id or event.data.get('status') not in {'completed', 'failed', 'interrupted'}:
        raise ValueError('Invalid session run outcome')
    cursor = connection.execute('UPDATE session SET time_updated = ? WHERE id = ?', (event.time_created, event.aggregate_id))
    if cursor.rowcount != 1:
        raise ValueError('Session does not exist')
