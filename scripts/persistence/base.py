class Repository:
    """Repositories share only the facade's connection and transaction manager."""

    def __init__(self, store):
        self._store = store
        self._connection = store._connection
        self._lock = store._lock
        self._transactions = store._transactions

    def transaction(self):
        return self._transactions.transaction()
