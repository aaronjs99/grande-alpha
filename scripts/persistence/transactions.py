"""One serialized connection with nested savepoints and commit callbacks."""

from contextlib import contextmanager


class Transactions:
    def __init__(self, connection, lock):
        self.connection = connection
        self.lock = lock
        self._depth = 0
        self._callbacks = []

    @contextmanager
    def transaction(self):
        callbacks = []
        with self.lock:
            depth = self._depth
            mark = len(self._callbacks)
            savepoint = f"repository_{depth}"
            self.connection.execute("BEGIN IMMEDIATE" if depth == 0 else f"SAVEPOINT {savepoint}")
            self._depth += 1
            try:
                yield self.connection
                self.connection.execute("COMMIT" if depth == 0 else f"RELEASE SAVEPOINT {savepoint}")
            except BaseException:
                if depth == 0:
                    self.connection.rollback()
                else:
                    self.connection.execute(f"ROLLBACK TO SAVEPOINT {savepoint}")
                    self.connection.execute(f"RELEASE SAVEPOINT {savepoint}")
                del self._callbacks[mark:]
                raise
            finally:
                self._depth -= 1
            if depth == 0:
                callbacks, self._callbacks = self._callbacks, []
        for callback in callbacks:
            callback()

    def after_commit(self, callback):
        with self.lock:
            if self._depth:
                self._callbacks.append(callback)
                return
        callback()
