import threading


class GenerationCancelled(Exception):
    """Raised when an administrator cancels an active AI generation request."""


_active_generations = {}
_active_generations_lock = threading.Lock()


def register_generation(generation_id, owner_id):
    with _active_generations_lock:
        if generation_id in _active_generations:
            return None
        cancel_event = threading.Event()
        _active_generations[generation_id] = (str(owner_id), cancel_event)
        return cancel_event


def cancel_generation(generation_id, owner_id):
    with _active_generations_lock:
        generation = _active_generations.get(generation_id)
        if generation is None or generation[0] != str(owner_id):
            return False
        generation[1].set()
        return True


def unregister_generation(generation_id, cancel_event):
    with _active_generations_lock:
        generation = _active_generations.get(generation_id)
        if generation is not None and generation[1] is cancel_event:
            del _active_generations[generation_id]


def raise_if_cancelled(cancel_event):
    if cancel_event is not None and cancel_event.is_set():
        raise GenerationCancelled('AI 문제 생성이 취소되었습니다.')
